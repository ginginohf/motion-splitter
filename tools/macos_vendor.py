"""Isolate the FFmpeg dylib closure from OpenCV's libraries of the same name."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def run(args):
    return subprocess.check_output([str(a) for a in args], text=True, stderr=subprocess.STDOUT)


def dependencies(binary):
    result = []
    for line in run(['otool', '-L', binary]).splitlines()[1:]:
        value = line.strip().split(' (compatibility version', 1)[0]
        if value.startswith(('/System/', '/usr/lib/')):
            continue
        if not value.startswith('/'):
            raise ValueError('macOS development closure requires absolute non-system dependencies: ' + value)
        result.append(Path(value).resolve())
    return result


def stage(source, destination, expected_dependencies=None):
    """Accept Homebrew-style absolute dependency paths; fail on unresolved rpaths."""
    source, destination = Path(source), Path(destination)
    destination.mkdir()
    pending = [source / 'ffmpeg', source / 'ffprobe']
    paths = {}
    while pending:
        binary = pending.pop().resolve()
        if binary in paths:
            continue
        target = destination / binary.name
        if target.exists():
            if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(binary.read_bytes()).digest():
                raise ValueError('Conflicting FFmpeg dependency name: ' + binary.name)
        else:
            shutil.copy2(binary, target)
            target.chmod(0o755)
        paths[binary] = target
        # otool includes a dylib's own install name; this is harmless in traversal.
        pending.extend(p for p in dependencies(binary) if p not in paths)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths if path.name not in ('ffmpeg', 'ffprobe')}
    if expected_dependencies is not None and hashes != expected_dependencies:
        raise ValueError('Native dependency SHA-256 set differs from the reviewed vendor lock')
    (destination / 'dependency-sha256.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    for original, target in paths.items():
        changes = []
        for line in run(['otool', '-L', original]).splitlines()[1:]:
            name = line.strip().split(' (compatibility version', 1)[0]
            if name.startswith(('/System/', '/usr/lib/')):
                continue
            changes += ['-change', name, '@loader_path/' + Path(name).resolve().name]
        if target.suffix == '.dylib':
            changes += ['-id', '@loader_path/' + target.name]
        if changes:
            run(['install_name_tool', *changes, target])
        run(['xattr', '-c', target])
        run(['codesign', '--force', '--sign', '-', target])
    return destination
