"""Close a real Tk window while a real FFmpeg process is running."""
import copy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import external_tools
import motion_splitter as module
from task_ui import TaskState


def main():
    with tempfile.TemporaryDirectory(prefix="motion-close-test-") as temp:
        root = module.tk.Tk()
        root.withdraw()
        errors, processes = [], []
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        original_popen = external_tools.subprocess.Popen

        def track_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        def callback_error(kind, error, traceback):
            errors.append(error)
            root.quit()
        root.report_callback_exception = callback_error

        def worker(context):
            runner = external_tools.ProcessRunner(Path(temp) / "logs", context.stop_event)
            runner.run([ffmpeg, "-re", "-f", "lavfi", "-i",
                        "color=size=64x64:rate=10", "-t", "30", "-f", "null", "-"],
                       "Проверка закрытия")
            context.ui_queue.put(("done", 1))

        app = None
        try:
            with patch.object(module, "load_settings", return_value=copy.deepcopy(module.DEFAULTS)), \
                 patch.object(external_tools.subprocess, "Popen", side_effect=track_process):
                app = module.App(root)

                def close_when_running():
                    if not processes:
                        root.after(20, close_when_running)
                        return
                    assert processes[0].poll() is None
                    app.request_close()

                def timeout():
                    errors.append(RuntimeError("Close check exceeded 10 seconds"))
                    app.stop_event.set()
                    root.quit()
                root.after(10000, timeout)
                app.launch_task("enhance", worker, ())
                root.after(100, close_when_running)
                root.mainloop()
                if errors:
                    raise errors[0]
                assert app.tasks.state == TaskState.CANCELLED
                assert app.worker_thread is None
                assert len(processes) == 1 and processes[0].poll() is not None
                print("Tk close: FFmpeg stopped and reaped; worker exited before window destruction.")
        finally:
            if app is not None and app.worker_thread is not None:
                app.stop_event.set()
                app.worker_thread.join(timeout=5)
            try:
                root.destroy()
            except module.tk.TclError:
                pass


if __name__ == "__main__":
    main()
