# Run through tools/build_installer.py; native builds only.
import os
import sys
from pathlib import Path
root = Path(SPECPATH).parent
vendor = Path(os.environ['MOTION_SPLITTER_VENDOR']).resolve()
version = os.environ['MOTION_SPLITTER_VERSION']
suffix = '.exe' if sys.platform == 'win32' else ''
# Keep external FFmpeg's libraries separate from OpenCV's libavcodec, etc.
mac_tools = os.environ.get('MOTION_SPLITTER_MAC_TOOLS')
tool_binaries = [] if mac_tools else [(str(vendor / (name + suffix)), 'vendor') for name in ('ffmpeg', 'ffprobe')]
a = Analysis([str(root / 'motion_splitter.py')], pathex=[str(root)],
    binaries=tool_binaries,
    datas=[(str(root / 'LICENSE'), '.'), (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
           (str(root / 'assets'), 'assets'), (str(vendor / 'notices'), 'notices'), (str(vendor / 'manifest.json'), 'vendor')],
    hiddenimports=[], hookspath=[], excludes=['pytest', 'unittest'], noarchive=False)
if mac_tools:
    # Add AFTER Analysis: prevent its binary reclassification/flattening of the isolated closure.
    a.datas += [('vendor/' + p.name, str(p), 'DATA') for p in Path(mac_tools).iterdir() if p.is_file()]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MotionSplitter',
    debug=False, strip=False, upx=False, console=False,
    icon=str(root / 'assets' / 'icon.ico') if sys.platform == 'win32' else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MotionSplitter')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='MotionSplitter.app', bundle_identifier='org.motion-splitter.app',
                 version=version, icon=str(root / 'assets' / 'icon.icns'), info_plist={'NSHighResolutionCapable': True})
