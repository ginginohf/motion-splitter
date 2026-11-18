"""Headless task orchestration. No Tk imports and no widget access."""
from realesrgan_config import preflight as preflight_realesrgan
from pathlib import Path
from time import monotonic
from job_contracts import TaskContext, JobResult, finish
from batch_report import BatchReport, now
from external_tools import ProcessRunner, find_ffmpeg, find_ffprobe, find_realesrgan, check_tools, check_cancel, TaskCancelled
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


def _run_batch(context, files, mode, parameters, prepare, process):
    started = monotonic()
    runner = report = run_dir = None
    status, error = 'done', None
    try:
        check_cancel(context.stop_event)
        if not files:
            raise ValueError('Не выбраны файлы для обработки.')
        run_dir = create_run_directory(Path(files[0]).resolve().parent)
        runner = ProcessRunner(run_dir / 'logs', context.stop_event)
        report = BatchReport(run_dir, mode, files, parameters)
        report.save()
        context.ui_queue.put(('info', f'Папка результатов: {run_dir}\nЖурналы диагностики: {runner.log_directory}'))
        context.ui_queue.put(('total_init', len(files)))
        dependencies = prepare(runner)
        for path, item in zip(files, report.data['files']):
            check_cancel(context.stop_event)
            file_started = monotonic()
            item.update(status='running', started_at=now())
            report.save()
            before_logs = set(runner.log_directory.glob('*'))
            context.ui_queue.put(('file_name', Path(path).name))
            try:
                outcome, note = process(path, item, run_dir, runner, dependencies)
                check_cancel(context.stop_event)
                item.update(status=outcome, note=note)
            except TaskCancelled as cancelled:
                item.update(status='cancelled', error=str(cancelled))
                status = 'cancelled'
            except Exception as failure:
                message = record_task_error(runner, f'Файл: {path}', failure)
                item.update(status='failed', error=message)
                context.ui_queue.put(('info', f'{Path(path).name}: {message}'))
            finally:
                item['finished_at'] = now()
                item['elapsed_sec'] = round(monotonic() - file_started, 3)
                report.collect_outputs(item)
                item['logs'] = [str(p) for p in sorted(set(runner.log_directory.glob('*')) - before_logs) if p.is_file()]
                report.save()
            counts = report.counts()
            completed = counts['success'] + counts['skipped'] + counts['failed']
            context.ui_queue.put(('total_progress', (completed, len(files))))
            if status == 'cancelled':
                break
        if status != 'cancelled' and report.counts()['failed']:
            status = 'error'
            last = next(i['error'] for i in reversed(report.data['files']) if i['status'] == 'failed')
            error = f"Ошибок файлов: {report.counts()['failed']}. Последняя ошибка: {last}"
    except TaskCancelled as cancelled:
        status = 'cancelled'
        context.ui_queue.put(('info', str(cancelled)))
    except Exception as failure:
        status = 'error'
        error = record_task_error(runner, 'Пакетная обработка', failure)
    report_path = None
    counts = {}
    if report:
        report.data.update(status=status, error=error, finished_at=now(), elapsed_sec=round(monotonic()-started, 3))
        # A report-write failure must not make an interrupted entry look successful.
        for item in report.data['files']:
            if item['status'] == 'running':
                item.update(status='cancelled' if status == 'cancelled' else 'failed', error=error, finished_at=now())
        try:
            report.save()
            report_path = report.path
        except Exception as failure:
            status = 'error'
            error = f'{error or ""}\nНе удалось сохранить итоговый отчёт: {failure}'.strip()
        counts = report.counts()
        summary = report.summary() if report_path else f'Итоговый отчёт не сохранён. {error}'
        context.ui_queue.put(('summary', summary))
    processed = sum(counts.get(key, 0) for key in ('success', 'skipped', 'failed'))
    return finish(context, status, processed, len(files), run_dir, error, report_path, counts)


def run_split(context: TaskContext, files, sensitivity, min_pause, min_motion, analysis_res, skip, save_full, auto_sens, auto_scale, merge_gap,
              enhance_after_split, realesrgan_exe, model, scale, tile, gpuid,
              smoothing_sec=0.2, pre_motion_sec=0, post_motion_sec=0, export_mode='fast', disk_limit_gb=32, disk_reserve_gb=2, models_dir="") -> JobResult:
    parameters = {key: value for key, value in locals().items() if key not in ('context', 'files')}

    def prepare(runner):
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe(ffmpeg)
        executable = find_realesrgan(realesrgan_exe) if enhance_after_split else None
        check_tools(runner, ffmpeg, executable, ffprobe)
        if executable:
            context.ui_queue.put(('phase', 'Проверка Real-ESRGAN: модель/GPU'))
            preflight_realesrgan(executable, model, scale, tile, gpuid, runner, models_dir=models_dir)
        return ffmpeg, ffprobe, executable

    def process(path, item, run_dir, runner, dependencies):
        ffmpeg, ffprobe, executable = dependencies
        def log(message): context.ui_queue.put(('info', message))
        def progress(cur, total): context.ui_queue.put(('file_progress', (cur, total)))
        def preview(frame, index, score): context.ui_queue.put(('preview_frame', (frame, index, score)))
        threshold = sensitivity
        if auto_sens:
            context.ui_queue.put(('phase', f'Автонастройка порога: {Path(path).name}'))
            base = calculate_auto_sensitivity(path, analysis_res, skip, progress_cb=progress,
                                             stop_event=context.stop_event, smoothing_sec=smoothing_sec, diagnostics_cb=log)
            threshold = min(255.0, base * auto_scale)
            runner.note('Автонастройка порога', f'Файл: {path}\nБазовый порог: {base}\nКоэффициент: {auto_scale}\nИтоговый порог: {threshold}')
            log(f'Автонастроенная чувствительность: {threshold:.2f}')
        item['effective_sensitivity'] = threshold
        context.ui_queue.put(('phase', f'Анализ движения: {Path(path).name}'))
        segments = analyze_video(path, threshold, min_pause, min_motion, analysis_res, skip,
                                 progress_cb=progress, stop_event=context.stop_event, preview_cb=preview,
                                 merge_gap=merge_gap, smoothing_sec=smoothing_sec,
                                 pre_motion_sec=pre_motion_sec, post_motion_sec=post_motion_sec)
        check_cancel(context.stop_event)
        item['segments'] = segments
        if not segments and not save_full:
            return 'skipped', 'Движение не найдено; сохранение полного видео отключено.'
        out_dir = source_output_directory(run_dir, path)
        item['output_directory'] = str(out_dir)
        context.ui_queue.put(('phase', f'Экспорт и проверка: {Path(path).name}'))
        kwargs = dict(mode=export_mode, ffprobe_path=ffprobe, ui_log=log)
        if segments:
            created = save_segments(path, segments, ffmpeg, out_dir, runner, **kwargs)
        else:
            created = [save_full_video(path, ffmpeg, out_dir, runner, **kwargs)]
        if enhance_after_split:
            for output in created:
                check_cancel(context.stop_event)
                segment = Path(output)
                target = segment.with_name(segment.stem + '_upscaled.mp4')
                enhance_video_fallback_frames(segment, target, executable, model, scale, tile, gpuid, ffmpeg,
                                              ui_log=log, runner=runner, disk_limit_gb=disk_limit_gb, disk_reserve_gb=disk_reserve_gb, models_dir=models_dir)
        return 'success', 'Сохранены сегменты.' if segments else 'Сохранено полное видео: движения нет.'

    return _run_batch(context, files, 'split', parameters, prepare, process)


def run_enhance(context: TaskContext, files, realesrgan_exe, model, scale, tile, gpuid, disk_limit_gb=32, disk_reserve_gb=2, models_dir="") -> JobResult:
    parameters = {key: value for key, value in locals().items() if key not in ('context', 'files')}

    def prepare(runner):
        ffmpeg = find_ffmpeg()
        executable = find_realesrgan(realesrgan_exe)
        check_tools(runner, ffmpeg, executable, find_ffprobe(ffmpeg))
        context.ui_queue.put(('phase', 'Проверка Real-ESRGAN: модель/GPU'))
        preflight_realesrgan(executable, model, scale, tile, gpuid, runner, models_dir=models_dir)
        return ffmpeg, executable

    def process(path, item, run_dir, runner, dependencies):
        ffmpeg, executable = dependencies
        out_dir = source_output_directory(run_dir, path)
        item['output_directory'] = str(out_dir)
        source = Path(path)
        target = out_dir / (source.stem + '_upscaled.mp4')
        enhance_video_fallback_frames(source, target, executable, model, scale, tile, gpuid, ffmpeg,
                                      ui_log=lambda message: context.ui_queue.put(('info', message)), runner=runner,
                                      disk_limit_gb=disk_limit_gb, disk_reserve_gb=disk_reserve_gb, models_dir=models_dir)
        return 'success', 'Апскейл сохранён.'

    return _run_batch(context, files, 'enhance', parameters, prepare, process)
