"""Real-ESRGAN pipeline with disk budgets and validated frame timestamps."""
import shutil
from pathlib import Path
from external_tools import ProcessRunner, ToolError, find_ffprobe
from realesrgan_config import configuration, validate_frames
from output_safety import staged_output, validate_output_path
from media_probe import probe_video, validate_export
from video_timing import read_timeline, write_concat, compare_timeline
from enhance_resources import DiskBudget, estimate_space


def enhance_video_with_realesrgan(input_path, output_path, realesrgan_exe, model, scale, tile=0, gpuid=0, runner=None):
    """Compatibility entry point: upstream ncnn-vulkan accepts images only."""
    raise ToolError("Прямой апскейл видео не поддерживается ncnn-vulkan. Используйте покадровый режим.")


def enhance_video_fallback_frames(input_path, output_path, realesrgan_exe, model, scale, tile, gpuid,
                                  ffmpeg_path, ui_log=None, runner=None, disk_limit_gb=32, disk_reserve_gb=2):
    """Extract, upscale and mux; publish only after timing and decoding checks."""
    validate_output_path(input_path, output_path)
    if Path(output_path).suffix.lower() != '.mp4':
        raise ToolError('Результат апскейла должен иметь расширение .mp4 (H.264/AAC).')
    runner = runner or ProcessRunner(Path(output_path).parent / 'logs')
    runner.check_cancel()
    command = configuration(realesrgan_exe, model, scale, tile, gpuid)
    ffprobe = find_ffprobe(ffmpeg_path)
    info = probe_video(input_path, ffprobe, runner)
    with staged_output(input_path, output_path, runner.check_cancel) as temporary:
        budget = DiskBudget(temporary.parent, disk_limit_gb, disk_reserve_gb)
        previous_guard = runner.resource_guard
        def guard():
            if previous_guard:
                previous_guard()
            budget.check()
        runner.resource_guard = guard
        try:
            timeline = read_timeline(input_path, ffprobe, runner, temporary.parent / 'source-timing.txt', info)
            frames = len(timeline['pts'])
            estimate = estimate_space(info['video']['width'], info['video']['height'], frames, scale)
            budget.require(estimate)
            message = f'Апскейл: {frames} кадров; оценка диска {estimate/1024**3:.2f} ГиБ; лимит {disk_limit_gb} ГиБ.'
            if ui_log: ui_log(message)
            runner.note('Бюджет апскейла', message)
            frames_dir = temporary.parent / 'frames'
            out_frames_dir = temporary.parent / 'frames_up'
            frames_dir.mkdir(); out_frames_dir.mkdir()
            if ui_log: ui_log('Извлечение кадров через FFmpeg…')
            runner.run([ffmpeg_path, '-n', '-i', str(input_path), '-map', '0:V:0',
                        '-fps_mode', 'passthrough', str(frames_dir / '%08d.png')],
                       'FFmpeg: извлечение кадров')
            if len(list(frames_dir.glob('*.png'))) != frames:
                raise ToolError('Количество извлечённых кадров не совпало с FFprobe.')
            budget.check(force=True)
            if ui_log: ui_log('Апскейл кадров Real-ESRGAN…')
            runner.run(command + ['-i', str(frames_dir), '-o', str(out_frames_dir)],
                       'Real-ESRGAN: улучшение кадров (модель/GPU)')
            validate_frames(frames_dir, out_frames_dir, scale, runner)
            budget.check(force=True)
            shutil.rmtree(frames_dir)  # Only the current job's owned extraction directory.
            concat = temporary.parent / 'frames.ffconcat'
            write_concat(concat, timeline)
            if ui_log: ui_log('Сборка видео с исходными временными метками…')
            duration = timeline['duration']
            # PNG packets have a nominal duration of one microsecond. Set the
            # final packet's duration explicitly; no B-frames keeps N in display order.
            runner.run([ffmpeg_path, '-n', '-copyts', '-f', 'concat', '-safe', '0', '-i', str(concat),
                        '-itsoffset', f"{-timeline['start']:.6f}", '-i', str(input_path),
                        '-map', '0:v:0', '-map', '1:a:0?', '-map_metadata', '-1', '-map_chapters', '-1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
                        '-crf', '18', '-preset', 'medium', '-fps_mode', 'vfr',
                        '-enc_time_base', '1:1000000', '-bf', '0', '-video_track_timescale', '1000000',
                        '-bsf:v', f"setts=duration=if(eq(N\\,{frames-1})\\,{round(timeline['last_duration']*1000000)}\\,DURATION)",
                        '-c:a', 'aac', '-b:a', '192k', '-af', f'atrim=start=0:end={duration:.6f}',
                        '-t', f'{duration:.6f}', '-movflags', '+faststart', str(temporary)],
                       'FFmpeg: сборка видео и звука')
            budget.check(force=True)
            result = validate_export(temporary, info, ffprobe, ffmpeg_path, runner, 'accurate', duration)
            actual = read_timeline(temporary, ffprobe, runner, temporary.parent / 'result-timing.txt', result)
            compare_timeline(timeline, actual)
            if abs(actual['start']) > .002:
                raise ToolError('Начальная временная метка результата не равна нулю.')
            if info['audio'] and result['audio']:
                # Preserve delayed audio; trim samples preceding the video timeline.
                expected_start = max(0, float(info['audio'][0].get('start_time', timeline['start'])) - timeline['start'])
                actual_start = float(result['audio'][0].get('start_time', 0))
                if abs(expected_start - actual_start) > .05:
                    raise ToolError('Начало звука сместилось при апскейле.')
            budget.check(force=True)
            runner.note('Тайминг апскейла', f"Проверено {frames} кадров, длительность {duration:.6f} с.")
        finally:
            runner.resource_guard = previous_guard
    return True
