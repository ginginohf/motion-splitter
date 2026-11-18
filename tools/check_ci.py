"""Repeatable local/CI checks, durable logs, no personal videos or settings."""
import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from external_tools import ProcessRunner, find_ffprobe


def fixture_state(directory):
    required = ('static.mp4', 'motion_middle.mp4', 'motion_to_end.mp4', 'motion_with_audio.mp4', 'manifest.json')
    exists = [(directory / name).is_file() for name in required]
    if all(exists):
        return 'ready'
    if any(exists):
        raise RuntimeError('Incomplete generated fixtures. Move tests/fixtures/generated aside and rerun; no files were overwritten.')
    return 'missing'


def syntax_check():
    files = list(ROOT.glob('*.py'))
    for folder in ('tools', 'tests'):
        files.extend((ROOT / folder).rglob('*.py'))
    for path in files:
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gui-only', action='store_true', help='Requires a desktop session or Xvfb; fixtures must exist.')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ['PYTHONUTF8'] = '1'
    directory = ROOT / 'tests' / 'ci-artifacts'
    directory.mkdir(parents=True, exist_ok=True)
    runner = ProcessRunner(directory)
    mode = 'gui' if args.gui_only else 'core'
    summary = {'mode': mode, 'python': sys.version, 'platform': platform.platform(), 'steps': [], 'status': 'running'}

    def run(name, command, timeout=300):
        entry = {'name': name, 'command': list(map(str, command)), 'status': 'running'}
        summary['steps'].append(entry)
        started = time.monotonic()
        print(name, flush=True)
        try:
            entry['log'] = str(runner.run(command, name, timeout=timeout))
            entry['status'] = 'passed'
        except Exception as error:
            entry.update(status='failed', error=str(error))
            raise
        finally:
            entry['elapsed_sec'] = round(time.monotonic() - started, 3)

    try:
        if args.gui_only:
            if fixture_state(ROOT / 'tests/fixtures/generated') != 'ready':
                raise RuntimeError('Run python tools/check_ci.py first to generate fixtures.')
            for script in ('check_gui_settings.py', 'check_gui_threads.py', 'check_gui_close.py', 'check_gui_layout.py'):
                run(script, [sys.executable, str(ROOT / 'tools' / script)], timeout=60)
        else:
            summary['compiled_files'] = syntax_check()
            run('Dependency compatibility', [sys.executable, '-m', 'pip', 'check'])
            run('Dependency versions', [sys.executable, '-m', 'pip', 'freeze'])
            import imageio_ffmpeg
            run('FFmpeg version', [imageio_ffmpeg.get_ffmpeg_exe(), '-version'])
            run('FFprobe version', [find_ffprobe(), '-version'])
            if fixture_state(ROOT / 'tests/fixtures/generated') == 'missing':
                run('Generate synthetic fixtures', [sys.executable, 'tools/generate_fixtures.py'])
            run('Unit and media integration tests', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        summary['status'] = 'passed'
        return 0
    except Exception as error:
        summary.update(status='failed', error=str(error))
        print(error, file=sys.stderr)
        return 1
    finally:
        target = directory / (mode + '-summary.json')
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Summary: {target}', flush=True)


if __name__ == '__main__':
    sys.exit(main())
