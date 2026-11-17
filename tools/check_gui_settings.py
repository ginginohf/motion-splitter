"""Real Tk settings/preset smoke check using a private settings file."""
import copy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import motion_splitter as module
import settings_store as store


def main():
    with tempfile.TemporaryDirectory(prefix="motion-settings-gui-") as temp:
        path = Path(temp) / "settings.json"
        settings = copy.deepcopy(store.DEFAULTS)
        settings["presets"]["old"] = {"sensitivity": 1}
        settings["current_preset"] = "old"
        settings["sensitivity"] = 3
        store.save(path, settings)
        root = module.tk.Tk()
        root.withdraw()
        try:
            with patch.object(module, "SETTINGS_FILE", path):
                app = module.App(root)
                assert float(app.sens_var.get()) == 3, "startup must not reapply stale preset"
                assert app.enh_tile_var is app.realesrgan_tile_var
                app.notebook.select(app.tab_enhance)
                app.enh_tile_var.set("200")
                app.save_current()
                assert "успешно сохранены" in app.log_enh.get("1.0", "end")
                assert store.load(path)["realesrgan_tile"] == 200
                app.sens_var.set("2.5")
                app.toggle_theme()
                assert store.load(path)["sensitivity"] == 2.5
                assert store.load(path)["dark_theme"] == app.dark_theme
                with patch.object(module.simpledialog, "askstring", return_value="new"):
                    app.save_preset()
                assert app.settings["presets"]["new"]["realesrgan_tile"] == 200
                app.enh_tile_var.set("400")
                app.preset_var.set("new")
                app.apply_preset()
                assert int(app.enh_tile_var.get()) == 200
                before = path.read_bytes()
                app.sens_var.set("nan")
                with patch.object(module.simpledialog, "askstring", return_value="invalid"), \
                     patch.object(app, "launch_task") as launch:
                    app.save_preset()
                    app.enh_source_var.set(str(ROOT / "tests/fixtures/generated/static.mp4"))
                    app.start_enhance_only()
                    launch.assert_not_called()
                assert path.read_bytes() == before
                assert "invalid" not in app.settings["presets"]
                app.sens_var.set("2.5")
                with patch.object(module.messagebox, "askyesno", return_value=True):
                    app.delete_preset()
                assert "new" not in store.load(path)["presets"]
                assert store.load(path)["current_preset"] is None
                root.update()
                print("Tk settings: shared fields, save, theme, presets, validation and startup restoration passed.")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()
