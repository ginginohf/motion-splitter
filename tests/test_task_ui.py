import threading
import unittest
from unittest.mock import patch

from motion_splitter import App
from task_ui import TaskController, TaskState, UIEvents


class Widget:
    """A Tk-like test double that fails on any foreign-thread access."""
    def __init__(self):
        self.owner = threading.get_ident()
        self.values = {}

    def config(self, **kwargs):
        if threading.get_ident() != self.owner:
            raise AssertionError("worker touched widget")
        self.values.update(kwargs)

    configure = config

    def __setitem__(self, key, value):
        self.config(**{key: value})


def make_app():
    app = App.__new__(App)
    app.tasks = TaskController()
    app.ui_queue = UIEvents()
    app.stop_event = threading.Event()
    app.worker_thread = None
    app.pending_terminal = None
    app.preview_image = None
    for name in ("btn_start", "btn_enh_start", "btn_save", "btn_stop", "status_label",
                 "preview_label", "frame_info", "pb_enh", "pb_total", "pb_file",
                 "lbl_enh", "lbl_total", "lbl_file"):
        setattr(app, name, Widget())
    app.messages = []
    app.scheduled = []
    app.root = type("Root", (), {"after": lambda _, delay, fn: app.scheduled.append((delay, fn))})()

    def log(message):
        app.tasks.assert_owner()
        app.messages.append(message)
    app.log_message = log
    app.log_message_enh = log
    app.update_preview = lambda *args: app.tasks.assert_owner()
    app.refresh_task_controls()
    return app


class TaskUITests(unittest.TestCase):
    def test_progress_and_preview_memory_is_bounded(self):
        events = UIEvents(log_limit=8)
        for n in range(10000):
            events.put(("file_progress", (n, 10000)))
            events.put(("preview_frame", (n, n, n)))
            events.put(("info", str(n)))
        events.put(("error", "final error"))
        messages = events.drain()
        self.assertLessEqual(len(messages), 12)
        self.assertEqual([m for m in messages if m[0] == "file_progress"],
                         [("file_progress", (9999, 10000))])
        self.assertEqual([m for m in messages if m[0] == "preview_frame"],
                         [("preview_frame", (9999, 9999, 9999))])
        self.assertEqual(messages[-1], ("error", "final error"))
        events.put(("info", "too late"))
        self.assertEqual(events.drain(), [])

    def test_file_transition_discards_stale_frame_and_progress(self):
        events = UIEvents()
        events.put(("file_name", "old.mp4"))
        events.put(("file_progress", (99, 100)))
        events.put(("preview_frame", ("old frame", 99, 0)))
        events.put(("file_name", "new.mp4"))
        self.assertEqual(events.drain(), [("file_name", "new.mp4")])

    def test_poll_budget_and_terminal_delivery(self):
        events = UIEvents(log_limit=60)
        for i in range(100):
            events.put(("info", str(i)))
        events.put(("done", 1))
        first = events.drain(50)
        self.assertEqual(len(first), 50)
        self.assertNotIn(("done", 1), first)
        self.assertEqual(events.drain(50)[-1], ("done", 1))

    def test_state_can_only_change_on_owner_thread(self):
        state = TaskController()
        errors = []

        def worker():
            try:
                state.begin("split")
            except RuntimeError as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(len(errors), 1)
        self.assertEqual(state.state, TaskState.IDLE)

    def test_buttons_stay_disabled_until_worker_exits(self):
        app = make_app()
        sent = threading.Event()
        release = threading.Event()

        def worker(context):
            context.ui_queue.put(("done", 1))
            sent.set()
            release.wait(2)
        try:
            app.launch_task("split", worker, ())
            self.assertTrue(sent.wait(2))
            app.poll_queue()
            self.assertEqual(app.tasks.state, TaskState.RUNNING)
            self.assertEqual(app.btn_enh_start.values["state"], "disabled")
            self.assertEqual(app.btn_start.values["state"], "disabled")
            old_thread = app.worker_thread
            app.launch_task("enhance", worker, ())
            self.assertIs(app.worker_thread, old_thread)
            # Public entry points also reject busy starts before reading any Tk vars.
            app.start()
            app.start_enhance_only()
        finally:
            release.set()
            app.worker_thread.join(timeout=2)
        app.poll_queue()
        self.assertEqual(app.tasks.state, TaskState.COMPLETED)
        self.assertEqual(app.btn_start.values["state"], "normal")
        self.assertEqual(app.btn_enh_start.values["state"], "normal")

    def test_cancel_does_not_claim_completion_before_exit(self):
        app = make_app()
        release = threading.Event()

        def worker(context):
            release.wait(2)
            context.ui_queue.put(("cancelled", 0))
        app.launch_task("split", worker, ())
        app.stop_processing()
        self.assertEqual(app.tasks.state, TaskState.CANCELLING)
        self.assertTrue(app.stop_event.is_set())
        self.assertEqual(app.btn_stop.values["state"], "disabled")
        release.set()
        app.worker_thread.join(timeout=2)
        app.poll_queue()
        self.assertEqual(app.tasks.state, TaskState.CANCELLED)

    def test_exception_and_restart_use_independent_event_buffers(self):
        app = make_app()

        def failed(context):
            raise ValueError("worker failure")
        app.launch_task("enhance", failed, ())
        app.worker_thread.join(timeout=2)
        previous = app.ui_queue
        app.poll_queue()
        self.assertEqual(app.tasks.state, TaskState.FAILED)
        self.assertIn("worker failure", app.messages)
        app.launch_task("split", lambda ctx: ctx.ui_queue.put(("done", 2)), ())
        app.worker_thread.join(timeout=2)
        previous.put(("error", "stale"))
        app.poll_queue()
        self.assertEqual(app.tasks.state, TaskState.COMPLETED)
        self.assertNotIn("stale", app.messages)

    def test_thread_start_failure_releases_buttons(self):
        app = make_app()
        with patch("motion_splitter.threading.Thread.start", side_effect=RuntimeError("no thread")):
            app.launch_task("split", lambda ctx: None, ())
        app.poll_queue()
        self.assertEqual(app.tasks.state, TaskState.FAILED)
        self.assertEqual(app.btn_start.values["state"], "normal")

    def test_enhancement_events_only_touch_widgets_in_poll(self):
        app = make_app()

        def worker(context):
            context.ui_queue.put(("total_init", 2))
            context.ui_queue.put(("info", "enhancement progress"))
            context.ui_queue.put(("total_progress", (2, 2)))
            context.ui_queue.put(("done", 2))
        app.launch_task("enhance", worker, ())
        app.worker_thread.join(timeout=2)
        self.assertEqual(app.messages, [])
        app.poll_queue()
        self.assertEqual(app.pb_enh.values["value"], 2)
        self.assertEqual(app.pb_total.values, {})
        self.assertIn("enhancement progress", app.messages)
        self.assertEqual(app.tasks.state, TaskState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
