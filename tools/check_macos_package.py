"""Inspect a PKG without installing into /Applications; optionally exercise Tk."""
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='motion-splitter-package-check-'))
    report = {'status': 'failed', 'working_directory': str(work)}
    try:
        subprocess.run(['pkgutil', '--expand-full', str(args.package.resolve()), str(work / 'expanded')], check=True)
        app = next((work / 'expanded').rglob('MotionSplitter.app'))
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        external, count = [], 0
        for path in app.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            with path.open('rb') as stream:
                magic = stream.read(4)
            if magic not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'):
                continue
            count += 1
            for line in subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]:
                dep = line.strip().split(' (compatibility version', 1)[0]
                if dep.startswith('/') and not dep.startswith(('/System/', '/usr/lib/')):
                    external.append([str(path.relative_to(app)), dep])
        report.update(macho_files=count, external_non_system_dependencies=external)
        if external:
            raise ValueError('Package depends on external non-system libraries')
        executable = app / 'Contents' / 'MacOS' / 'MotionSplitter'
        command = [str(executable), '--self-test', str(work / 'self-test.json')]
        if args.gui:
            command.append('--gui')
        subprocess.run(command, check=True, cwd=work, env=dict(os.environ, PATH=''), timeout=30)
        report['self_test'] = json.loads((work / 'self-test.json').read_text(encoding='utf-8'))
        if report['self_test']['status'] != 'passed':
            raise ValueError('Self-test failed')
        report['status'] = 'passed'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
