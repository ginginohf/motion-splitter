"""Versioned settings, validation, recovery and atomic persistence."""
import copy
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path

SCHEMA_VERSION = 2
DEFAULTS = {
    "sensitivity": 1.0,
    "min_pause_sec": 0.5,
    "min_motion_sec": 1.0,
    "analysis_resolution": "Оригинал",
    "skip_frames": 0,
    "save_full_if_no_motion": True,
    "presets": {},
    "current_preset": None,
    "auto_sensitivity": False,
    "auto_scale_factor": 0.5,
    "dark_theme": True,
    "last_source_type": "folder",
    "merge_gap_sec": 1.0,
    "smoothing_sec": 0.2,
    "pre_motion_sec": 0.0,
    "post_motion_sec": 0.0,

    # --- Новые настройки Real-ESRGAN ---
    "enhance_after_split": False,            # апскейлить сегменты после нарезки
    "realesrgan_path": "",                   # путь к realesrgan-ncnn-vulkan(.exe)
    "realesrgan_model": "realesr-general-x4v3",
    "realesrgan_scale": 4,                   # 2 или 4, зависит от модели
    "realesrgan_tile": 0,                    # 0 = авто (без тайлинга), иначе например 200
    "realesrgan_gpuid": 0                    # id GPU (для ncnn-vulkan)
}

DEFAULTS["schema_version"] = SCHEMA_VERSION

FIELDS = {k: v for k, v in DEFAULTS.items() if k not in ("presets", "current_preset", "schema_version")}
NUMBERS = {
    "smoothing_sec": ("Сглаживание", 0, 10, False),
    "pre_motion_sec": ("Запас до движения", 0, 3600, False),
    "post_motion_sec": ("Запас после движения", 0, 3600, False),
    "sensitivity": ("Порог движения", 0, 255, False),
    "min_pause_sec": ("Минимальная пауза", 0, 86400, False),
    "min_motion_sec": ("Минимальное движение", 0, 86400, False),
    "merge_gap_sec": ("Объединение сегментов", 0, 86400, False),
    "auto_scale_factor": ("Коэффициент автонастройки", .01, 100, False),
    "skip_frames": ("Пропуск кадров", 0, 10000, True),
    "realesrgan_scale": ("Масштаб", 2, 4, True),
    "realesrgan_tile": ("Размер тайла", 0, 8192, True),
    "realesrgan_gpuid": ("GPU ID", 0, 255, True),
}
CHOICES = {
    "analysis_resolution": ("Оригинал", "640x360", "854x480", "1280x720", "1920x1080"),
    "last_source_type": ("folder", "file"),
    "realesrgan_model": ("realesr-general-x4v3", "realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3"),
}


class SettingsError(ValueError):
    pass


def validate_field(key, value):
    if key in NUMBERS:
        label, low, high, integer = NUMBERS[key]
        try:
            if isinstance(value, bool):
                raise ValueError()
            if integer and not re.fullmatch(r"[+-]?\d+", str(value).strip()):
                raise ValueError()
            number = int(value) if integer else float(value)
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise SettingsError(f"{label}: требуется {'целое ' if integer else ''}число от {low} до {high}.")
        return number
    if isinstance(FIELDS[key], bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{key}: требуется логическое значение.")
    elif not isinstance(value, str):
        raise SettingsError(f"{key}: требуется строка.")
    if key in CHOICES and value not in CHOICES[key]:
        raise SettingsError(f"{key}: допустимо {', '.join(CHOICES[key])}.")
    if key == "realesrgan_path":
        if "\x00" in value:
            raise SettingsError("Путь Real-ESRGAN содержит недопустимый символ.")
        value = value.strip()
    return value


def normalize(data, recover=False, warnings=None):
    warnings = warnings if warnings is not None else []
    if not isinstance(data, dict):
        raise SettingsError("Настройки должны быть JSON-объектом.")
    version = data.get("schema_version", 0)
    if type(version) is not int or version < 0 or version > SCHEMA_VERSION:
        raise SettingsError(f"Неподдерживаемая версия настроек: {version}. Файл не изменён.")

    def fields(raw, prefix=""):
        result = {}
        for key, default in FIELDS.items():
            value = raw.get(key, copy.deepcopy(default))
            # Preserve the legacy conversion, once, only for unversioned data.
            if version == 0 and key == "sensitivity" and type(value) in (int, float) and value > 255:
                value /= 854 * 480
            try:
                result[key] = validate_field(key, value)
            except SettingsError as error:
                if not recover:
                    raise
                result[key] = copy.deepcopy(default)
                warnings.append(f"{prefix}{error} Использовано значение по умолчанию.")
        return result

    result = fields(data)
    result.update(schema_version=SCHEMA_VERSION, presets={}, current_preset=None)
    presets = data.get("presets", {})
    if not isinstance(presets, dict):
        if not recover:
            raise SettingsError("presets: требуется объект.")
        warnings.append("Повреждён список пресетов; использован пустой список.")
        presets = {}
    for name, preset in presets.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or not isinstance(preset, dict):
            if not recover:
                raise SettingsError("Некорректное имя или содержимое пресета.")
            warnings.append(f"Пропущен повреждённый пресет: {name!r}.")
            continue
        result["presets"][name] = fields(preset, f"Пресет {name}: ")
    current = data.get("current_preset")
    if isinstance(current, str) and current in result["presets"]:
        result["current_preset"] = current
    elif current is not None:
        if not recover:
            raise SettingsError("Выбранный пресет отсутствует.")
        warnings.append("Ссылка на отсутствующий пресет сброшена.")
    return result


def backup(path, contents):
    target = path.with_name(path.name + ".recovery-" + uuid.uuid4().hex + ".bak")
    with target.open("xb") as stream:
        stream.write(contents)
    return target


def load(path, warnings=None):
    path = Path(path)
    warnings = warnings if warnings is not None else []
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    contents = path.read_bytes()
    repairs = []
    try:
        raw = json.loads(contents)
        # Future versions must remain untouched, not be mistaken for corruption.
        if isinstance(raw, dict) and isinstance(raw.get("schema_version"), int) and raw["schema_version"] > SCHEMA_VERSION:
            raise SettingsError("Файл создан более новой версией программы. Обновите программу; файл не изменён.")
        try:
            result = normalize(raw, recover=True, warnings=repairs)
        except SettingsError as error:
            repairs.append(str(error))
            result = copy.deepcopy(DEFAULTS)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        repairs.append(f"Не удалось прочитать JSON: {error}")
        result = copy.deepcopy(DEFAULTS)
    if repairs:
        saved = backup(path, contents)
        warnings.extend(repairs + [f"Исходный файл сохранён для восстановления: {saved}"])
    return result


def save(path, settings):
    path = Path(path)
    validated = normalize(settings)
    contents = (json.dumps(validated, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if path.exists():
        old = path.read_bytes()
        try:
            raw = json.loads(old)
        except (json.JSONDecodeError, UnicodeDecodeError):
            backup(path, old)
        else:
            if isinstance(raw, dict) and isinstance(raw.get("schema_version"), int) and raw["schema_version"] > SCHEMA_VERSION:
                raise SettingsError("Нельзя перезаписать настройки более новой версии.")
            repairs = []
            try:
                normalize(raw, recover=True, warnings=repairs)
            except SettingsError as error:
                repairs.append(str(error))
            if repairs:
                backup(path, old)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return validated
