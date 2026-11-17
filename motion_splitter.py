import cv2
import os
import numpy as np
import sys
import json
import threading
from types import SimpleNamespace
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog, scrolledtext
from pathlib import Path
from collections import deque
from PIL import Image, ImageTk
from task_ui import TaskController, TaskState, UIEvents
from external_tools import ProcessRunner, resolve_executable, check_tools, TaskCancelled, check_cancel
from output_safety import (create_run_directory, source_output_directory,
                           collect_folder_videos, staged_output, validate_output_path)

# ---------------- Settings ----------------
SETTINGS_FILE = Path.home() / ".motion_splitter_settings.json"

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

    # --- Новые настройки Real-ESRGAN ---
    "enhance_after_split": False,            # апскейлить сегменты после нарезки
    "realesrgan_path": "",                   # путь к realesrgan-ncnn-vulkan(.exe)
    "realesrgan_model": "realesr-general-x4v3",
    "realesrgan_scale": 4,                   # 2 или 4, зависит от модели
    "realesrgan_tile": 0,                    # 0 = авто (без тайлинга), иначе например 200
    "realesrgan_gpuid": 0                    # id GPU (для ncnn-vulkan)
}

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm")

def load_settings():
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            if "sensitivity" in s and s["sensitivity"] > 255:
                s["sensitivity"] = s["sensitivity"] / (854 * 480)
            for k, v in DEFAULTS.items():
                if k not in s:
                    s[k] = v
            return s
    except Exception:
        pass
    return DEFAULTS.copy()

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Settings save error:", e)

def find_ffmpeg():
    exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = [exe_dir / "ffmpeg" / "ffmpeg.exe", exe_dir / "ffmpeg.exe",
                  exe_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
                  exe_dir / "ffmpeg" / "bin" / "ffmpeg", exe_dir / "ffmpeg"]
    return resolve_executable("ffmpeg", candidates)


def find_realesrgan(custom_path=""):
    exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = [exe_dir / name for name in ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan")]
    candidates += [exe_dir / "realesrgan" / name
                   for name in ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan")]
    return resolve_executable("realesrgan-ncnn-vulkan", candidates, custom_path)

def record_task_error(runner, stage, error):
    message = f"Ошибка: {error}"
    if runner is not None and not hasattr(error, "log_path"):
        try:
            log = runner.note(stage, message)
            message += f"\nПолный журнал: {log}"
        except OSError as log_error:
            message += f"\nНе удалось сохранить журнал: {log_error}"
    return message


# -------------- Core Logic (анализ движения) ----------------
def parse_resolution(opt, orig_w, orig_h):
    if opt.strip().lower() == "оригинал":
        return orig_w, orig_h
    try:
        w, h = opt.lower().split("x")
        return int(w), int(h)
    except Exception:
        return orig_w, orig_h

def get_video_duration(video_path):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps
    cap.release()
    return duration

def calculate_auto_sensitivity(video_path, analysis_res, skip_frames, progress_cb=None, stop_event=None):
    check_cancel(stop_event)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise IOError(f"Не удалось открыть видео: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width, height = parse_resolution(analysis_res, orig_w, orig_h)
        num_pixels = width * height

        motion_scores = []
        frame_idx = 0
        skip = max(0, int(skip_frames))
        sample_size = min(300, total_frames)

        start_frame = int(total_frames * 0.05)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        ret, prev = cap.read()
        if not ret:
            return 1.0

        if (prev.shape[1], prev.shape[0]) != (width, height):
            prev = cv2.resize(prev, (width, height), interpolation=cv2.INTER_LINEAR)
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        prev_gray = cv2.GaussianBlur(prev_gray, (21,21), 0)

        while len(motion_scores) < sample_size:
            check_cancel(stop_event)
            ret, frame = cap.read()
            check_cancel(stop_event)
            if not ret:
                break
            frame_idx += 1
            if skip > 0 and (frame_idx % (skip+1)) != 0:
                if progress_cb: progress_cb(frame_idx, total_frames)
                continue
            if (frame.shape[1], frame.shape[0]) != (width, height):
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21,21), 0)
            delta = cv2.absdiff(prev_gray, gray)
            score = delta.sum() / num_pixels
            motion_scores.append(score)
            prev_gray = gray
            if progress_cb: progress_cb(frame_idx, total_frames)

        if not motion_scores:
            return 1.0

        avg_score = np.mean(motion_scores)
        max_score = np.max(motion_scores)
        sorted_scores = sorted(motion_scores)
        median_score = sorted_scores[len(sorted_scores)//2]
        base_sensitivity = median_score * 1.5
        if max_score > median_score * 3:
            base_sensitivity = max_score * 0.3
        base_sensitivity = max(0.5, min(5.0, base_sensitivity))
        return base_sensitivity
    finally:
        cap.release()

def merge_close_segments(segments, merge_gap):
    if not segments or merge_gap <= 0:
        return segments.copy()
    merged = []
    current_start, current_end = segments[0]
    for start, end in segments[1:]:
        if start - current_end <= merge_gap:
            current_end = end
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged

def analyze_video(video_path, sensitivity, min_pause, min_motion, analysis_res, skip_frames,
                 progress_cb=None, stop_event=None, preview_cb=None, merge_gap=0):
    check_cancel(stop_event)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise IOError(f"Не удалось открыть видео: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width, height = parse_resolution(analysis_res, orig_w, orig_h)
        num_pixels = width * height

        ret, prev = cap.read()
        if not ret:
            return []
        if (prev.shape[1], prev.shape[0]) != (width, height):
            prev = cv2.resize(prev, (width, height), interpolation=cv2.INTER_LINEAR)
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        prev_gray = cv2.GaussianBlur(prev_gray, (21,21), 0)

        motion_segments = []
        in_motion = False
        motion_start_ms = None
        still_counter = 0
        frame_idx = 0
        skip = max(0, int(skip_frames))
        motion_history = deque(maxlen=5)
        preview_counter = 0

        while True:
            check_cancel(stop_event)
            ret, frame = cap.read()
            check_cancel(stop_event)
            if not ret:
                break
            frame_idx += 1
            if skip > 0 and (frame_idx % (skip+1)) != 0:
                if progress_cb: progress_cb(frame_idx, total_frames)
                continue
            if (frame.shape[1], frame.shape[0]) != (width, height):
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21,21), 0)
            delta = cv2.absdiff(prev_gray, gray)
            score = delta.sum() / num_pixels
            motion_history.append(score)
            smoothed_score = sum(motion_history) / len(motion_history)
            preview_counter += 1
            if preview_cb and preview_counter % 5 == 0:
                preview_cb(frame.copy(), frame_idx, smoothed_score)
            if smoothed_score > sensitivity:
                if not in_motion:
                    in_motion = True
                    motion_start_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                still_counter = 0
            else:
                still_counter += 1
                if in_motion and (still_counter > (min_pause*fps)/(skip+1)):
                    motion_end_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                    if (motion_end_ms - motion_start_ms) >= (min_motion*1000.0):
                        motion_segments.append((motion_start_ms/1000.0, motion_end_ms/1000.0))
                    in_motion = False
                    motion_start_ms = None
                    still_counter = 0
            prev_gray = gray
            if progress_cb: progress_cb(frame_idx, total_frames)

        if in_motion and motion_start_ms is not None:
            end_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            if (end_ms - motion_start_ms) >= (min_motion*1000.0):
                motion_segments.append((motion_start_ms/1000.0, end_ms/1000.0))
        if merge_gap > 0:
            motion_segments = merge_close_segments(motion_segments, merge_gap)
        return motion_segments
    finally:
        cap.release()

def save_segments(video_path, segments, ffmpeg_path, output_dir=None, runner=None):
    vpath = Path(video_path).resolve(strict=True)
    if output_dir is None:
        output_dir = source_output_directory(create_run_directory(vpath.parent), vpath)
    out_dir = Path(output_dir)
    runner = runner or ProcessRunner(out_dir / "logs")
    created = []
    for i, (start, end) in enumerate(segments, start=1):
        suffix = vpath.suffix if vpath.suffix.lower() in VIDEO_EXTS else ".mp4"
        out_file = out_dir / f"{vpath.stem}_segment_{i:02d}{suffix}"
        with staged_output(vpath, out_file, runner.check_cancel) as temporary:
            cmd = [ffmpeg_path, "-n", "-i", str(vpath),
                   "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                   "-c", "copy", str(temporary)]
            runner.run(cmd, f"FFmpeg: сохранение сегмента {i} ({vpath.name})")
        created.append(str(out_file))
    return created


def save_full_video(video_path, ffmpeg_path, output_dir=None, runner=None):
    vpath = Path(video_path).resolve(strict=True)
    if output_dir is None:
        output_dir = source_output_directory(create_run_directory(vpath.parent), vpath)
    runner = runner or ProcessRunner(Path(output_dir) / "logs")
    suffix = vpath.suffix if vpath.suffix.lower() in VIDEO_EXTS else ".mp4"
    out_file = Path(output_dir) / f"{vpath.stem}_full{suffix}"
    with staged_output(vpath, out_file, runner.check_cancel) as temporary:
        cmd = [ffmpeg_path, "-n", "-i", str(vpath), "-c", "copy", str(temporary)]
        runner.run(cmd, f"FFmpeg: сохранение полного видео ({vpath.name})")
    return str(out_file)


# -------------- Real-ESRGAN (ncnn-vulkan) интеграция ----------------
def enhance_video_with_realesrgan(input_path, output_path, realesrgan_exe, model, scale, tile=0, gpuid=0, runner=None):
    """Attempt direct processing in a private directory, publishing on success."""
    validate_output_path(input_path, output_path)
    runner = runner or ProcessRunner(Path(output_path).parent / "logs")
    with staged_output(input_path, output_path, runner.check_cancel) as temporary:
        cmd = [realesrgan_exe, "-i", str(input_path), "-o", str(temporary),
               "-n", str(model), "-s", str(int(scale)), "-g", str(int(gpuid))]
        if tile and int(tile) > 0:
            cmd += ["-t", str(int(tile))]
        runner.run(cmd, "Real-ESRGAN: прямое улучшение видео")
    return True


def enhance_video_fallback_frames(input_path, output_path, realesrgan_exe, model, scale, tile, gpuid, ffmpeg_path, ui_log=None, runner=None):
    """Extract, upscale and mux within a unique directory owned by this call."""
    validate_output_path(input_path, output_path)
    runner = runner or ProcessRunner(Path(output_path).parent / "logs")
    with staged_output(input_path, output_path, runner.check_cancel) as temporary:
        frames_dir = temporary.parent / "frames"
        out_frames_dir = temporary.parent / "frames_up"
        frames_dir.mkdir()
        out_frames_dir.mkdir()

        cap = cv2.VideoCapture(str(input_path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        finally:
            cap.release()

        if ui_log: ui_log("Извлечение кадров через FFmpeg…")
        cmd_extract = [ffmpeg_path, "-n", "-i", str(input_path), "-vsync", "0",
                       str(frames_dir / "%08d.png")]
        runner.run(cmd_extract, "FFmpeg: извлечение кадров")

        if ui_log: ui_log("Апскейл кадров Real-ESRGAN…")
        cmd_realesr = [realesrgan_exe, "-i", str(frames_dir), "-o", str(out_frames_dir),
                      "-n", str(model), "-s", str(int(scale)), "-g", str(int(gpuid)), "-f", "png"]
        if tile and int(tile) > 0:
            cmd_realesr += ["-t", str(int(tile))]
        runner.run(cmd_realesr, "Real-ESRGAN: улучшение кадров (модель/GPU)")

        if ui_log: ui_log("Сборка апскейленного видео…")
        cmd_mux = [ffmpeg_path, "-n", "-r", f"{fps:.3f}",
                   "-i", str(out_frames_dir / "%08d.png"), "-i", str(input_path),
                   "-map", "0:v:0", "-map", "1:a?", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium",
                   "-c:a", "copy", str(temporary)]
        runner.run(cmd_mux, "FFmpeg: сборка видео и звука")
    return True

# -------------- GUI ----------------
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

        tk.Label(frm, text="Чувствительность (ср.изм.на пикс):", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=0, sticky="w")
        self.sens_var = tk.StringVar(value=str(self.settings["sensitivity"]))
        self.sens_entry = tk.Entry(frm, textvariable=self.sens_var, width=12, bg=self.entry_bg, fg=self.fg_color)
        self.sens_entry.grid(row=2, column=1, sticky="w")

        tk.Label(frm, text="Мин. пауза (сек):", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=2, sticky="w")
        self.pause_var = tk.StringVar(value=str(self.settings["min_pause_sec"]))
        tk.Entry(frm, textvariable=self.pause_var, width=12, bg=self.entry_bg, fg=self.fg_color).grid(row=2, column=3, sticky="w")

        tk.Label(frm, text="Мин. время движения (сек):", bg=self.bg_color, fg=self.fg_color).grid(row=3, column=0, sticky="w")
        self.minmov_var = tk.StringVar(value=str(self.settings["min_motion_sec"]))
        tk.Entry(frm, textvariable=self.minmov_var, width=12, bg=self.entry_bg, fg=self.fg_color).grid(row=3, column=1, sticky="w")

        tk.Label(frm, text="Разрешение анализа:", bg=self.bg_color, fg=self.fg_color).grid(row=3, column=2, sticky="w")
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
        tk.Label(frm, text="(0.1-чувствительно, 1.0-умеренно, 2.0-агрессивно)", bg=self.bg_color, fg=self.fg_color).grid(row=5, column=4, sticky="w")

        # --- Новое: настройки апскейла прямо во вкладке «Нарезка» ---
        sep = tk.Label(frm, text="—"*90, bg=self.bg_color, fg=self.fg_color)
        sep.grid(row=6, column=0, columnspan=6, sticky="we", pady=(8,4))

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
        self.realesrgan_model_var = tk.StringVar(value=self.settings.get("realesrgan_model","realesr-general-x4v3"))
        self.model_combo = ttk.Combobox(frm, textvariable=self.realesrgan_model_var, width=28, state="readonly",
                                        values=["realesr-general-x4v3","realesrgan-x4plus","realesrgan-x4plus-anime","realesr-animevideov3"])
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
        self.enh_realesrgan_path_var = tk.StringVar(value=self.settings.get("realesrgan_path",""))
        tk.Entry(fe, textvariable=self.enh_realesrgan_path_var, width=60, bg=self.entry_bg, fg=self.fg_color).grid(row=1, column=1, columnspan=4, sticky="we")
        tk.Button(fe, text="Обзор…", command=self.browse_realesrgan_enh, bg=self.button_bg, fg=self.fg_color).grid(row=1, column=5, padx=2)

        tk.Label(fe, text="Модель:", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=0, sticky="w")
        self.enh_model_var = tk.StringVar(value=self.settings.get("realesrgan_model","realesr-general-x4v3"))
        ttk.Combobox(fe, textvariable=self.enh_model_var, width=28, state="readonly",
                     values=["realesr-general-x4v3","realesrgan-x4plus","realesrgan-x4plus-anime","realesr-animevideov3"])\
            .grid(row=2, column=1, sticky="w")

        tk.Label(fe, text="Scale:", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=2, sticky="e")
        self.enh_scale_var = tk.StringVar(value=str(self.settings.get("realesrgan_scale",4)))
        tk.Entry(fe, textvariable=self.enh_scale_var, width=6, bg=self.entry_bg, fg=self.fg_color).grid(row=2, column=3, sticky="w")

        tk.Label(fe, text="Tile (0=auto):", bg=self.bg_color, fg=self.fg_color).grid(row=2, column=4, sticky="e")
        self.enh_tile_var = tk.StringVar(value=str(self.settings.get("realesrgan_tile",0)))
        tk.Entry(fe, textvariable=self.enh_tile_var, width=6, bg=self.entry_bg, fg=self.fg_color).grid(row=2, column=5, sticky="w")

        tk.Label(fe, text="GPU ID:", bg=self.bg_color, fg=self.fg_color).grid(row=3, column=0, sticky="w")
        self.enh_gpuid_var = tk.StringVar(value=str(self.settings.get("realesrgan_gpuid",0)))
        tk.Entry(fe, textvariable=self.enh_gpuid_var, width=6, bg=self.entry_bg, fg=self.fg_color).grid(row=3, column=1, sticky="w")

        self.btn_enh_start = tk.Button(fe, text="Запустить улучшение", command=self.start_enhance_only, bg=self.button_bg, fg=self.fg_color)
        self.btn_enh_start.grid(row=4, column=0, padx=5, pady=8, sticky="w")
        self.btn_enh_stop = tk.Button(fe, text="Прервать", command=self.stop_processing,
                                     bg="#c0392b", fg="white", state="disabled")
        self.btn_enh_stop.grid(row=3, column=2, padx=5, sticky="w")

        self.pb_enh = ttk.Progressbar(fe, length=360, mode="determinate")
        self.pb_enh.grid(row=4, column=1, columnspan=5, sticky="we")
        self.lbl_enh = tk.Label(fe, text="", bg=self.bg_color, fg=self.fg_color)
        self.lbl_enh.grid(row=5, column=0, columnspan=6, sticky="w")

        self.log_enh = scrolledtext.ScrolledText(fe, height=12, bg=self.text_bg, fg=self.fg_color)
        self.log_enh.grid(row=6, column=0, columnspan=6, sticky="nsew")
        self.log_enh.config(state="disabled")

        fe.grid_rowconfigure(6, weight=1)
        for c in (1,2,3,4,5): fe.grid_columnconfigure(c, weight=1)

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
            self.apply_preset()
        self.toggle_auto_sensitivity()

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
        self.dark_theme = not self.dark_theme
        self.set_dark_theme(self.dark_theme)
        self.settings["dark_theme"] = self.dark_theme
        save_settings(self.settings)
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

    def save_preset(self):
        preset_name = simpledialog.askstring("Сохранение пресета", "Введите название пресета:")
        if not preset_name:
            return
        preset = {
            "sensitivity": float(self.sens_var.get()),
            "min_pause_sec": float(self.pause_var.get()),
            "min_motion_sec": float(self.minmov_var.get()),
            "analysis_resolution": self.res_var.get(),
            "skip_frames": int(self.skip_var.get()),
            "save_full_if_no_motion": self.save_full_var.get(),
            "auto_sensitivity": self.auto_sens_var.get(),
            "auto_scale_factor": float(self.auto_scale_var.get()),
            "dark_theme": self.dark_theme,
            "merge_gap_sec": float(self.merge_gap_var.get()),
            "enhance_after_split": self.enhance_after_split_var.get(),
            "realesrgan_path": self.realesrgan_path_var.get(),
            "realesrgan_model": self.realesrgan_model_var.get(),
            "realesrgan_scale": int(self.realesrgan_scale_var.get()),
            "realesrgan_tile": int(self.realesrgan_tile_var.get()),
            "realesrgan_gpuid": int(self.realesrgan_gpuid_var.get())
        }
        self.settings["presets"][preset_name] = preset
        self.settings["current_preset"] = preset_name
        save_settings(self.settings)
        self.update_presets_combo()
        self.preset_var.set(preset_name)
        self.log_message(f"Пресет '{preset_name}' успешно сохранен!")

    def delete_preset(self):
        preset_name = self.preset_var.get()
        if not preset_name or preset_name not in self.settings["presets"]:
            messagebox.showwarning("Удаление пресета", "Выберите пресет для удаления")
            return
        if messagebox.askyesno("Удаление пресета", f"Удалить пресет '{preset_name}'?"):
            del self.settings["presets"][preset_name]
            if self.settings["current_preset"] == preset_name:
                self.settings["current_preset"] = None
                self.preset_var.set("")
            save_settings(self.settings)
            self.update_presets_combo()
            self.log_message(f"Пресет '{preset_name}' удален!")

    def apply_preset(self, event=None):
        preset_name = self.preset_var.get()
        if not preset_name or preset_name not in self.settings["presets"]:
            return
        preset = self.settings["presets"][preset_name]
        self.sens_var.set(preset["sensitivity"])
        self.pause_var.set(preset["min_pause_sec"])
        self.minmov_var.set(preset["min_motion_sec"])
        self.res_var.set(preset["analysis_resolution"])
        self.skip_var.set(preset["skip_frames"])
        self.save_full_var.set(preset["save_full_if_no_motion"])
        self.auto_sens_var.set(preset.get("auto_sensitivity", False))
        self.auto_scale_var.set(preset.get("auto_scale_factor", 0.5))
        self.merge_gap_var.set(preset.get("merge_gap_sec", 1.0))
        self.enhance_after_split_var.set(preset.get("enhance_after_split", False))
        self.realesrgan_path_var.set(preset.get("realesrgan_path",""))
        self.realesrgan_model_var.set(preset.get("realesrgan_model","realesr-general-x4v3"))
        self.realesrgan_scale_var.set(str(preset.get("realesrgan_scale",4)))
        self.realesrgan_tile_var.set(str(preset.get("realesrgan_tile",0)))
        self.realesrgan_gpuid_var.set(str(preset.get("realesrgan_gpuid",0)))

        self.settings["current_preset"] = preset_name
        save_settings(self.settings)
        self.toggle_auto_sensitivity()
        if "dark_theme" in preset:
            self.dark_theme = preset["dark_theme"]
            self.set_dark_theme(self.dark_theme)
            self.update_widget_colors()

    def toggle_auto_sensitivity(self):
        if self.auto_sens_var.get():
            self.scale_entry.config(state="normal")
            self.sens_entry.config(state="disabled")
        else:
            self.scale_entry.config(state="disabled")
            self.sens_entry.config(state="normal")

    def save_current(self):
        try:
            s = {
                "sensitivity": float(self.sens_var.get()),
                "min_pause_sec": float(self.pause_var.get()),
                "min_motion_sec": float(self.minmov_var.get()),
                "analysis_resolution": self.res_var.get(),
                "skip_frames": int(self.skip_var.get()),
                "save_full_if_no_motion": self.save_full_var.get(),
                "presets": self.settings["presets"],
                "current_preset": self.settings["current_preset"],
                "auto_sensitivity": self.auto_sens_var.get(),
                "auto_scale_factor": float(self.auto_scale_var.get()),
                "dark_theme": self.dark_theme,
                "last_source_type": self.settings.get("last_source_type", "folder"),
                "merge_gap_sec": float(self.merge_gap_var.get()),

                "enhance_after_split": self.enhance_after_split_var.get(),
                "realesrgan_path": self.realesrgan_path_var.get(),
                "realesrgan_model": self.realesrgan_model_var.get(),
                "realesrgan_scale": int(self.realesrgan_scale_var.get()),
                "realesrgan_tile": int(self.realesrgan_tile_var.get()),
                "realesrgan_gpuid": int(self.realesrgan_gpuid_var.get())
            }
            save_settings(s)
            self.log_message("Настройки успешно сохранены.")
        except Exception as e:
            self.log_message(f"Ошибка сохранения настроек: {e}")

    @property
    def processing(self):
        return self.tasks.busy

    def refresh_task_controls(self):
        self.tasks.assert_owner()
        busy = self.tasks.busy or self.closing
        self.btn_start.config(state="disabled" if busy else "normal")
        self.btn_enh_start.config(state="disabled" if busy else "normal")
        self.btn_save.config(state="disabled" if busy else "normal")
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
        context = SimpleNamespace(ui_queue=self.ui_queue, stop_event=self.stop_event)

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
            sensitivity = float(self.sens_var.get())
            min_pause = float(self.pause_var.get())
            min_motion = float(self.minmov_var.get())
            analysis_res = self.res_var.get()
            skip = int(self.skip_var.get())
            save_full = self.save_full_var.get()
            auto_sens = self.auto_sens_var.get()
            auto_scale = float(self.auto_scale_var.get())
            merge_gap = float(self.merge_gap_var.get())

            # Real-ESRGAN
            enhance_after_split = self.enhance_after_split_var.get()
            realesrgan_exe = self.realesrgan_path_var.get().strip()
            model = self.realesrgan_model_var.get()
            scale = int(self.realesrgan_scale_var.get())
            tile = int(self.realesrgan_tile_var.get())
            gpuid = int(self.realesrgan_gpuid_var.get())
        except Exception as e:
            self.log_message(f"Ошибка параметров: {e}")
            return

        save_settings({
            "sensitivity": sensitivity,
            "min_pause_sec": min_pause,
            "min_motion_sec": min_motion,
            "analysis_resolution": analysis_res,
            "skip_frames": skip,
            "save_full_if_no_motion": save_full,
            "presets": self.settings["presets"],
            "current_preset": self.settings["current_preset"],
            "auto_sensitivity": auto_sens,
            "auto_scale_factor": auto_scale,
            "dark_theme": self.dark_theme,
            "last_source_type": source_type,
            "merge_gap_sec": merge_gap,
            "enhance_after_split": enhance_after_split,
            "realesrgan_path": realesrgan_exe,
            "realesrgan_model": model,
            "realesrgan_scale": scale,
            "realesrgan_tile": tile,
            "realesrgan_gpuid": gpuid
        })

        self.log_message("="*50)
        self.log_message(f"Начата обработка: {source}")
        self.log_message(f"Найдено файлов: {len(files)}")
        self.log_message(f"Параметры движения: Чувств={sensitivity}, Авто={'Вкл' if auto_sens else 'Выкл'}, Коэф={auto_scale}, Объединение={merge_gap}s")
        if enhance_after_split:
            self.log_message(f"Real-ESRGAN: {model}, scale={scale}, tile={tile}, GPU={gpuid}, exe={realesrgan_exe}")

        self.launch_task("split", App.worker_process,
                         (files, sensitivity, min_pause, min_motion, analysis_res, skip,
                          save_full, auto_sens, auto_scale, merge_gap, enhance_after_split,
                          realesrgan_exe, model, scale, tile, gpuid))

    def worker_process(self, files, sensitivity, min_pause, min_motion, analysis_res, skip, save_full, auto_sens, auto_scale, merge_gap,
                       enhance_after_split, realesrgan_exe, model, scale, tile, gpuid):
        runner = None
        done = 0
        try:
            check_cancel(self.stop_event)
            run_dir = create_run_directory(Path(files[0]).resolve().parent)
            runner = ProcessRunner(run_dir / "logs", self.stop_event)
            self.ui_queue.put(("info", f"Папка результатов: {run_dir}"))
            self.ui_queue.put(("info", f"Журналы диагностики: {runner.log_directory}"))
            ffmpeg_path = find_ffmpeg()
            if enhance_after_split:
                realesrgan_exe = find_realesrgan(realesrgan_exe)
            check_tools(runner, ffmpeg_path, realesrgan_exe if enhance_after_split else None)
            total = len(files); done = 0
            self.ui_queue.put(("total_init", total))

            for path in files:
                if self.stop_event.is_set():
                    self.ui_queue.put(("info", "Обработка прервана"))
                    break
                self.ui_queue.put(("file_name", Path(path).name))

                def file_progress(cur, total_frames):
                    if self.stop_event.is_set(): return
                    self.ui_queue.put(("file_progress", (cur, total_frames)))

                def preview_callback(frame, frame_idx, motion_score):
                    self.ui_queue.put(("preview_frame", (frame, frame_idx, motion_score)))

                if auto_sens and not self.stop_event.is_set():
                    self.ui_queue.put(("info", f"Автонастройка чувствительности для {Path(path).name}"))
                    auto_sensitivity = calculate_auto_sensitivity(path, analysis_res, skip, progress_cb=file_progress, stop_event=self.stop_event)
                    auto_sensitivity *= auto_scale
                    self.ui_queue.put(("info", f"Автонастроенная чувствительность: {auto_sensitivity:.2f}"))
                    sensitivity = auto_sensitivity

                segments = []
                if not self.stop_event.is_set():
                    segments = analyze_video(path, sensitivity, min_pause, min_motion, analysis_res, skip,
                                             progress_cb=file_progress, stop_event=self.stop_event,
                                             preview_cb=preview_callback, merge_gap=merge_gap)

                if not self.stop_event.is_set():
                    out_dir = source_output_directory(run_dir, path)
                    created = []
                    if segments:
                        created = save_segments(path, segments, ffmpeg_path, out_dir, runner)
                        self.ui_queue.put(("info", f"Найдено сегментов: {len(segments)}"))
                    elif save_full:
                        full_path = save_full_video(path, ffmpeg_path, out_dir, runner)
                        created = [full_path]
                        self.ui_queue.put(("info", f"Сохранено полное видео: {full_path}"))

                    # --- Апскейл сегментов (если включено) ---
                    if enhance_after_split and created:
                        for seg_file in created:
                            if self.stop_event.is_set(): break
                            seg_path = Path(seg_file)
                            up_path = seg_path.with_name(seg_path.stem + "_upscaled" + seg_path.suffix)
                            self.ui_queue.put(("info", f"Real-ESRGAN апскейл: {seg_path.name} → {up_path.name}"))
                            enhance_video_fallback_frames(seg_path, up_path, realesrgan_exe, model, scale, tile, gpuid, ffmpeg_path,
                                                          ui_log=lambda m: self.ui_queue.put(("info", m)), runner=runner)
                            self.ui_queue.put(("info", f"Готово: {up_path}"))

                if self.stop_event.is_set():
                    break
                done += 1
                self.ui_queue.put(("total_progress", (done, total)))

            self.ui_queue.put(("cancelled" if self.stop_event.is_set() else "done", done))
        except TaskCancelled as error:
            self.ui_queue.put(("info", str(error)))
            self.ui_queue.put(("cancelled", done))
        except Exception as e:
            message = record_task_error(runner, "Обработка видео", e)
            self.ui_queue.put(("error", message))

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

        realesrgan_exe = self.enh_realesrgan_path_var.get().strip()
        model = self.enh_model_var.get()
        try:
            scale = int(self.enh_scale_var.get())
            tile = int(self.enh_tile_var.get())
            gpuid = int(self.enh_gpuid_var.get())
        except:
            self.log_message_enh("Некорректные параметры scale/tile/gpu.")
            return

        self.log_message_enh(f"Real-ESRGAN: {model}, scale={scale}, tile={tile}, GPU={gpuid}, exe={realesrgan_exe}")
        self.launch_task("enhance", App.worker_enhance,
                         (files, realesrgan_exe, model, scale, tile, gpuid))

    def worker_enhance(self, files, realesrgan_exe, model, scale, tile, gpuid):
        runner = None
        done = 0
        try:
            check_cancel(self.stop_event)
            run_dir = create_run_directory(Path(files[0]).resolve().parent)
            runner = ProcessRunner(run_dir / "logs", self.stop_event)
            self.ui_queue.put(("info", f"Папка результатов: {run_dir}"))
            self.ui_queue.put(("info", f"Журналы диагностики: {runner.log_directory}"))
            self.ui_queue.put(("total_init", len(files)))
            ffmpeg_path = find_ffmpeg()
            executable = find_realesrgan(realesrgan_exe)
            check_tools(runner, ffmpeg_path, executable)
            done = 0
            failures = 0
            for f in files:
                if self.stop_event.is_set():
                    break
                try:
                    in_p = Path(f)
                    out_dir = source_output_directory(run_dir, in_p)
                    out_p = out_dir / (in_p.stem + "_upscaled" + in_p.suffix)
                    self.ui_queue.put(("info", f"Апскейл: {in_p.name} → {out_p.name}"))
                    enhance_video_fallback_frames(in_p, out_p, executable, model, scale, tile, gpuid, ffmpeg_path,
                                                  ui_log=lambda m: self.ui_queue.put(("info", m)), runner=runner)
                    self.ui_queue.put(("info", f"Готово: {out_p}"))
                except TaskCancelled:
                    raise
                except Exception as error:
                    failures += 1
                    self.ui_queue.put(("info", record_task_error(runner, "Улучшение видео", error)))
                done += 1
                self.ui_queue.put(("total_progress", (done, len(files))))
            if self.stop_event.is_set():
                self.ui_queue.put(("cancelled", done))
            elif failures:
                self.ui_queue.put(("error", f"Улучшение завершилось с ошибками: {failures} из {done} файлов."))
            else:
                self.ui_queue.put(("done", done))
        except TaskCancelled as error:
            self.ui_queue.put(("info", str(error)))
            self.ui_queue.put(("cancelled", done))
        except Exception as error:
            self.ui_queue.put(("error", record_task_error(runner, "Улучшение видео", error)))

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

if __name__ == "__main__":
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
