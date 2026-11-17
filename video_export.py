"""Safe FFmpeg export independent of the GUI."""
from pathlib import Path
from external_tools import ProcessRunner
from output_safety import create_run_directory, source_output_directory, staged_output

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm")

def save_segments(video_path, segments, ffmpeg_path, output_dir=None, runner=None):
    vpath = Path(video_path).resolve(strict=True)
    if output_dir is None:
        output_dir = source_output_directory(create_run_directory(vpath.parent), vpath)
    out_dir = Path(output_dir)
    runner = runner or ProcessRunner(out_dir / "logs")
    created = []
    for i, (start, end) in enumerate(segments, start=1):
        suffix = vpath.suffix if vpath.suffix.lower() in VIDEO_EXTS else ".mp4"
        out_file = out_dir / f"{vpath.stem}_segment_{i:02d}{suffix}"
        with staged_output(vpath, out_file, runner.check_cancel) as temporary:
            cmd = [ffmpeg_path, "-n", "-i", str(vpath),
                   "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                   "-c", "copy", str(temporary)]
            runner.run(cmd, f"FFmpeg: сохранение сегмента {i} ({vpath.name})")
        created.append(str(out_file))
    return created


def save_full_video(video_path, ffmpeg_path, output_dir=None, runner=None):
    vpath = Path(video_path).resolve(strict=True)
    if output_dir is None:
        output_dir = source_output_directory(create_run_directory(vpath.parent), vpath)
    runner = runner or ProcessRunner(Path(output_dir) / "logs")
    suffix = vpath.suffix if vpath.suffix.lower() in VIDEO_EXTS else ".mp4"
    out_file = Path(output_dir) / f"{vpath.stem}_full{suffix}"
    with staged_output(vpath, out_file, runner.check_cancel) as temporary:
        cmd = [ffmpeg_path, "-n", "-i", str(vpath), "-c", "copy", str(temporary)]
        runner.run(cmd, f"FFmpeg: сохранение полного видео ({vpath.name})")
    return str(out_file)
