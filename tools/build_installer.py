"""Build native, unsigned review artifacts. Never download or publish anything."""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {('Windows', 'AMD64'): 'windows-x64', ('Darwin', 'arm64'): 'macos-arm64',
           ('Linux', 'x86_64'): 'linux-amd64'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_vendor(directory, target):
    """Require reviewed, checksummed binaries and accompanying notices/source info."""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('target') != target:
        raise ValueError('Vendor target does not match the native build target')
    for field in ('ffmpeg_version', 'origin_url', 'source_url', 'configuration'):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError('Missing vendor provenance: ' + field)
    suffix = '.exe' if target == 'windows-x64' else ''
    files = manifest.get('files', {})
    required = {'ffmpeg' + suffix, 'ffprobe' + suffix, 'notices/FFmpeg.txt', 'notices/SOURCES.txt',
                'notices/DEPENDENCIES.txt'}
    if not required.issubset(files):
        raise ValueError('Manifest must cover binaries, license, source and dependency notices')
    for relative, expected in files.items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or not path.stat().st_size:
            raise ValueError('Invalid vendor file: ' + relative)
        if not re.fullmatch('[0-9a-f]{64}', expected) or sha256(path) != expected:
            raise ValueError('SHA-256 mismatch: ' + relative)
    # Never bundle undeclared files such as model weights, secrets or stale notices.
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual != set(files) | {'manifest.json'}:
        raise ValueError('Vendor directory contains undeclared files')
    return manifest


def run(command, **kwargs):
    subprocess.run([str(item) for item in command], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--version', default='1.0.0')
    parser.add_argument('--iscc', default='ISCC.exe')
    parser.add_argument('--app-only', action='store_true')
    parser.add_argument('--development', action='store_true', help='Label local unreviewed artifacts; never publish')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Version must have form X.Y.Z')
    target = TARGETS.get((platform.system(), platform.machine()))
    if target is None:
        parser.error('Unsupported build host/architecture')
    import tkinter
    tcl = tkinter.Tcl()
    if tuple(map(int, tcl.eval('info patchlevel').split('.')[:2])) < (8, 6):
        raise ValueError('Tcl/Tk 8.6 or newer is required; Apple system Tk 8.5 cannot be packaged')
    vendor = args.vendor.resolve()
    manifest = validate_vendor(vendor, target)
    if not args.development and manifest.get('redistribution_reviewed') is not True:
        raise ValueError('Vendor distribution review incomplete; use --development only for local inspection')
    suffix = '.exe' if target == 'windows-x64' else ''
    for name in ('ffmpeg', 'ffprobe'):
        run([vendor / (name + suffix), '-version'], stdout=subprocess.DEVNULL)
    # Functional self-test exercises codecs, muxer, filters, ffprobe and decoding.
    work = ROOT / 'build' / ('installer-' + target)
    work.mkdir(parents=True, exist_ok=True)
    media = work / 'media-smoke.mp4'
    run([vendor / ('ffmpeg' + suffix), '-y', '-v', 'error', '-f', 'lavfi', '-i',
         'color=size=64x64:rate=25:duration=1', '-f', 'lavfi', '-i',
         'sine=frequency=440:duration=1', '-c:v', 'libx264', '-c:a', 'aac',
         '-bsf:v', 'setts=pts=PTS', media])
    run([vendor / ('ffprobe' + suffix), '-v', 'error', '-show_streams', media], stdout=subprocess.DEVNULL)
    run([vendor / ('ffmpeg' + suffix), '-v', 'error', '-i', media, '-f', 'null', '-'])
    env = dict(os.environ, MOTION_SPLITTER_VENDOR=str(vendor), MOTION_SPLITTER_VERSION=args.version,
               PYINSTALLER_CONFIG_DIR=str(work / 'cache'))
    if target == 'macos-arm64':
        from macos_vendor import stage
        isolated = work / 'isolated-vendor'
        if isolated.exists():
            raise ValueError('Move previous isolated-vendor staging aside before rebuilding')
        env['MOTION_SPLITTER_MAC_TOOLS'] = str(stage(vendor, isolated, None if args.development else manifest.get('dependency_sha256', {})))
    final_output = ROOT / 'dist' / (target + ('-development' if args.development else ''))
    output = final_output
    if target == 'macos-arm64':
        # Finder/iCloud can attach FinderInfo to .app/.framework in Documents during signing.
        output = Path(tempfile.mkdtemp(prefix='motion-splitter-package-')) / 'dist'
    # PyInstaller refuses replacement unless explicitly asked; fresh output per build.
    if final_output.exists():
        raise ValueError('Build output already exists; move it aside before rebuilding: ' + str(final_output))
    run([sys.executable, '-m', 'PyInstaller', '--clean', '--distpath', output,
         '--workpath', work / 'pyinstaller', ROOT / 'packaging' / 'MotionSplitter.spec'], cwd=ROOT, env=env)
    if target == 'macos-arm64':
        app = output / 'MotionSplitter.app'
        run(['xattr', '-cr', app])
        run(['xattr', '-crs', app])  # Only the newly generated app bundle.
        # Ad-hoc signature is required for ARM execution; this is NOT Developer ID/notarization.
        run(['codesign', '--force', '--deep', '--sign', '-', app])
        run(['xattr', '-crs', app])
        run(['codesign', '--verify', '--deep', '--strict', app])
    executable = (output / 'MotionSplitter.app' / 'Contents' / 'MacOS' / 'MotionSplitter'
                  if target == 'macos-arm64' else output / 'MotionSplitter' / ('MotionSplitter' + suffix))
    report = output / 'self-test.json'
    run([executable, '--self-test', report], cwd=work, env=dict(os.environ, PATH=''), timeout=120)
    if json.loads(report.read_text(encoding='utf-8')).get('status') != 'passed':
        raise ValueError('Frozen app self-test failed: ' + str(report))
    if args.app_only:
        if output != final_output:
            shutil.copytree(output, final_output, symlinks=True)
        return
    name = f'MotionSplitter-{args.version}-{target}' + ('-development' if args.development else '')
    if target == 'windows-x64':
        run([args.iscc, '/DArtifactName=' + name, '/DAppVersion=' + args.version, '/DSourceDir=' + str(output / 'MotionSplitter'),
             '/DOutputDir=' + str(output), ROOT / 'packaging' / 'windows' / 'setup.iss'])
        artifact = output / (name + '.exe')
    elif target == 'macos-arm64':
        artifact = output / (name + '.pkg')
        run(['pkgbuild', '--component', output / 'MotionSplitter.app', '--install-location', '/Applications',
             '--identifier', 'org.motion-splitter.app', '--version', args.version, artifact])
    else:
        stage = work / 'deb'
        stage.mkdir()  # Refuse stale staging directories.
        shutil.copytree(output / 'MotionSplitter', stage / 'opt' / 'motion-splitter', symlinks=True)
        (stage / 'DEBIAN').mkdir()
        (stage / 'DEBIAN' / 'control').write_text(
            'Package: motion-splitter\nVersion: ' + args.version + '\nArchitecture: amd64\n'
            'Maintainer: Motion Splitter contributors\nSection: video\nPriority: optional\n'
            'Depends: libc6 (>= 2.39), libgl1, libglib2.0-0, libx11-6, libxext6, libxrender1, libfontconfig1\n'
            'Description: Motion detection video splitter with optional Real-ESRGAN enhancement\n', encoding='utf-8')
        desktop = stage / 'usr' / 'share' / 'applications'
        desktop.mkdir(parents=True)
        shutil.copy2(ROOT / 'packaging' / 'linux' / 'motion-splitter.desktop', desktop)
        icons = stage / 'usr' / 'share' / 'icons' / 'hicolor' / '512x512' / 'apps'
        icons.mkdir(parents=True)
        shutil.copy2(ROOT / 'assets' / 'icon-512.png', icons / 'motion-splitter.png')
        artifact = output / (name + '.deb')
        run(['dpkg-deb', '--root-owner-group', '--build', stage, artifact])
    (output / 'SHA256SUMS').write_text(sha256(artifact) + '  ' + artifact.name + '\n', encoding='utf-8')
    (output / 'build-info.json').write_text(json.dumps({
        'version': args.version, 'target': target, 'python': sys.version,
        'host': platform.platform(), 'signed_for_distribution': False, 'development_only': args.development, 'vendor': manifest,
    }, indent=2), encoding='utf-8')
    if output != final_output:
        final_output.mkdir(parents=True)
        for item in (artifact, output / 'SHA256SUMS', output / 'build-info.json', report):
            shutil.copy2(item, final_output / item.name)
        artifact = final_output / artifact.name
    print(artifact)


if __name__ == '__main__':
    main()
