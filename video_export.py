"""Fast stream-copy and accurate H.264/AAC export with pre-publication checks."""
import math
from pathlib import Path
from external_tools import ProcessRunner, find_ffprobe, TaskCancelled
from media_probe import probe_video, validate_export
from output_safety import create_run_directory, source_output_directory, staged_output

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm")


def export_command(ffmpeg, source, target, mode, start=None, duration=None):
    if mode not in ("fast", "accurate"):
        raise ValueError("Режим экспорта должен быть fast или accurate.")
    command = [ffmpeg, "-n"]
    if start is not None:
        command += ["-ss", f"{start:.6f}"]
    command += ["-i", str(source)]
    if duration is not None:
        command += ["-t", f"{duration:.6f}"]
    command += ["-map", "0:V:0", "-map", "0:a:0?", "-map_chapters", "-1"]
    if mode == "fast":
        command += ["-c", "copy"]
    else:
        command += ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                    "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    return command + [str(target)]


def _export(video_path, segments, ffmpeg_path, output_dir, runner, mode, ffprobe_path, ui_log):
    if mode not in ("fast", "accurate"):
        raise ValueError("Неизвестный режим экспорта.")
    source = Path(video_path).resolve(strict=True)
    if output_dir is None:
        output_dir = source_output_directory(create_run_directory(source.parent), source)
    output_dir = Path(output_dir)
    runner = runner or ProcessRunner(output_dir / "logs")
    ffprobe = ffprobe_path or find_ffprobe(ffmpeg_path)
    created, failed = [], 0
    intervals = list(segments) if segments is not None else [None]
    try:
        source_info = probe_video(source, ffprobe, runner)
        # Validate all requested intervals before writing any output.
        for interval in intervals:
            if interval is not None:
                start, end = interval
                if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                    raise ValueError(f"Некорректный интервал: {interval}")
                if start >= source_info["duration"] or end > source_info["duration"] + .1:
                    raise ValueError(f"Интервал выходит за длительность видео: {interval}")
        suffix = ".mp4" if mode == "accurate" else (source.suffix if source.suffix.lower() in VIDEO_EXTS else ".mkv")
        for index, interval in enumerate(intervals, 1):
            runner.check_cancel()
            name = f"{source.stem}_full" if interval is None else f"{source.stem}_segment_{index:02d}"
            destination = output_dir / (name + suffix)
            start = interval[0] if interval is not None else None
            duration = min(interval[1], source_info["duration"]) - start if interval is not None else source_info["duration"]
            with staged_output(source, destination, runner.check_cancel) as temporary:
                runner.run(export_command(ffmpeg_path, source, temporary, mode, start,
                                          duration if interval is not None else None),
                           f"FFmpeg: экспорт {mode} ({destination.name})")
                validate_export(temporary, source_info, ffprobe, ffmpeg_path, runner, mode, duration)
            created.append(str(destination))
        return created
    except TaskCancelled:
        raise
    except Exception:
        failed = 1
        raise
    finally:
        summary = (f"Экспорт: сохранено {len(created)}, ошибок {failed}, "
                   f"не завершено {max(0, len(intervals) - len(created) - failed)}.")
        if ui_log:
            ui_log(summary)
        try:
            runner.note("Результаты экспорта", summary)
        except OSError:
            pass  # Do not hide the original export/disk error.


def save_segments(video_path, segments, ffmpeg_path, output_dir=None, runner=None,
                  mode="fast", ffprobe_path=None, ui_log=None):
    return _export(video_path, segments, ffmpeg_path, output_dir, runner, mode, ffprobe_path, ui_log)


def save_full_video(video_path, ffmpeg_path, output_dir=None, runner=None,
                    mode="fast", ffprobe_path=None, ui_log=None):
    return _export(video_path, None, ffmpeg_path, output_dir, runner, mode, ffprobe_path, ui_log)[0]
