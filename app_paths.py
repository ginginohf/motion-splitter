"""Locations independent of working directory; never create folders on import."""
import os
import sys
from pathlib import Path


def bundled_tool(name):
    if not getattr(sys, "frozen", False):
        return None
    base = Path(sys._MEIPASS)
    return base / "vendor" / (name + (".exe" if os.name == "nt" else ""))


def user_models_directory():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        value = os.environ.get("XDG_DATA_HOME", "")
        base = Path(value) if value and Path(value).is_absolute() else Path.home() / ".local" / "share"
    return base / "MotionSplitter" / "models"


def model_directory(executable, custom=""):
    if custom:
        return Path(custom).expanduser().resolve()
    user = user_models_directory()
    # Existing source installations keep working, without writing to the app.
    legacy = Path(executable).resolve().parent / "models"
    return user if user.is_dir() or not legacy.is_dir() else legacy
