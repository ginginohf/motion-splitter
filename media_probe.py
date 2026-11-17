"""Probe media with FFprobe, keeping diagnostics outside temporary output dirs."""
import json
import math
import os
import tempfile
from fractions import Fraction
from pathlib import Path

from external_tools import ToolError


def probe_video(path, ffprobe, runner):
    runner.check_cancel()
    runner.log_directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="probe-", suffix=".json", dir=runner.log_directory)
    os.close(fd)
    report = Path(name)
    runner.run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                "-of", "json", "-o", str(report), str(path)], f"FFprobe: проверка {Path(path).name}")
    if report.stat().st_size > 4 * 1024 * 1024:
        raise ToolError(f"Слишком большой отчёт FFprobe: {report}")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        videos = [s for s in data["streams"] if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")]
        video = videos[0]
        if video.get("width", 0) <= 0 or video.get("height", 0) <= 0:
            raise ValueError("пустой видеопоток")
        duration = float(video.get("duration") or data.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("нет положительной длительности")
        try:
            fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
        except (ValueError, ZeroDivisionError):
            fps = 0
        return {"duration": duration, "fps": fps, "video": video, "video_count": len(videos),
                "audio": [s for s in data["streams"] if s.get("codec_type") == "audio"],
                "report": report}
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ToolError(f"Некорректные данные видео: {error}. Отчёт: {report}") from error


def validate_export(path, source_info, ffprobe, ffmpeg, runner, mode, expected_duration):
    result = probe_video(path, ffprobe, runner)
    if result["video_count"] != 1:
        raise ToolError("Экспорт должен содержать один видеопоток.")
    if bool(result["audio"]) != bool(source_info["audio"]):
        raise ToolError("Экспорт изменил наличие звуковой дорожки.")
    if len(result["audio"]) > 1:
        raise ToolError("Экспорт должен содержать только первую звуковую дорожку.")
    if mode == "accurate":
        if result["video"].get("codec_name") != "h264" or any(a.get("codec_name") != "aac" for a in result["audio"]):
            raise ToolError("Точный экспорт должен использовать H.264/AAC.")
        fps = source_info["fps"]
        tolerance = max(.1, 2 / fps) if fps > 0 else .1
        if abs(result["duration"] - expected_duration) > tolerance:
            raise ToolError(f"Длительность точного экспорта {result['duration']:.3f} с "
                            f"отличается от {expected_duration:.3f} с больше чем на {tolerance:.3f} с.")
    # Metadata alone does not prove that the video/audio can be decoded.
    runner.run([ffmpeg, "-v", "error", "-xerror", "-i", str(path),
                "-map", "0:V:0", "-map", "0:a:0?", "-f", "null", "-"],
               "FFmpeg: проверка декодирования результата")
    return result
