"""Explicit diagnostic entry point for installed/frozen application acceptance."""
import json
import os
import tempfile
import traceback
from pathlib import Path


def run_selftest(report_path, gui=False):
    report = {'status': 'failed'}
    try:
        import cv2
        import numpy
        import tkinter
        from PIL import Image
        from external_tools import find_ffmpeg, find_ffprobe, ProcessRunner
        from video_export import save_segments
        from video_analysis import analyze_video
        with tempfile.TemporaryDirectory(prefix='motion-splitter-selftest-') as temp:
            work = Path(temp)
            runner = ProcessRunner(work / 'logs')
            ffmpeg, ffprobe = find_ffmpeg(), find_ffprobe()
            report.update(ffmpeg=ffmpeg, ffprobe=ffprobe)
            source = work / 'source.mp4'
            runner.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=64x64:rate=25:duration=2', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac', str(source)], 'generate', timeout=30)
            playlist = work / 'concat.txt'
            playlist.write_text("file 'source.mp4'\nfile 'source.mp4'\n", encoding='utf-8')
            runner.run([ffmpeg, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(playlist),
                        '-c', 'copy', '-bsf:v', 'setts=pts=PTS', str(work / 'concat.mp4')],
                       'concat/setts', timeout=30)
            segments = analyze_video(source, .01, .1, .1, 'Оригинал', 0)
            if not segments:
                raise RuntimeError('Moving fixture was not detected')
            output = work / 'output'; output.mkdir()
            paths = save_segments(source, [(0.2, 1.4)], ffmpeg, output, runner,
                                  mode='accurate', ffprobe_path=ffprobe)
            if len(paths) != 1:
                raise RuntimeError('Export did not create one segment')
            if gui:
                root = tkinter.Tk()
                root.after(150, root.destroy)
                root.mainloop()
            report.update(status='passed', gui=gui, detected_segments=len(segments))
    except Exception:
        report['error'] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['status'] == 'passed' else 1
