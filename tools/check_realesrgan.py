"""Optional actual model/GPU smoke test; requires an installed upstream bundle."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realesrgan_config import MODELS, preflight
from external_tools import ProcessRunner
from output_safety import create_run_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', help='Path to realesrgan-ncnn-vulkan with models/ beside it')
    parser.add_argument('--model', choices=MODELS, default='realesrgan-x4plus')
    parser.add_argument('--scale', type=int, default=4)
    parser.add_argument('--tile', type=int, default=0)
    parser.add_argument('--gpu', type=int, default=0)
    args = parser.parse_args()
    directory = create_run_directory(Path.cwd())
    print('Logs:', directory / 'logs')
    preflight(args.executable, args.model, args.scale, args.tile, args.gpu,
              ProcessRunner(directory / 'logs'))
    print('Model/GPU smoke test passed; this does not certify long video processing.')


if __name__ == '__main__':
    main()
