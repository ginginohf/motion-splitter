"""Real-ESRGAN pipeline independent of the GUI."""
import cv2
from pathlib import Path
from external_tools import ProcessRunner, ToolError
from realesrgan_config import configuration, validate_frames
from output_safety import staged_output, validate_output_path

def enhance_video_with_realesrgan(input_path, output_path, realesrgan_exe, model, scale, tile=0, gpuid=0, runner=None):
    """Compatibility entry point: upstream ncnn-vulkan accepts images only."""
    raise ToolError("Прямой апскейл видео не поддерживается ncnn-vulkan. Используйте покадровый режим.")


def enhance_video_fallback_frames(input_path, output_path, realesrgan_exe, model, scale, tile, gpuid, ffmpeg_path, ui_log=None, runner=None):
    """Extract, upscale and mux within a unique directory owned by this call."""
    validate_output_path(input_path, output_path)
    runner = runner or ProcessRunner(Path(output_path).parent / "logs")
    runner.check_cancel()
    command = configuration(realesrgan_exe, model, scale, tile, gpuid)
    with staged_output(input_path, output_path, runner.check_cancel) as temporary:
        frames_dir = temporary.parent / "frames"
        out_frames_dir = temporary.parent / "frames_up"
        frames_dir.mkdir()
        out_frames_dir.mkdir()

        cap = cv2.VideoCapture(str(input_path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        finally:
            cap.release()

        if ui_log: ui_log("Извлечение кадров через FFmpeg…")
        cmd_extract = [ffmpeg_path, "-n", "-i", str(input_path), "-vsync", "0",
                       str(frames_dir / "%08d.png")]
        runner.run(cmd_extract, "FFmpeg: извлечение кадров")

        if ui_log: ui_log("Апскейл кадров Real-ESRGAN…")
        cmd_realesr = command + ["-i", str(frames_dir), "-o", str(out_frames_dir)]
        runner.run(cmd_realesr, "Real-ESRGAN: улучшение кадров (модель/GPU)")

        validate_frames(frames_dir, out_frames_dir, scale, runner)

        if ui_log: ui_log("Сборка апскейленного видео…")
        cmd_mux = [ffmpeg_path, "-n", "-r", f"{fps:.3f}",
                   "-i", str(out_frames_dir / "%08d.png"), "-i", str(input_path),
                   "-map", "0:v:0", "-map", "1:a?", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium",
                   "-c:a", "copy", str(temporary)]
        runner.run(cmd_mux, "FFmpeg: сборка видео и звука")
    return True
