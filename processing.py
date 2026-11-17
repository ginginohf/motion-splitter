"""Headless task orchestration. No Tk imports and no widget access."""
from pathlib import Path
from job_contracts import TaskContext, JobResult, finish
from external_tools import ProcessRunner, find_ffmpeg, find_realesrgan, check_tools, check_cancel, TaskCancelled
from output_safety import create_run_directory, source_output_directory
from video_analysis import parse_resolution, get_video_duration, calculate_auto_sensitivity, merge_close_segments, analyze_video
from video_export import VIDEO_EXTS, save_segments, save_full_video
from video_enhance import enhance_video_with_realesrgan, enhance_video_fallback_frames

def record_task_error(runner, stage, error):
    message = f"Ошибка: {error}"
    if runner is not None and not hasattr(error, "log_path"):
        try:
            log = runner.note(stage, message)
            message += f"\nПолный журнал: {log}"
        except OSError as log_error:
            message += f"\nНе удалось сохранить журнал: {log_error}"
    return message


def run_split(context: TaskContext, files, sensitivity, min_pause, min_motion, analysis_res, skip, save_full, auto_sens, auto_scale, merge_gap,
              enhance_after_split, realesrgan_exe, model, scale, tile, gpuid,
              smoothing_sec=0.2, pre_motion_sec=0, post_motion_sec=0) -> JobResult:
    runner = None
    run_dir = None
    done = 0
    try:
        check_cancel(context.stop_event)
        run_dir = create_run_directory(Path(files[0]).resolve().parent)
        runner = ProcessRunner(run_dir / "logs", context.stop_event)
        context.ui_queue.put(("info", f"Папка результатов: {run_dir}"))
        context.ui_queue.put(("info", f"Журналы диагностики: {runner.log_directory}"))
        ffmpeg_path = find_ffmpeg()
        if enhance_after_split:
            realesrgan_exe = find_realesrgan(realesrgan_exe)
        check_tools(runner, ffmpeg_path, realesrgan_exe if enhance_after_split else None)
        total = len(files); done = 0
        context.ui_queue.put(("total_init", total))

        for path in files:
            if context.stop_event.is_set():
                context.ui_queue.put(("info", "Обработка прервана"))
                break
            context.ui_queue.put(("file_name", Path(path).name))

            def file_progress(cur, total_frames):
                if context.stop_event.is_set(): return
                context.ui_queue.put(("file_progress", (cur, total_frames)))

            def preview_callback(frame, frame_idx, motion_score):
                context.ui_queue.put(("preview_frame", (frame, frame_idx, motion_score)))

            if auto_sens and not context.stop_event.is_set():
                context.ui_queue.put(("info", f"Автонастройка чувствительности для {Path(path).name}"))
                context.ui_queue.put(("phase", f"Автонастройка порога: {Path(path).name}"))
                auto_sensitivity = calculate_auto_sensitivity(
                    path, analysis_res, skip, progress_cb=file_progress, stop_event=context.stop_event,
                    smoothing_sec=smoothing_sec,
                    diagnostics_cb=lambda m: context.ui_queue.put(("info", m)))
                base_sensitivity = auto_sensitivity
                auto_sensitivity = min(255.0, auto_sensitivity * auto_scale)
                runner.note("Автонастройка порога", f"Файл: {path}\nРазмер анализа: {analysis_res}\n"
                            f"Пропуск: {skip}\nСглаживание: {smoothing_sec}\n"
                            f"Базовый порог: {base_sensitivity}\nКоэффициент: {auto_scale}\n"
                            f"Итоговый порог: {auto_sensitivity}")
                context.ui_queue.put(("info", f"Порог: {base_sensitivity:.3f} × {auto_scale} → {auto_sensitivity:.3f}"))
                context.ui_queue.put(("info", f"Автонастроенная чувствительность: {auto_sensitivity:.2f}"))
                sensitivity = auto_sensitivity

            segments = []
            if not context.stop_event.is_set():
                context.ui_queue.put(("phase", f"Анализ движения: {Path(path).name}"))
                segments = analyze_video(path, sensitivity, min_pause, min_motion, analysis_res, skip,
                                         progress_cb=file_progress, stop_event=context.stop_event,
                                         preview_cb=preview_callback, merge_gap=merge_gap, smoothing_sec=smoothing_sec,
                                         pre_motion_sec=pre_motion_sec, post_motion_sec=post_motion_sec)

            if not context.stop_event.is_set():
                out_dir = source_output_directory(run_dir, path)
                created = []
                if segments:
                    created = save_segments(path, segments, ffmpeg_path, out_dir, runner)
                    context.ui_queue.put(("info", f"Найдено сегментов: {len(segments)}"))
                elif save_full:
                    full_path = save_full_video(path, ffmpeg_path, out_dir, runner)
                    created = [full_path]
                    context.ui_queue.put(("info", f"Сохранено полное видео: {full_path}"))

                # --- Апскейл сегментов (если включено) ---
                if enhance_after_split and created:
                    for seg_file in created:
                        if context.stop_event.is_set(): break
                        seg_path = Path(seg_file)
                        up_path = seg_path.with_name(seg_path.stem + "_upscaled" + seg_path.suffix)
                        context.ui_queue.put(("info", f"Real-ESRGAN апскейл: {seg_path.name} → {up_path.name}"))
                        enhance_video_fallback_frames(seg_path, up_path, realesrgan_exe, model, scale, tile, gpuid, ffmpeg_path,
                                                      ui_log=lambda m: context.ui_queue.put(("info", m)), runner=runner)
                        context.ui_queue.put(("info", f"Готово: {up_path}"))

            if context.stop_event.is_set():
                break
            done += 1
            context.ui_queue.put(("total_progress", (done, total)))

        return finish(context, "cancelled" if context.stop_event.is_set() else "done", done, len(files), run_dir)
    except TaskCancelled as error:
        context.ui_queue.put(("info", str(error)))
        return finish(context, "cancelled", done, len(files), run_dir)
    except Exception as e:
        message = record_task_error(runner, "Обработка видео", e)
        return finish(context, "error", done, len(files), run_dir, message)


def run_enhance(context: TaskContext, files, realesrgan_exe, model, scale, tile, gpuid) -> JobResult:
    runner = None
    run_dir = None
    done = 0
    try:
        check_cancel(context.stop_event)
        run_dir = create_run_directory(Path(files[0]).resolve().parent)
        runner = ProcessRunner(run_dir / "logs", context.stop_event)
        context.ui_queue.put(("info", f"Папка результатов: {run_dir}"))
        context.ui_queue.put(("info", f"Журналы диагностики: {runner.log_directory}"))
        context.ui_queue.put(("total_init", len(files)))
        ffmpeg_path = find_ffmpeg()
        executable = find_realesrgan(realesrgan_exe)
        check_tools(runner, ffmpeg_path, executable)
        done = 0
        failures = 0
        for f in files:
            if context.stop_event.is_set():
                break
            try:
                in_p = Path(f)
                out_dir = source_output_directory(run_dir, in_p)
                out_p = out_dir / (in_p.stem + "_upscaled" + in_p.suffix)
                context.ui_queue.put(("info", f"Апскейл: {in_p.name} → {out_p.name}"))
                enhance_video_fallback_frames(in_p, out_p, executable, model, scale, tile, gpuid, ffmpeg_path,
                                              ui_log=lambda m: context.ui_queue.put(("info", m)), runner=runner)
                context.ui_queue.put(("info", f"Готово: {out_p}"))
            except TaskCancelled:
                raise
            except Exception as error:
                failures += 1
                context.ui_queue.put(("info", record_task_error(runner, "Улучшение видео", error)))
            done += 1
            context.ui_queue.put(("total_progress", (done, len(files))))
        if context.stop_event.is_set():
            return finish(context, "cancelled", done, len(files), run_dir)
        elif failures:
            return finish(context, "error", done, len(files), run_dir, f"Улучшение завершилось с ошибками: {failures} из {done} файлов.")
        else:
            return finish(context, "done", done, len(files), run_dir)
    except TaskCancelled as error:
        context.ui_queue.put(("info", str(error)))
        return finish(context, "cancelled", done, len(files), run_dir)
    except Exception as error:
        return finish(context, "error", done, len(files), run_dir, record_task_error(runner, "Улучшение видео", error))
