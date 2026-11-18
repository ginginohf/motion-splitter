from realesrgan_config import MODELS
import cv2
import os
import sys
import copy
import settings_store
from settings_store import DEFAULTS, FIELDS
import threading
from job_contracts import TaskContext
import processing
from video_export import VIDEO_EXTS
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog, scrolledtext
from pathlib import Path
from PIL import Image, ImageTk
from task_ui import TaskController, TaskState, UIEvents
from external_tools import TaskCancelled
from output_safety import collect_folder_videos

# ---------------- Settings ----------------
SETTINGS_FILE = Path.home() / ".motion_splitter_settings.json"

SETTINGS_WARNINGS = []
SETTING_VARS = {
    "enhance_disk_limit_gb": "disk_limit_var", "enhance_disk_reserve_gb": "disk_reserve_var",
    "export_mode": "export_mode_var",
    "smoothing_sec": "smoothing_var", "pre_motion_sec": "pre_motion_var",
    "post_motion_sec": "post_motion_var",
    "sensitivity": "sens_var", "min_pause_sec": "pause_var", "min_motion_sec": "minmov_var",
    "analysis_resolution": "res_var", "skip_frames": "skip_var",
    "save_full_if_no_motion": "save_full_var", "auto_sensitivity": "auto_sens_var",
    "auto_scale_factor": "auto_scale_var", "merge_gap_sec": "merge_gap_var",
    "enhance_after_split": "enhance_after_split_var", "realesrgan_path": "realesrgan_path_var",
    "realesrgan_model": "realesrgan_model_var", "realesrgan_scale": "realesrgan_scale_var",
    "realesrgan_tile": "realesrgan_tile_var", "realesrgan_gpuid": "realesrgan_gpuid_var",
}


def load_settings():
    SETTINGS_WARNINGS.clear()
    return settings_store.load(SETTINGS_FILE, SETTINGS_WARNINGS)


def save_settings(settings):
    return settings_store.save(SETTINGS_FILE, settings)

class App:
    def __init__(self, root):
        self.root = root
        root.title("Motion Splitter")
        root.geometry("1500x700")

        self.settings = load_settings()
        self.dark_theme = self.settings.get("dark_theme", True)
        self.set_dark_theme(self.dark_theme)

        # ----- Notebook (вкладки) -----
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Вкладка 1: Нарезка (ваш текущий UI)
        self.tab_split = tk.Frame(self.notebook, padx=10, pady=10, bg=self.bg_color)
        self.notebook.add(self.tab_split, text="Нарезка")

        # Вкладка 2: Улучшение отдельно
        self.tab_enhance = tk.Frame(self.notebook, padx=10, pady=10, bg=self.bg_color)
        self.notebook.add(self.tab_enhance, text="Улучшение (Real-ESRGAN)")

        # ----- Содержимое вкладки «Нарезка» (копия вашего GUI) -----
        frm = self.tab_split
        tk.Label(frm, text="Источник:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky="w")
        self.source_var = tk.StringVar()
        tk.Entry(frm, textvariable=self.source_var, width=70, bg=self.entry_bg, fg=self.fg_color).grid(row=0, column=1, sticky="we", columnspan=3)

        self.btn_folder = tk.Button(frm, text="Папка", command=self.browse_folder, bg=self.button_bg, fg=self.fg_color)
        self.btn_folder.grid(row=0, column=4, padx=2)
        self.btn_file = tk.Button(frm, text="Файл", command=self.browse_file, bg=self.button_bg, fg=self.fg_color)
        self.btn_file.grid(row=0, column=5, padx=2)

        tk.Label(frm, text="Пресет настроек:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky="w")
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(frm, textvariable=self.preset_var, width=40, state="readonly")
        self.preset_combo.grid(row=1, column=1, sticky="we")
        tk.Button(frm, text="Сохранить пресет", command=self.save_preset, bg=self.button_bg, fg=self.fg_color).grid(row=1, column=2, padx=2)
        tk.Button(frm, text="Удалить пресет", command=self.delete_preset, bg=self.button_bg, fg=self.fg_color).grid(row=1, column=3, padx=2)
        self.update_presets_combo()
        self.preset_combo.bind("<<ComboboxSelected>>", self.apply_preset)

        tk.Label(frm, text="Порог движения (изменение яркости):", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=0, sticky="w")
        self.sens_var = tk.StringVar(value=str(self.settings["sensitivity"]))
        self.sens_entry = tk.Entry(frm, textvariable=self.sens_var, width=12, bg=self.entry_bg, fg=self.fg_color)
        self.sens_entry.grid(row=2, column=1, sticky="w")

        tk.Label(frm, text="Мин. пауза (сек):", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=2, sticky="w")
        self.pause_var = tk.StringVar(value=str(self.settings["min_pause_sec"]))
        tk.Entry(frm, textvariable=self.pause_var, width=12, bg=self.entry_bg, fg=self.fg_color).grid(row=2, column=3, sticky="w")

        tk.Label(frm, text="Мин. время движения (сек):", bg=self.bg_color, fg=self.fg_color).grid(row=3, column=0, sticky="w")
        self.minmov_var = tk.StringVar(value=str(self.settings["min_motion_sec"]))
        tk.Entry(frm, textvariable=self.minmov_var, width=12, bg=self.entry_bg, fg=self.fg_color).grid(row=3, column=1, sticky="w")

        tk.Label(frm, text="Макс. размер анализа:", bg=self.bg_color, fg=self.fg_color).grid(row=3, column=2, sticky="w")
        self.res_var = tk.StringVar(value=self.settings["analysis_resolution"])
        res_menu = tk.OptionMenu(frm, self.res_var, "Оригинал", "640x360", "854x480", "1280x720", "1920x1080")
        res_menu.config(bg=self.button_bg, fg=self.fg_color, highlightthickness=0)
        res_menu.grid(row=3, column=3, sticky="w")

        tk.Label(frm, text="Пропуск кадров:", bg=self.bg_color, fg=self.fg_color).grid(row=4, column=0, sticky="w")
        self.skip_var = tk.StringVar(value=str(self.settings["skip_frames"]))
        tk.Entry(frm, textvariable=self.skip_var, width=12, bg=self.entry_bg, fg=self.fg_color).grid(row=4, column=1, sticky="w")

        tk.Label(frm, text="Объединять сегменты (сек):", bg=self.bg_color, fg=self.fg_color).grid(row=4, column=2, sticky="w")
        self.merge_gap_var = tk.StringVar(value=str(self.settings.get("merge_gap_sec", 1.0)))
        tk.Entry(frm, textvariable=self.merge_gap_var, width=12, bg=self.entry_bg, fg=self.fg_color).grid(row=4, column=3, sticky="w")

        tk.Label(frm, text="Сохранять полное видео при отсутствии движения:", bg=self.bg_color, fg=self.fg_color).grid(row=4, column=4, sticky="w")
        self.save_full_var = tk.BooleanVar(value=self.settings.get("save_full_if_no_motion", True))
        self.save_full_cb = tk.Checkbutton(frm, variable=self.save_full_var, bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color,
                                           activebackground=self.bg_color, activeforeground=self.fg_color)
        self.save_full_cb.grid(row=4, column=5, sticky="w")

        tk.Label(frm, text="Автонастройка чувствительности:", bg=self.bg_color, fg=self.fg_color).grid(row=5, column=0, sticky="w")
        self.auto_sens_var = tk.BooleanVar(value=self.settings.get("auto_sensitivity", False))
        self.auto_sens_cb = tk.Checkbutton(frm, variable=self.auto_sens_var, command=self.toggle_auto_sensitivity,
                                           bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color,
                                           activebackground=self.bg_color, activeforeground=self.fg_color)
        self.auto_sens_cb.grid(row=5, column=1, sticky="w")

        tk.Label(frm, text="Коэффициент автонастройки:", bg=self.bg_color, fg=self.fg_color).grid(row=5, column=2, sticky="w")
        self.auto_scale_var = tk.StringVar(value=str(self.settings.get("auto_scale_factor", 0.5)))
        self.scale_entry = tk.Entry(frm, textvariable=self.auto_scale_var, width=8,
                                    state="normal" if self.auto_sens_var.get() else "disabled", bg=self.entry_bg, fg=self.fg_color)
        self.scale_entry.grid(row=5, column=3, sticky="w")
        tk.Label(frm, text="Меньше коэффициент — чувствительнее", bg=self.bg_color, fg=self.fg_color).grid(row=5, column=4, sticky="w")

        # --- Новое: настройки апскейла прямо во вкладке «Нарезка» ---
        self.export_mode_var = tk.StringVar(value=self.settings["export_mode"])
        export_options = tk.Frame(frm, bg=self.bg_color)
        export_options.grid(row=3, column=4, columnspan=2, sticky="w")
        tk.Radiobutton(export_options, text="Быстрый (границы по ключевым кадрам)",
                       variable=self.export_mode_var, value="fast", bg=self.bg_color,
                       fg=self.fg_color, selectcolor=self.bg_color).pack(anchor="w")
        tk.Radiobutton(export_options, text="Точный (MP4, H.264/AAC)",
                       variable=self.export_mode_var, value="accurate", bg=self.bg_color,
                       fg=self.fg_color, selectcolor=self.bg_color).pack(anchor="w")

        motion_options = tk.Frame(frm, bg=self.bg_color)
        motion_options.grid(row=6, column=0, columnspan=6, sticky="we", pady=8)
        for column, key, variable, label in (
            (0, "smoothing_sec", "smoothing_var", "Сглаживание, с (0=выкл.):"),
            (2, "pre_motion_sec", "pre_motion_var", "Запас до движения, с:"),
            (4, "post_motion_sec", "post_motion_var", "Запас после движения, с:")):
            value = tk.StringVar(value=str(self.settings[key]))
            setattr(self, variable, value)
            tk.Label(motion_options, text=label, bg=self.bg_color, fg=self.fg_color).grid(row=0, column=column)
            tk.Entry(motion_options, textvariable=value, width=8, bg=self.entry_bg,
                     fg=self.fg_color).grid(row=0, column=column+1, padx=(3, 15))
        tk.Label(frm, text="Меньше порог — больше чувствительность", bg=self.bg_color,
                 fg=self.fg_color).grid(row=2, column=4, columnspan=2, sticky="w")
        tk.Label(frm, text="Пропуск: 0 — все кадры; 1 — каждый второй. Больше пропуск — грубее границы; порог может потребовать настройки.",
                 wraplength=650, justify="left", bg=self.bg_color, fg=self.fg_color).grid(row=9, column=2, columnspan=4, sticky="w")

        self.enhance_after_split_var = tk.BooleanVar(value=self.settings.get("enhance_after_split", False))
        tk.Label(frm, text="Улучшать сегменты (Real-ESRGAN):", bg=self.bg_color, fg=self.fg_color).grid(row=7, column=0, sticky="w")
        tk.Checkbutton(frm, variable=self.enhance_after_split_var, bg=self.bg_color, fg=self.fg_color,
                       selectcolor=self.bg_color, activebackground=self.bg_color, activeforeground=self.fg_color)\
            .grid(row=7, column=1, sticky="w")

        tk.Label(frm, text="Путь к realesrgan-ncnn-vulkan:", bg=self.bg_color, fg=self.fg_color).grid(row=7, column=2, sticky="e")
        self.realesrgan_path_var = tk.StringVar(value=self.settings.get("realesrgan_path",""))
        tk.Entry(frm, textvariable=self.realesrgan_path_var, width=40, bg=self.entry_bg, fg=self.fg_color)\
            .grid(row=7, column=3, sticky="we", columnspan=2)
        tk.Button(frm, text="Обзор…", command=self.browse_realesrgan, bg=self.button_bg, fg=self.fg_color)\
            .grid(row=7, column=5, sticky="w", padx=2)

        tk.Label(frm, text="Модель:", bg=self.bg_color, fg=self.fg_color).grid(row=8, column=0, sticky="w")
        self.realesrgan_model_var = tk.StringVar(value=self.settings.get("realesrgan_model","realesrgan-x4plus"))
        self.model_combo = ttk.Combobox(frm, textvariable=self.realesrgan_model_var, width=28, state="readonly",
                                        values=list(MODELS))
        self.model_combo.grid(row=8, column=1, sticky="w")

        tk.Label(frm, text="Scale:", bg=self.bg_color, fg=self.fg_color).grid(row=8, column=2, sticky="e")
        self.realesrgan_scale_var = tk.StringVar(value=str(self.settings.get("realesrgan_scale",4)))
        tk.Entry(frm, textvariable=self.realesrgan_scale_var, width=6, bg=self.entry_bg, fg=self.fg_color)\
            .grid(row=8, column=3, sticky="w")

        tk.Label(frm, text="Tile (0=auto):", bg=self.bg_color, fg=self.fg_color).grid(row=8, column=4, sticky="e")
        self.realesrgan_tile_var = tk.StringVar(value=str(self.settings.get("realesrgan_tile",0)))
        tk.Entry(frm, textvariable=self.realesrgan_tile_var, width=6, bg=self.entry_bg, fg=self.fg_color)\
            .grid(row=8, column=5, sticky="w")

        tk.Label(frm, text="GPU ID:", bg=self.bg_color, fg=self.fg_color).grid(row=9, column=0, sticky="w")
        self.realesrgan_gpuid_var = tk.StringVar(value=str(self.settings.get("realesrgan_gpuid",0)))
        tk.Entry(frm, textvariable=self.realesrgan_gpuid_var, width=6, bg=self.entry_bg, fg=self.fg_color)\
            .grid(row=9, column=1, sticky="w")

        tk.Label(frm, text="x4plus: ×4; animevideov3: ×2/×3/×4; Tile: 0 или ≥32",
                 bg=self.bg_color, fg=self.fg_color).grid(row=9, column=2, columnspan=4, sticky="w")

        # Прогресс по одному файлу
        tk.Label(frm, text="Прогресс файла:", bg=self.bg_color, fg=self.fg_color).grid(row=10, column=0, sticky="w", pady=(8,2))
        self.pb_file = ttk.Progressbar(frm, length=360, mode="determinate")
        self.pb_file.grid(row=10, column=1, columnspan=5, sticky="we")
        self.lbl_file = tk.Label(frm, text="", bg=self.bg_color, fg=self.fg_color)
        self.lbl_file.grid(row=11, column=0, columnspan=6, sticky="w")

        # Общий прогресс
        tk.Label(frm, text="Общий прогресс:", bg=self.bg_color, fg=self.fg_color).grid(row=12, column=0, sticky="w", pady=(8,2))
        self.pb_total = ttk.Progressbar(frm, length=360, mode="determinate")
        self.pb_total.grid(row=12, column=1, columnspan=5, sticky="we")
        self.lbl_total = tk.Label(frm, text="", bg=self.bg_color, fg=self.fg_color)
        self.lbl_total.grid(row=13, column=0, columnspan=6, sticky="w")

        # Кнопки
        self.btn_frame = tk.Frame(frm, bg=self.bg_color)
        self.btn_frame.grid(row=14, column=0, columnspan=6, pady=10)
        self.btn_start = tk.Button(self.btn_frame, text="Запустить обработку", command=self.start, bg=self.button_bg, fg=self.fg_color)
        self.btn_start.grid(row=0, column=0, padx=5)
        self.btn_stop = tk.Button(self.btn_frame, text="Прервать", command=self.stop_processing, bg="#c0392b", fg="white", state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=5)
        self.btn_save = tk.Button(self.btn_frame, text="Сохранить настройки", command=self.save_current, bg=self.button_bg, fg=self.fg_color)
        self.btn_save.grid(row=0, column=2, padx=5)
        self.btn_theme = tk.Button(self.btn_frame, text="Сменить тему", command=self.toggle_theme, bg=self.button_bg, fg=self.fg_color)
        self.btn_theme.grid(row=0, column=3, padx=5)

        # Лог + предпросмотр (как было)
        tk.Label(frm, text="Журнал событий и предпросмотр:", bg=self.bg_color, fg=self.fg_color).grid(row=15, column=0, sticky="w", pady=(8,2))
        self.preview_frame = tk.Frame(frm, bg=self.bg_color)
        self.preview_frame.grid(row=16, column=0, columnspan=6, sticky="nsew")
        self.log_text = scrolledtext.ScrolledText(self.preview_frame, height=8, bg=self.text_bg, fg=self.fg_color)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text.config(state="disabled")
        self.preview_container = tk.Frame(self.preview_frame, bg="black", width=320, height=180)
        self.preview_container.pack(side=tk.RIGHT, padx=5, pady=5, fill=tk.BOTH, expand=False)
        self.preview_container.pack_propagate(False)
        self.preview_label = tk.Label(self.preview_container, bg="black")
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        self.frame_info = tk.Label(self.preview_container, text="Ожидание обработки...", bg="black", fg="white", anchor="sw")
        self.frame_info.pack(fill=tk.X, side=tk.BOTTOM)

        frm.grid_rowconfigure(16, weight=1)
        for c in (1,2,3,4,5): frm.grid_columnconfigure(c, weight=1)

        # ----- Содержимое вкладки «Улучшение (Real-ESRGAN)» -----
        fe = self.tab_enhance
        tk.Label(fe, text="Источник (файл или папка):", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky="w")
        self.enh_source_var = tk.StringVar()
        tk.Entry(fe, textvariable=self.enh_source_var, width=70, bg=self.entry_bg, fg=self.fg_color).grid(row=0, column=1, sticky="we", columnspan=3)
        tk.Button(fe, text="Папка", command=self.enh_browse_folder, bg=self.button_bg, fg=self.fg_color).grid(row=0, column=4, padx=2)
        tk.Button(fe, text="Файл", command=self.enh_browse_file, bg=self.button_bg, fg=self.fg_color).grid(row=0, column=5, padx=2)

        tk.Label(fe, text="Путь к realesrgan-ncnn-vulkan:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky="e")
        self.enh_realesrgan_path_var = self.realesrgan_path_var
        tk.Entry(fe, textvariable=self.enh_realesrgan_path_var, width=60, bg=self.entry_bg, fg=self.fg_color).grid(row=1, column=1, columnspan=4, sticky="we")
        tk.Button(fe, text="Обзор…", command=self.browse_realesrgan_enh, bg=self.button_bg, fg=self.fg_color).grid(row=1, column=5, padx=2)

        tk.Label(fe, text="Модель:", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=0, sticky="w")
        self.enh_model_var = self.realesrgan_model_var
        ttk.Combobox(fe, textvariable=self.enh_model_var, width=28, state="readonly",
                     values=list(MODELS))\
            .grid(row=2, column=1, sticky="w")

        tk.Label(fe, text="Scale:", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=2, sticky="e")
        self.enh_scale_var = self.realesrgan_scale_var
        tk.Entry(fe, textvariable=self.enh_scale_var, width=6, bg=self.entry_bg, fg=self.fg_color).grid(row=2, column=3, sticky="w")

        tk.Label(fe, text="Tile (0=auto):", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=4, sticky="e")
        self.enh_tile_var = self.realesrgan_tile_var
        tk.Entry(fe, textvariable=self.enh_tile_var, width=6, bg=self.entry_bg, fg=self.fg_color).grid(row=2, column=5, sticky="w")

        tk.Label(fe, text="GPU ID:", bg=self.bg_color, fg=self.fg_color).grid(row=3, column=0, sticky="w")
        self.enh_gpuid_var = self.realesrgan_gpuid_var
        tk.Entry(fe, textvariable=self.enh_gpuid_var, width=6, bg=self.entry_bg, fg=self.fg_color).grid(row=3, column=1, sticky="w")

        tk.Label(fe, text="x4plus: ×4; animevideov3: ×2/×3/×4; Tile: 0 или ≥32",
                 bg=self.bg_color, fg=self.fg_color).grid(row=3, column=2, columnspan=4, sticky="w")

        self.btn_enh_start = tk.Button(fe, text="Запустить улучшение", command=self.start_enhance_only, bg=self.button_bg, fg=self.fg_color)
        self.btn_enh_start.grid(row=4, column=0, padx=5, pady=8, sticky="w")
        self.btn_enh_stop = tk.Button(fe, text="Прервать", command=self.stop_processing,
                                     bg="#c0392b", fg="white", state="disabled")
        self.btn_enh_stop.grid(row=3, column=2, padx=5, sticky="w")
        self.btn_save_enh = tk.Button(fe, text="Сохранить настройки", command=self.save_current,
                                      bg=self.button_bg, fg=self.fg_color)
        self.btn_save_enh.grid(row=3, column=3, padx=5, sticky="w")

        self.pb_enh = ttk.Progressbar(fe, length=360, mode="determinate")
        self.pb_enh.grid(row=4, column=1, columnspan=5, sticky="we")
        self.lbl_enh = tk.Label(fe, text="", bg=self.bg_color, fg=self.fg_color)
        self.lbl_enh.grid(row=5, column=0, columnspan=6, sticky="w")

        self.log_enh = scrolledtext.ScrolledText(fe, height=12, bg=self.text_bg, fg=self.fg_color)
        self.log_enh.grid(row=6, column=0, columnspan=6, sticky="nsew")
        self.log_enh.config(state="disabled")

        fe.grid_rowconfigure(6, weight=1)
        for c in (1,2,3,4,5): fe.grid_columnconfigure(c, weight=1)

        self.disk_limit_var = tk.StringVar(value=str(self.settings["enhance_disk_limit_gb"]))
        self.disk_reserve_var = tk.StringVar(value=str(self.settings["enhance_disk_reserve_gb"]))
        for tab, row in ((frm, 17), (fe, 7)):
            resources = tk.Frame(tab, bg=self.bg_color)
            resources.grid(row=row, column=0, columnspan=6, sticky="w", pady=4)
            tk.Label(resources, text="Диск апскейла: лимит (ГиБ)", bg=self.bg_color, fg=self.fg_color).pack(side=tk.LEFT)
            tk.Entry(resources, textvariable=self.disk_limit_var, width=7).pack(side=tk.LEFT, padx=4)
            tk.Label(resources, text="Оставлять свободно (ГиБ)", bg=self.bg_color, fg=self.fg_color).pack(side=tk.LEFT)
            tk.Entry(resources, textvariable=self.disk_reserve_var, width=7).pack(side=tk.LEFT, padx=4)

        # Общие переменные
        self.closing = False
        self.tasks = TaskController()
        self.ui_queue = UIEvents()
        self.worker_thread = None
        self.pending_terminal = None
        self.stop_event = threading.Event()
        self.status_label = tk.Label(root, text=TaskState.IDLE.value, bg=self.bg_color, fg=self.fg_color)
        self.status_label.pack(fill=tk.X)
        self.root.after(100, self.poll_queue)
        if self.settings["current_preset"]:
            self.preset_var.set(self.settings["current_preset"])
        self.toggle_auto_sensitivity()
        for warning in SETTINGS_WARNINGS:
            self.log_message(warning)

        self.preview_image = None
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.refresh_task_controls()

    # ---------- Темы/цвета/вспомогательные ----------
    def set_dark_theme(self, dark=True):
        self.dark_theme = dark
        if dark:
            self.bg_color = "#2c3e50"
            self.fg_color = "#ecf0f1"
            self.entry_bg = "#34495e"
            self.text_bg = "#2c3e50"
            self.button_bg = "#3498db"
        else:
            self.bg_color = "#ecf0f1"
            self.fg_color = "#2c3e50"
            self.entry_bg = "#ffffff"
            self.text_bg = "#ffffff"
            self.button_bg = "#3498db"
        self.root.configure(bg=self.bg_color)

    def toggle_theme(self):
        try:
            candidate = self.collect_settings()
            candidate["dark_theme"] = not self.dark_theme
            self.persist_settings(candidate)
        except Exception as error:
            self.log_message(f"Ошибка сохранения темы: {error}")
            return
        self.set_dark_theme(self.settings["dark_theme"])
        self.update_widget_colors()

    def update_widget_colors(self):
        for widget in self.root.winfo_children():
            if isinstance(widget, tk.Frame):
                widget.configure(bg=self.bg_color)
                for child in widget.winfo_children():
                    try:
                        if isinstance(child, (tk.Label, tk.Checkbutton)):
                            child.configure(bg=self.bg_color, fg=self.fg_color)
                        elif isinstance(child, tk.Entry):
                            child.configure(bg=self.entry_bg, fg=self.fg_color, insertbackground=self.fg_color)
                        elif isinstance(child, tk.Button):
                            child.configure(bg=self.button_bg, fg=self.fg_color)
                        elif isinstance(child, tk.Frame):
                            child.configure(bg=self.bg_color)
                        elif isinstance(child, tk.OptionMenu):
                            child.configure(bg=self.button_bg, fg=self.fg_color, highlightthickness=0)
                    except Exception as e:
                        print(f"Error updating widget: {type(child).__name__}, {e}")
        self.save_full_cb.configure(bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color,
                                    activebackground=self.bg_color, activeforeground=self.fg_color)
        self.auto_sens_cb.configure(bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color,
                                    activebackground=self.bg_color, activeforeground=self.fg_color)
        self.log_text.config(bg=self.text_bg, fg=self.fg_color)

    def log_message(self, message):
        self.tasks.assert_owner()
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 1000:
            self.log_text.delete("1.0", f"{lines - 1000}.0")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def log_message_enh(self, message):
        self.tasks.assert_owner()
        self.log_enh.config(state="normal")
        self.log_enh.insert(tk.END, message + "\n")
        lines = int(self.log_enh.index("end-1c").split(".")[0])
        if lines > 1000:
            self.log_enh.delete("1.0", f"{lines - 1000}.0")
        self.log_enh.see(tk.END)
        self.log_enh.config(state="disabled")

    def browse_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.source_var.set(p)
            self.settings["last_source_type"] = "folder"

    def browse_file(self):
        file_types = [("Видео файлы", "*.mp4 *.mov *.avi *.mkv *.m4v *.webm")]
        p = filedialog.askopenfilename(filetypes=file_types)
        if p:
            self.source_var.set(p)
            self.settings["last_source_type"] = "file"

    def browse_realesrgan(self):
        file_types = [("realesrgan-ncnn-vulkan", "*.exe *.bin *")]
        p = filedialog.askopenfilename(filetypes=file_types, title="Укажите realesrgan-ncnn-vulkan")
        if p:
            self.realesrgan_path_var.set(p)

    def browse_realesrgan_enh(self):
        file_types = [("realesrgan-ncnn-vulkan", "*.exe *.bin *")]
        p = filedialog.askopenfilename(filetypes=file_types, title="Укажите realesrgan-ncnn-vulkan")
        if p:
            self.enh_realesrgan_path_var.set(p)

    def enh_browse_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.enh_source_var.set(p)

    def enh_browse_file(self):
        file_types = [("Видео файлы", "*.mp4 *.mov *.avi *.mkv *.m4v *.webm")]
        p = filedialog.askopenfilename(filetypes=file_types)
        if p:
            self.enh_source_var.set(p)

    def update_presets_combo(self):
        presets = list(self.settings["presets"].keys())
        self.preset_combo["values"] = presets

    def collect_settings(self):
        candidate = copy.deepcopy(self.settings)
        for key, variable in SETTING_VARS.items():
            candidate[key] = getattr(self, variable).get()
        candidate["dark_theme"] = self.dark_theme
        return settings_store.normalize(candidate)

    def persist_settings(self, candidate):
        # Commit in-memory state only after a successful atomic disk write.
        self.settings = save_settings(candidate)

    def apply_settings_to_widgets(self):
        for key, variable in SETTING_VARS.items():
            getattr(self, variable).set(self.settings[key])
        self.preset_var.set(self.settings["current_preset"] or "")
        self.toggle_auto_sensitivity()
        self.set_dark_theme(self.settings["dark_theme"])
        self.update_widget_colors()

    def save_preset(self):
        name = simpledialog.askstring("Сохранение пресета", "Введите название пресета:")
        if name is None:
            return
        name = name.strip()
        if not name or len(name) > 100:
            self.log_message("Название пресета должно содержать от 1 до 100 символов.")
            return
        try:
            candidate = self.collect_settings()
            candidate["presets"][name] = {key: copy.deepcopy(candidate[key]) for key in FIELDS}
            candidate["current_preset"] = name
            self.persist_settings(candidate)
        except Exception as error:
            self.log_message(f"Ошибка сохранения пресета: {error}")
            return
        self.update_presets_combo()
        self.preset_var.set(name)
        self.log_message(f"Пресет '{name}' сохранён.")

    def delete_preset(self):
        name = self.preset_var.get()
        if name not in self.settings["presets"]:
            messagebox.showwarning("Удаление пресета", "Выберите пресет для удаления")
            return
        if not messagebox.askyesno("Удаление пресета", f"Удалить пресет '{name}'?"):
            return
        try:
            candidate = self.collect_settings()
            del candidate["presets"][name]
            if candidate["current_preset"] == name:
                candidate["current_preset"] = None
            self.persist_settings(candidate)
        except Exception as error:
            self.log_message(f"Ошибка удаления пресета: {error}")
            return
        self.preset_var.set(self.settings["current_preset"] or "")
        self.update_presets_combo()
        self.log_message(f"Пресет '{name}' удалён.")

    def apply_preset(self, event=None):
        name = self.preset_var.get()
        if name not in self.settings["presets"]:
            return
        try:
            candidate = copy.deepcopy(self.settings)
            candidate.update(copy.deepcopy(candidate["presets"][name]))
            candidate["current_preset"] = name
            self.persist_settings(candidate)
        except Exception as error:
            self.preset_var.set(self.settings["current_preset"] or "")
            self.log_message(f"Ошибка применения пресета: {error}")
            return
        self.apply_settings_to_widgets()

    def toggle_auto_sensitivity(self):
        if self.auto_sens_var.get():
            self.scale_entry.config(state="normal")
            self.sens_entry.config(state="disabled")
        else:
            self.scale_entry.config(state="disabled")
            self.sens_entry.config(state="normal")

    def save_current(self):
        log = self.log_message_enh if self.notebook.select() == str(self.tab_enhance) else self.log_message
        try:
            self.persist_settings(self.collect_settings())
            log("Настройки успешно сохранены.")
        except Exception as error:
            log(f"Ошибка сохранения настроек: {error}")

    @property
    def processing(self):
        return self.tasks.busy

    def refresh_task_controls(self):
        self.tasks.assert_owner()
        busy = self.tasks.busy or self.closing
        self.btn_start.config(state="disabled" if busy else "normal")
        self.btn_enh_start.config(state="disabled" if busy else "normal")
        self.btn_save.config(state="disabled" if busy else "normal")
        self.btn_save_enh.config(state="disabled" if busy else "normal")
        self.btn_stop.config(state="normal" if self.tasks.state == TaskState.RUNNING
                             and self.tasks.mode == "split" else "disabled")
        self.btn_enh_stop.config(state="normal" if self.tasks.state == TaskState.RUNNING
                                 and self.tasks.mode == "enhance" else "disabled")
        self.status_label.config(text="Закрытие: ожидание остановки..." if self.closing else self.tasks.state.value)

    def launch_task(self, mode, target, args):
        if self.closing or not self.tasks.begin(mode):
            return
        self.ui_queue = UIEvents()
        self.stop_event = threading.Event()
        self.pending_terminal = None
        self.preview_image = None
        self.preview_label.configure(image="")
        self.frame_info.config(text="Ожидание кадров...")
        self.refresh_task_controls()
        # The worker receives only plain data and thread-safe communication,
        # never the App instance or a Tkinter variable/widget.
        context = TaskContext(ui_queue=self.ui_queue, stop_event=self.stop_event)

        def run():
            try:
                target(context, *args)
            except TaskCancelled:
                context.ui_queue.put(("cancelled", 0))
            except Exception as error:
                context.ui_queue.put(("error", str(error)))

        try:
            self.worker_thread = threading.Thread(target=run, daemon=False)
            self.worker_thread.start()
        except Exception as error:
            self.worker_thread = None
            self.ui_queue.put(("error", f"Не удалось запустить обработку: {error}"))

    def stop_processing(self):
        if self.tasks.state == TaskState.RUNNING:
            self.tasks.cancel()
            self.stop_event.set()
            self.refresh_task_controls()
            log = self.log_message_enh if self.tasks.mode == "enhance" else self.log_message
            log("Запрошена отмена. Ожидание завершения текущей операции...")

    def request_close(self):
        self.tasks.assert_owner()
        if self.closing:
            return
        self.closing = True
        if self.processing:
            self.stop_processing()
            self.refresh_task_controls()
        else:
            self.root.destroy()

    def start(self):
        if self.processing or self.closing: return
        source = self.source_var.get().strip()
        if not source:
            self.log_message("Ошибка: Укажите источник (папка/файл).")
            return
        if os.path.isdir(source):
            source_type = "folder"
            files = collect_folder_videos(source, VIDEO_EXTS)
        elif os.path.isfile(source):
            source_type = "file"
            files = [source]
        else:
            self.log_message(f"Ошибка: Путь не существует: {source}")
            return
        if not files:
            self.log_message("Ошибка: Не найдено видеофайлов.")
            return

        try:
            candidate = self.collect_settings()
            candidate["last_source_type"] = source_type
            self.persist_settings(candidate)
        except Exception as error:
            self.log_message(f"Ошибка параметров или сохранения настроек: {error}")
            return
        sensitivity = candidate["sensitivity"]
        min_pause = candidate["min_pause_sec"]
        min_motion = candidate["min_motion_sec"]
        analysis_res = candidate["analysis_resolution"]
        skip = candidate["skip_frames"]
        save_full = candidate["save_full_if_no_motion"]
        auto_sens = candidate["auto_sensitivity"]
        auto_scale = candidate["auto_scale_factor"]
        merge_gap = candidate["merge_gap_sec"]
        enhance_after_split = candidate["enhance_after_split"]
        realesrgan_exe = candidate["realesrgan_path"]
        model = candidate["realesrgan_model"]
        scale = candidate["realesrgan_scale"]
        tile = candidate["realesrgan_tile"]
        gpuid = candidate["realesrgan_gpuid"]

        self.log_message("="*50)
        self.log_message(f"Начата обработка: {source}")
        self.log_message(f"Найдено файлов: {len(files)}")
        self.log_message(f"Параметры движения: Чувств={sensitivity}, Авто={'Вкл' if auto_sens else 'Выкл'}, Коэф={auto_scale}, Объединение={merge_gap}s")
        self.log_message(f"Сглаживание: {candidate['smoothing_sec']} с; запас до/после: {candidate['pre_motion_sec']}/{candidate['post_motion_sec']} с")
        if enhance_after_split:
            self.log_message(f"Real-ESRGAN: {model}, scale={scale}, tile={tile}, GPU={gpuid}, exe={realesrgan_exe}")

        self.launch_task("split", processing.run_split,
                         (files, sensitivity, min_pause, min_motion, analysis_res, skip,
                          save_full, auto_sens, auto_scale, merge_gap, enhance_after_split,
                          realesrgan_exe, model, scale, tile, gpuid, candidate["smoothing_sec"],
                          candidate["pre_motion_sec"], candidate["post_motion_sec"], candidate["export_mode"],
                          candidate["enhance_disk_limit_gb"], candidate["enhance_disk_reserve_gb"]))


    # ------------ Режим «Улучшение» (отдельная вкладка) ------------
    def start_enhance_only(self):
        if self.processing or self.closing: return
        source = self.enh_source_var.get().strip()
        if not source:
            self.log_message_enh("Укажите источник (файл/папка).")
            return
        if os.path.isdir(source):
            files = collect_folder_videos(source, VIDEO_EXTS)
        elif os.path.isfile(source):
            files = [source]
        else:
            self.log_message_enh("Путь не существует.")
            return
        if not files:
            self.log_message_enh("Видео не найдены.")
            return

        try:
            candidate = self.collect_settings()
            self.persist_settings(candidate)
        except Exception as error:
            self.log_message_enh(f"Ошибка параметров или сохранения настроек: {error}")
            return
        realesrgan_exe = candidate["realesrgan_path"]
        model = candidate["realesrgan_model"]
        scale = candidate["realesrgan_scale"]
        tile = candidate["realesrgan_tile"]
        gpuid = candidate["realesrgan_gpuid"]

        self.log_message_enh(f"Real-ESRGAN: {model}, scale={scale}, tile={tile}, GPU={gpuid}, exe={realesrgan_exe}")
        self.launch_task("enhance", processing.run_enhance,
                         (files, realesrgan_exe, model, scale, tile, gpuid,
                          candidate["enhance_disk_limit_gb"], candidate["enhance_disk_reserve_gb"]))


    # ------------ Предпросмотр/очередь сообщений ------------
    def update_preview(self, frame, frame_idx, motion_score):
        self.tasks.assert_owner()
        try:
            height, width = frame.shape[:2]
            max_height = 180
            ratio = max_height / height
            new_width = int(width * ratio)
            resized_frame = cv2.resize(frame, (new_width, max_height))
            rgb_frame = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb_frame)
            self.preview_image = ImageTk.PhotoImage(image=img)
            self.preview_label.configure(image=self.preview_image)
            info = f"Кадр: {frame_idx} | Движение: {motion_score:.2f}"
            self.frame_info.config(text=info)
        except Exception as e:
            self.log_message(f"Ошибка обновления предпросмотра: {str(e)}")

    def poll_queue(self):
        self.tasks.assert_owner()
        enhance = self.tasks.mode == "enhance"
        log = self.log_message_enh if enhance else self.log_message
        progress = self.pb_enh if enhance else self.pb_total
        label = self.lbl_enh if enhance else self.lbl_total
        # Fixed event budget and coalesced progress keep Tk's event loop responsive.
        for kind, payload in self.ui_queue.drain(limit=50):
            if kind == "total_init":
                progress["maximum"] = max(1, payload)
                progress["value"] = 0
                label.config(text=f"Файлов: {payload}")
                if not enhance:
                    self.pb_file["value"] = 0
                    self.lbl_file.config(text="")
            elif kind == "file_name":
                self.lbl_file.config(text=f"Обрабатывается: {payload}")
                self.pb_file["value"] = 0
                self.preview_image = None
                self.preview_label.configure(image="")
                self.frame_info.config(text="Ожидание кадров...")
            elif kind == "phase":
                self.lbl_file.config(text=payload)
                self.pb_file["value"] = 0
                self.pb_file["maximum"] = 1
            elif kind == "file_progress":
                cur, total = payload
                self.pb_file["maximum"] = max(1, total)
                self.pb_file["value"] = cur
            elif kind == "total_progress":
                done, total = payload
                progress["maximum"] = max(1, total)
                progress["value"] = done
                label.config(text=f"Обработано файлов: {done}/{total}")
            elif kind in UIEvents.TERMINAL:
                self.pending_terminal = (kind, payload)
            elif kind == "info":
                log(payload)
            elif kind == "preview_frame":
                self.update_preview(*payload)
        # A terminal message can arrive just before the worker actually exits.
        # Never permit another run until it has exited; no join() on the UI thread.
        if self.pending_terminal and (self.worker_thread is None or not self.worker_thread.is_alive()):
            kind, payload = self.pending_terminal
            self.pending_terminal = None
            self.worker_thread = None
            state = {"done": TaskState.COMPLETED, "error": TaskState.FAILED,
                     "cancelled": TaskState.CANCELLED}[kind]
            self.tasks.finish(state)
            self.refresh_task_controls()
            if kind == "error":
                log(str(payload))
            else:
                log(f"{state.value}. Обработано файлов: {payload}")
        if self.closing and not self.tasks.busy:
            self.root.destroy()
            return
        self.root.after(100, self.poll_queue)


def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()



def launch():
    try:
        main()
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print("Critical error:", error_msg)
        try:
            root = tk.Tk(); root.withdraw()
            from tkinter import messagebox
            messagebox.showerror("Ошибка запуска", f"Программа завершилась с ошибкой:\n\n{error_msg}")
        except:
            print("Failed to create error dialog")
        input("Нажмите Enter для выхода...")
        sys.exit(1)
