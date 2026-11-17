import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings_store as store
import gui as module


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motion-settings-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

    def test_defaults_are_independent(self):
        first = store.load(self.path)
        first["presets"]["new"] = {}
        self.assertEqual(store.load(self.path)["presets"], {})
        self.assertEqual(store.DEFAULTS["presets"], {})

    def test_legacy_settings_and_partial_preset_migrate(self):
        self.path.write_text(json.dumps({"sensitivity": 854 * 480,
                                        "presets": {"old": {"sensitivity": 854 * 480 * 2}},
                                        "current_preset": "old"}))
        settings = store.load(self.path)
        self.assertEqual(settings["schema_version"], store.SCHEMA_VERSION)
        self.assertEqual(settings["sensitivity"], 1)
        self.assertEqual(settings["presets"]["old"]["sensitivity"], 2)
        self.assertEqual(settings["presets"]["old"]["merge_gap_sec"], 1)
        store.save(self.path, settings)
        self.assertEqual(store.load(self.path), settings)

    def test_invalid_numeric_inputs_are_rejected(self):
        for key, value in (("sensitivity", "nan"), ("min_motion_sec", "inf"),
                           ("min_pause_sec", -1), ("skip_frames", "2.5"),
                           ("skip_frames", True), ("realesrgan_scale", 0),
                           ("realesrgan_tile", -2), ("realesrgan_gpuid", -1),
                           ("auto_scale_factor", 0), ("merge_gap_sec", "")):
            with self.subTest(key=key, value=value), self.assertRaises(store.SettingsError):
                store.normalize({"schema_version": 1, key: value})

    def test_invalid_enums_and_boolean_rejected(self):
        for key, value in (("analysis_resolution", "0x0"), ("dark_theme", "false"),
                           ("realesrgan_model", "missing-model")):
            with self.subTest(key=key), self.assertRaises(store.SettingsError):
                store.normalize({key: value})

    def test_corrupt_json_backed_up_byte_for_byte(self):
        contents = b'{invalid json\xff'
        self.path.write_bytes(contents)
        warnings = []
        self.assertEqual(store.load(self.path, warnings), store.DEFAULTS)
        self.assertTrue(warnings)
        copies = list(self.path.parent.glob("*.bak"))
        self.assertEqual(copies[0].read_bytes(), contents)
        self.assertEqual(self.path.read_bytes(), contents)
        store.save(self.path, copy.deepcopy(store.DEFAULTS))
        self.assertEqual(store.load(self.path), store.DEFAULTS)

    def test_invalid_fields_salvaged_without_discarding_good_values(self):
        self.path.write_text(json.dumps({"skip_frames": -10, "min_pause_sec": 3,
                                        "presets": {"good": {"sensitivity": 2}, "bad": []},
                                        "current_preset": "missing"}))
        warnings = []
        settings = store.load(self.path, warnings)
        self.assertEqual(settings["skip_frames"], 0)
        self.assertEqual(settings["min_pause_sec"], 3)
        self.assertEqual(settings["presets"]["good"]["sensitivity"], 2)
        self.assertNotIn("bad", settings["presets"])
        self.assertIsNone(settings["current_preset"])
        self.assertTrue(list(self.path.parent.glob("*.bak")))

    def test_invalid_root_can_be_replaced_after_recovery(self):
        self.path.write_text("[]")
        recovered = store.load(self.path)
        store.save(self.path, recovered)
        self.assertEqual(store.load(self.path), store.DEFAULTS)

    def test_future_version_never_overwritten(self):
        contents = '{"schema_version": 99}'
        self.path.write_text(contents)
        with self.assertRaises(store.SettingsError):
            store.load(self.path)
        with self.assertRaises(store.SettingsError):
            store.save(self.path, copy.deepcopy(store.DEFAULTS))
        self.assertEqual(self.path.read_text(), contents)

    def test_atomic_replace_failure_preserves_previous_file(self):
        original = store.save(self.path, copy.deepcopy(store.DEFAULTS))
        original_bytes = self.path.read_bytes()
        changed = copy.deepcopy(original)
        changed["sensitivity"] = 3
        with patch("settings_store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                store.save(self.path, changed)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_failed_validation_does_not_touch_file(self):
        store.save(self.path, copy.deepcopy(store.DEFAULTS))
        original = self.path.read_bytes()
        with self.assertRaises(store.SettingsError):
            store.save(self.path, {"sensitivity": float("nan")})
        self.assertEqual(self.path.read_bytes(), original)

    def test_in_memory_commit_only_after_successful_save(self):
        app = module.App.__new__(module.App)
        app.settings = copy.deepcopy(store.DEFAULTS)
        changed = copy.deepcopy(app.settings)
        changed["sensitivity"] = 3
        with patch.object(module, "SETTINGS_FILE", self.path):
            app.persist_settings(changed)
            self.assertEqual(app.settings["sensitivity"], 3)
            changed["sensitivity"] = 4
            with patch("settings_store.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    app.persist_settings(changed)
            self.assertEqual(app.settings["sensitivity"], 3)
            self.assertEqual(store.load(self.path)["sensitivity"], 3)


if __name__ == "__main__":
    unittest.main()
