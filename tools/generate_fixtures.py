"""Create small synthetic, non-personal videos with known motion intervals."""
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "fixtures" / "generated"
WIDTH, HEIGHT, FPS, SECONDS = 320, 180, 25, 6


def generate(name, intervals, audio=False):
    path = OUTPUT / name
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
           "-n", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
           "-r", str(FPS), "-i", "pipe:0"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={SECONDS}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    if path.exists():
        raise FileExistsError(f"Use a fresh generated directory; refusing to overwrite {path}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(FPS * SECONDS):
            t = i / FPS
            frame = bytearray([24]) * (WIDTH * HEIGHT * 3)
            if any(start <= t < end for start, end in intervals):
                x = (i * 7) % (WIDTH - 96)
                stripe = bytes([235]) * (96 * 3)
                for y in range(50, 130):
                    offset = (y * WIDTH + x) * 3
                    frame[offset:offset + len(stripe)] = stripe
            proc.stdin.write(frame)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"FFmpeg failed: {name}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
    return {"file": name, "duration_sec": SECONDS, "fps": FPS,
            "frames": FPS * SECONDS, "width": WIDTH, "height": HEIGHT,
            "motion_intervals_sec": intervals, "audio": audio}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = [generate("static.mp4", []),
                generate("motion_middle.mp4", [(1, 3)]),
                generate("motion_to_end.mp4", [(2, 6)]),
                generate("motion_with_audio.mp4", [(1, 4)], audio=True)]
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Created {len(manifest)} videos in {OUTPUT}")


if __name__ == "__main__":
    main()
