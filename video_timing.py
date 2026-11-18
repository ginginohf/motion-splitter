"""Read frame timestamps and build a microsecond-resolution concat timeline."""
import math
from external_tools import ToolError


def read_timeline(path, ffprobe, runner, report, video_info):
    runner.run([ffprobe, '-v', 'error', '-select_streams', 'V:0', '-show_frames',
                '-show_entries', 'frame=best_effort_timestamp_time,duration_time,pkt_duration_time,width,height',
                '-of', 'compact=p=0:nk=0', '-o', str(report), str(path)],
               'FFprobe: временные метки кадров')
    points, last_duration = [], None
    expected_size = (video_info['video']['width'], video_info['video']['height'])
    with report.open(encoding='utf-8') as stream:
        for line in stream:
            runner.check_cancel()
            if len(line) > 8192 or len(points) >= 500000:
                raise ToolError('Слишком большой список кадров (максимум 500000).')
            data = dict(part.split('=', 1) for part in line.strip().split('|') if '=' in part)
            if 'width' not in data:
                continue
            try:
                if (int(data['width']), int(data['height'])) != expected_size:
                    raise ValueError('изменение размера кадров')
                timestamp = float(data['best_effort_timestamp_time'])
                if not math.isfinite(timestamp) or (points and timestamp <= points[-1]):
                    raise ValueError('немонотонные временные метки')
                value = data.get('duration_time', data.get('pkt_duration_time', '0'))
                last_duration = float(value) if value != 'N/A' else 0
            except (KeyError, ValueError) as error:
                raise ToolError(f'Невозможно сохранить тайминг: {error}') from error
            points.append(timestamp)
    if not points:
        raise ToolError('FFprobe не обнаружил кадров.')
    if not last_duration or not math.isfinite(last_duration) or last_duration <= 0:
        try:
            last_duration = points[0] + float(video_info['video']['duration']) - points[-1]
        except (KeyError, ValueError, TypeError) as error:
            raise ToolError('Нет достоверной длительности последнего кадра.') from error
    if not math.isfinite(last_duration) or last_duration <= 0:
        raise ToolError('Не удалось определить длительность последнего кадра.')
    normalized = [round(p - points[0], 6) for p in points]
    if any(b <= a for a, b in zip(normalized, normalized[1:])):
        raise ToolError('Интервал между кадрами меньше микросекунды.')
    return {'pts': normalized, 'start': points[0], 'last_duration': last_duration,
            'duration': normalized[-1] + last_duration}


def write_concat(path, timeline):
    points = timeline['pts']
    with path.open('w', encoding='utf-8') as stream:
        stream.write('ffconcat version 1.0\n')
        for i, timestamp in enumerate(points):
            duration = points[i + 1] - timestamp if i + 1 < len(points) else timeline['last_duration']
            # Fixed, app-generated relative paths: no user strings in concat syntax.
            stream.write(f"file 'frames_up/{i+1:08d}.png'\noption framerate 1000000\nduration {duration:.6f}\n")


def compare_timeline(expected, actual):
    if len(expected['pts']) != len(actual['pts']):
        raise ToolError('Количество кадров изменилось при сборке апскейла.')
    if any(abs(a-b) > .002 for a,b in zip(expected['pts'], actual['pts'])):
        raise ToolError('Временные метки кадров изменились при сборке апскейла.')
    if abs(expected['duration'] - actual['duration']) > .01:
        raise ToolError('Длительность апскейла отличается от исходного таймлайна.')
