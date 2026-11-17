"""Launch with development FFmpeg; FFprobe must be installed separately."""
import runpy
import os
import shutil
import sys
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT))
    bin_dir = ROOT / "tools" / "bin"
    bin_dir.mkdir(exist_ok=True)
    target = bin_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not target.exists():
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), target)
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    runpy.run_path(str(ROOT / "motion_splitter.py"), run_name="__main__")


if __name__ == "__main__":
    main()
