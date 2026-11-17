"""Compatibility API and desktop entry point. GUI is imported only on demand."""
from processing import (run_split, run_enhance, parse_resolution, get_video_duration,
                        calculate_auto_sensitivity, merge_close_segments, analyze_video,
                        save_segments, save_full_video, enhance_video_with_realesrgan,
                        enhance_video_fallback_frames)
from job_contracts import TaskContext, JobResult


def main():
    from gui import launch
    launch()


def __getattr__(name):
    if name == "App":
        from gui import App
        return App
    raise AttributeError(name)


if __name__ == "__main__":
    main()
