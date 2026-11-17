"""Read/decode fixtures and record legacy analysis/export without changing it."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import motion_splitter as app


def main():
    fixtures = ROOT / "tests" / "fixtures" / "generated"
    output = ROOT / "tests" / "output"
    output.mkdir(exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    results = []
    for item in json.loads((fixtures / "manifest.json").read_text()):
        path = fixtures / item["file"]
        cap = cv2.VideoCapture(str(path))
        count = 0
        try:
            assert cap.isOpened(), path
            assert round(cap.get(cv2.CAP_PROP_FPS)) == item["fps"]
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                assert frame.shape[:2] == (item["height"], item["width"])
                count += 1
        finally:
            cap.release()
        assert count == item["frames"], (path, count)
        decoded = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path),
                                  "-f", "null", "-"], capture_output=True, text=True, check=True)
        assert ("Audio:" in decoded.stderr) == item["audio"], path
        segments = app.analyze_video(path, 1.0, 0.5, 1.0, "Оригинал", 0, merge_gap=1.0)
        auto = app.calculate_auto_sensitivity(path, "Оригинал", 0)
        results.append({"file": path.name, "decoded_frames": count,
                        "audio": item["audio"], "segments": segments,
                        "auto_sensitivity": float(auto)})
    # Export only generated copies, never arbitrary user inputs.
    for name in ("static.mp4", "motion_with_audio.mp4"):
        target = output / name
        shutil.copy2(fixtures / name, target)
        files = ([app.save_full_video(target, ffmpeg)] if name == "static.mp4"
                 else app.save_segments(target, [(1.0, 4.0)], ffmpeg))
        for file in files:
            subprocess.run([ffmpeg, "-v", "error", "-i", file, "-f", "null", "-"],
                           capture_output=True, check=True)
    (output / "baseline.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("Fixture decode and export checks passed. Segment values are observations, not correctness assertions.")


if __name__ == "__main__":
    main()
