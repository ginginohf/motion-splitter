from gui_components import ScrollForm, apply_palette, ActionButton
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
    "realesrgan_models_dir": "models_dir_var",
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
        root.geometry("1040x820")
        root.minsize(760, 540)

        self.settings = load_settings()
        self.dark_theme = self.settings.get("dark_theme", True)
        self.set_dark_theme(self.dark_theme)

        from gui_layout import build_layout
        build_layout(self, SETTING_VARS)

        # Общие переменные
        self.closing = False
        self.tasks = TaskController()
        self.ui_queue = UIEvents()
        self.worker_thread = None
        self.pending_terminal = None
        self.stop_event = threading.Event()
        toolbar = tk.Frame(root)
        toolbar.pack(fill=tk.X)
        self.quick_start = ActionButton(toolbar, text="Нарезать видео", style="Primary.TButton", command=lambda:
            self.start_enhance_only() if self.notebook.select() == str(self.tab_enhance) else self.start())
        self.quick_start.pack(side=tk.LEFT, padx=8, pady=6)
        self.quick_stop = ActionButton(toolbar, text="Прервать", command=self.stop_processing)
        self.quick_stop.pack(side=tk.LEFT, padx=4)
        self.btn_start = self.btn_enh_start = self.quick_start
        self.btn_stop = self.btn_enh_stop = self.quick_stop
        self.btn_save = self.btn_save_enh = ActionButton(toolbar, text="Сохранить настройки", command=self.save_current)
        self.btn_save.pack(side=tk.LEFT, padx=8)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _: self.quick_start.configure(
            text="Улучшить видео" if self.notebook.select() == str(self.tab_enhance) else "Нарезать видео"))
        ActionButton(toolbar, text="Сменить тему", command=self.toggle_theme).pack(side=tk.RIGHT, padx=8)
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
        for form in (self.split_form, self.enhance_form):
            for widget in form.content.winfo_children():
                if isinstance(widget, tk.Label) and int(widget.grid_info().get("columnspan", 1)) == 1:
                    widget.configure(wraplength=210, justify="left")
        self.split_form.bind_navigation()
        self.enhance_form.bind_navigation()
        self.update_widget_colors()
        self.root.bind("<Escape>", lambda event: self.stop_processing())

    # ---------- Темы/цвета/вспомогательные ----------
    def set_dark_theme(self, dark=True):
        self.dark_theme = dark
        if dark:
            self.bg_color = "#111827"
            self.fg_color = "#eef2ff"
            self.entry_bg = "#1e293b"
            self.text_bg = "#111827"
            self.button_bg = "#6366f1"
        else:
            self.bg_color = "#f4f6fb"
            self.fg_color = "#111827"
            self.entry_bg = "#edf1f7"
            self.text_bg = "#ffffff"
            self.button_bg = "#6366f1"
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
        apply_palette(self.root, self.bg_color, self.fg_color, self.entry_bg, self.button_bg,
                      self.preview_container, (self.btn_stop, self.btn_enh_stop, self.quick_stop))

    def show_help(self):
        window = tk.Toplevel(self.root)
        window.title("Как пользоваться Motion Splitter")
        window.geometry("720x520")
        text = scrolledtext.ScrolledText(window, wrap="word", padx=16, pady=16,
                                        bg=self.entry_bg, fg=self.fg_color)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", """НАРЕЗКА ПО ДВИЖЕНИЮ
1. Выберите файл или папку. Подпапки не обрабатываются.
2. В списке «Поиск движения» выберите автоматическую настройку.
3. Выберите экспорт и нажмите «Запустить». Путь результатов появится в журнале.

Порог: меньше значение — больше событий, включая шум и изменение освещения.
Минимальная пауза разделяет события. Минимальное движение отбрасывает короткие.
Запас до/после добавляет контекст. Объединение соединяет близкие сегменты.
Разрешение и пропуск кадров влияют только на анализ; пропуск может скрыть короткое событие.
Быстрый экспорт копирует потоки, границы зависят от ключевых кадров.
Точный экспорт перекодирует в MP4/H.264/AAC и обрезает с точностью до кадров.

УЛУЧШЕНИЕ
Укажите движок realesrgan-ncnn-vulkan и отдельно выберите папку models из архива релиза.
Веса .param/.bin скачиваются отдельно; .pth не подходит. Проверка GPU выполняется при запуске.
x4plus — масштаб 4; animevideov3 — 2, 3 или 4. Tile 0 выбирается автоматически.
Перед обработкой проверяются модель/GPU и бюджет диска. Для большого видео сначала сделайте нарезку.
Настройки Real-ESRGAN и диска общие для обеих вкладок.

РЕЗУЛЬТАТЫ И ОТМЕНА
Ошибка файла не останавливает остальные. Итог и пути файлов — в report.json.
«Прервать» или Escape запрашивает отмену. Готовые результаты сохраняются.
Поля, изменённые во время работы, применяются только к следующему запуску.

НАВИГАЦИЯ
Полосы прокрутки открывают все настройки в небольшом окне.
Колесо прокручивает форму; Shift + колесо — по горизонтали.
В выбранном числовом поле колесо и стрелки меняют значение.
Разделы со стрелкой раскрывают дополнительные параметры.
Tab переводит фокус и прокручивает форму к выбранному полю.
Журналы прокручиваются отдельно. Нижние кнопки доступны при любой прокрутке.
""")
        text.configure(state="disabled")
        window.bind("<Escape>", lambda event: window.destroy())

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

    def browse_models(self):
        from app_paths import user_models_directory
        directory = filedialog.askdirectory(title="Папка с моделями .param/.bin",
                                             initialdir=str(user_models_directory().parent))
        if directory:
            self.models_dir_var.set(directory)
            self.check_model_files()

    def open_models_source(self):
        import webbrowser
        webbrowser.open("https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0")

    def check_model_files(self):
        from realesrgan_config import configuration
        try:
            command = configuration(self.realesrgan_path_var.get(), self.realesrgan_model_var.get(),
                                    int(self.realesrgan_scale_var.get()), int(self.realesrgan_tile_var.get()),
                                    int(self.realesrgan_gpuid_var.get()), self.models_dir_var.get())
            self.model_status_var.set("Файлы найдены: " + command[2] + ". GPU проверяется при запуске.")
        except (ValueError, RuntimeError, OSError) as error:
            self.model_status_var.set(str(error))

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
        active = self.scale_entry if self.auto_sens_var.get() else self.sens_entry
        inactive = self.sens_entry if self.auto_sens_var.get() else self.scale_entry
        inactive.master.pack_forget()
        active.master.pack(fill="x", pady=5, before=self.sensitivity_hint)

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
        if hasattr(self, 'quick_start'):
            self.quick_start.config(state="disabled" if busy else "normal")
            self.quick_stop.config(state="normal" if self.tasks.state == TaskState.RUNNING else "disabled")
        self.btn_start.config(state="disabled" if busy else "normal")
        self.btn_enh_start.config(state="disabled" if busy else "normal")
        self.btn_save.config(state="disabled" if busy else "normal")
        self.btn_save_enh.config(state="disabled" if busy else "normal")
        self.btn_stop.config(state="normal" if self.tasks.state == TaskState.RUNNING else "disabled")
        self.btn_enh_stop.config(state="normal" if self.tasks.state == TaskState.RUNNING else "disabled")
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
                          candidate["enhance_disk_limit_gb"], candidate["enhance_disk_reserve_gb"], candidate["realesrgan_models_dir"]))


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
                          candidate["enhance_disk_limit_gb"], candidate["enhance_disk_reserve_gb"], candidate["realesrgan_models_dir"]))


    # ------------ Предпросмотр/очередь сообщений ------------
    def update_preview(self, frame, frame_idx, motion_score):
        self.tasks.assert_owner()
        try:
            height, width = frame.shape[:2]
            ratio = min(320 / width, 150 / height)
            resized_frame = cv2.resize(frame, (max(1, round(width * ratio)), max(1, round(height * ratio))))
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
            elif kind == "summary":
                log(payload)
                label.config(text=payload.split("\n", 1)[0])
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
