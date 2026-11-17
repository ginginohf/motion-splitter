"""Optional real-Tk smoke check; requires a graphical desktop session."""
import copy
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gui as module
from output_safety import staged_output
from task_ui import TaskState


def main():
    with tempfile.TemporaryDirectory(prefix="motion-gui-test-") as temp:
        directory = Path(temp)
        source = directory / "source.mp4"
        shutil.copy2(ROOT / "tests/fixtures/generated/motion_middle.mp4", source)
        root = module.tk.Tk()
        root.withdraw()
        errors = []
        phases = []

        def callback_exception(kind, error, traceback):
            errors.append(error)
            root.quit()
        root.report_callback_exception = callback_exception

        def fake_enhance(input_path, output_path, *args, ui_log=None, **kwargs):
            if ui_log:
                ui_log("Проверка сообщения из потока улучшения")
            with staged_output(input_path, output_path) as target:
                shutil.copy2(input_path, target)
            return True

        try:
            with patch.object(module, "load_settings", return_value=copy.deepcopy(module.DEFAULTS)), \
                 patch.object(module, "SETTINGS_FILE", directory / "settings.json"), \
                 patch.object(module.processing, "find_ffmpeg", return_value=imageio_ffmpeg.get_ffmpeg_exe()), \
                 patch.object(module.processing, "find_realesrgan", return_value=sys.executable), \
                 patch.object(module.processing, "enhance_video_fallback_frames", side_effect=fake_enhance):
                app = module.App(root)
                app.source_var.set(str(source))
                app.enh_source_var.set(str(source))

                def check():
                    if app.processing:
                        assert str(app.btn_start["state"]) == "disabled"
                        assert str(app.btn_enh_start["state"]) == "disabled"
                        root.after(20, check)
                        return
                    assert app.tasks.state == TaskState.COMPLETED, app.tasks.state
                    assert str(app.btn_start["state"]) == "normal"
                    assert str(app.btn_enh_start["state"]) == "normal"
                    phases.append(app.tasks.mode)
                    if len(phases) == 1:
                        app.start_enhance_only()
                        root.after(20, check)
                    else:
                        root.quit()

                def timeout():
                    errors.append(RuntimeError("GUI smoke check exceeded 10 seconds"))
                    root.quit()
                root.after(10000, timeout)
                app.start()
                # Second tab cannot start concurrently, even via direct callback.
                app.start_enhance_only()
                assert app.tasks.mode == "split"
                root.after(20, check)
                root.mainloop()
                if errors:
                    raise errors[0]
                assert phases == ["split", "enhance"], phases
                assert len(list(directory.glob("motion_splitter_run_*"))) == 2
                print("Real Tk event loop: split + simulated enhancement passed; no callback errors.")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()
