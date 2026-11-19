"""Presentation only: user-friendly controls backed by the existing settings variables."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext
from PIL import Image, ImageTk
from gui_components import ScrollForm, ActionButton, Disclosure, NumberPicker, Choice
from settings_store import NUMBERS
from realesrgan_config import MODELS


def build_layout(app, setting_vars):
    root = app.root
    root.option_add('*Font', ('Helvetica', 12))
    for key, name in setting_vars.items():
        kind = tk.BooleanVar if isinstance(app.settings[key], bool) else tk.StringVar
        setattr(app, name, kind(root, value=app.settings[key]))
    app.source_var = tk.StringVar(root)
    app.enh_source_var = tk.StringVar(root)
    app.preset_var = tk.StringVar(root)
    app.model_status_var = tk.StringVar(root)
    for alias, original in [('enh_realesrgan_path_var','realesrgan_path_var'),
                            ('enh_model_var','realesrgan_model_var'), ('enh_scale_var','realesrgan_scale_var'),
                            ('enh_tile_var','realesrgan_tile_var'), ('enh_gpuid_var','realesrgan_gpuid_var')]:
        setattr(app, alias, getattr(app, original))

    header = tk.Frame(root, padx=24, pady=16)
    header.pack(fill='x')
    assets = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets'
    if (assets / 'icon.png').is_file():
        app.brand_image = ImageTk.PhotoImage(Image.open(assets / 'icon.png').resize((48,48)))
        tk.Label(header, image=app.brand_image).pack(side='left', padx=(0,12))
        app.window_icon = tk.PhotoImage(file=str(assets / 'icon.png'))
        root.iconphoto(True, app.window_icon)
    title = tk.Frame(header)
    title.pack(side='left')
    tk.Label(title, text='Motion Splitter', font=('Helvetica',22,'bold')).pack(anchor='w')
    tk.Label(title, text='Оставьте в кадре главное', font=('Helvetica',12)).pack(anchor='w')
    ActionButton(header, text='Как это работает', command=app.show_help).pack(side='right')

    app.notebook = ttk.Notebook(root)
    app.notebook.pack(fill='both', expand=True, padx=16)
    app.tab_split = tk.Frame(app.notebook)
    app.tab_enhance = tk.Frame(app.notebook)
    app.notebook.add(app.tab_split, text='  Нарезка по движению  ')
    app.notebook.add(app.tab_enhance, text='  Улучшение качества  ')
    app.split_form = ScrollForm(app.tab_split)
    app.enhance_form = ScrollForm(app.tab_enhance)
    app.disclosures = []

    def section(parent, title, hint=None, collapsed=False):
        if collapsed:
            panel = Disclosure(parent, title)
            panel.pack(fill='x', pady=(0,10))
            app.disclosures.append(panel)
            body = panel.body
        else:
            panel = tk.Frame(parent, padx=18, pady=14)
            panel._surface = True
            panel.pack(fill='x', pady=(0,12))
            tk.Label(panel, text=title, font=('Helvetica',14,'bold')).pack(anchor='w', pady=(0,10))
            body = panel
        if hint:
            tk.Label(body, text=hint, wraplength=660, justify='left').pack(anchor='w', pady=(0,10))
        return body

    def row(parent, label):
        box=tk.Frame(parent); box.pack(fill='x', pady=5)
        tk.Label(box, text=label, width=29, anchor='w').pack(side='left')
        return box

    def number(parent, key, label, step=.1):
        box=row(parent,label)
        _, low, high, integer=NUMBERS[key]
        widget=NumberPicker(box, textvariable=getattr(app,setting_vars[key]), from_=low, to=high,
                            increment=(32 if key == "realesrgan_tile" else 1) if integer else step, width=10)
        widget.pack(side='left')
        return widget

    def source(parent, variable, file_cmd, folder_cmd):
        box=tk.Frame(parent); box.pack(fill='x')
        tk.Entry(box, textvariable=variable, width=28, relief='flat', borderwidth=0, highlightthickness=0).pack(side='left',fill='x',expand=True,padx=(0,8))
        file_button=ActionButton(box,text='Выбрать видео',command=file_cmd)
        file_button.pack(side='left',padx=4)
        folder_button=ActionButton(box,text='Папка',command=folder_cmd)
        folder_button.pack(side='left')
        return file_button,folder_button

    def choice(parent,label,variable,values):
        widget=Choice(row(parent,label),variable,values)
        widget.pack(side='left',fill='x',expand=True)
        return widget

    frm=app.split_form.content
    source_box=section(frm,'01  Ваше видео','Выберите один файл или папку с видео. Оригиналы останутся на месте.')
    app.btn_file,app.btn_folder=source(source_box, app.source_var,app.browse_file,app.browse_folder)
    basic=section(frm,'02  Как нарезать')
    choice(basic,'Сохранение сегментов',app.export_mode_var,
           {'Быстро · без перекодирования':'fast','Точно · MP4, до кадра':'accurate'})
    app.auto_sens_cb=choice(basic,'Поиск движения',app.auto_sens_var,
                           {'Автоматическая настройка':True,'Настроить вручную':False})
    app.auto_sens_cb.bind('<<ComboboxSelected>>',lambda _:app.toggle_auto_sensitivity(),add='+')
    app.sens_entry=number(basic,'sensitivity','Порог движения')
    app.scale_entry=number(basic,'auto_scale_factor','Чувствительность автонастройки')
    app.sensitivity_hint=tk.Label(basic,text='Меньше значение — замечаем больше движения.',anchor='w')
    app.sensitivity_hint.pack(anchor='w',pady=(2,4))
    advanced=section(frm,'Точная настройка движения',collapsed=True)
    number(advanced,'min_motion_sec','Минимальное движение, с')
    number(advanced,'min_pause_sec','Пауза между событиями, с')
    number(advanced,'merge_gap_sec','Объединять через паузу, с')
    number(advanced,'pre_motion_sec','Добавить до движения, с')
    number(advanced,'post_motion_sec','Добавить после движения, с')
    number(advanced,'smoothing_sec','Сглаживание, с')
    choice(advanced,'Размер анализа',app.res_var,{v:v for v in ['Оригинал','640x360','854x480','1280x720','1920x1080']})
    number(advanced,'skip_frames','Пропуск кадров')
    app.save_full_cb=ttk.Checkbutton(advanced,text='Если движения нет — сохранить видео целиком',variable=app.save_full_var)
    app.save_full_cb.pack(anchor='w',pady=8)
    presets=section(frm,'Мои наборы настроек',collapsed=True)
    app.preset_combo=ttk.Combobox(presets,textvariable=app.preset_var,state='readonly',width=28)
    app.preset_combo.pack(side='left',fill='x',expand=True,padx=(0,8))
    app.update_presets_combo(); app.preset_combo.bind('<<ComboboxSelected>>',app.apply_preset)
    ActionButton(presets,text='Сохранить набор',command=app.save_preset).pack(side='left',padx=4)
    ActionButton(presets,text='Удалить',command=app.delete_preset).pack(side='left')
    extras=tk.Frame(frm); extras.pack(fill='x',pady=(0,12))
    ttk.Checkbutton(extras,text='Улучшить качество после нарезки',variable=app.enhance_after_split_var).pack(side='left')
    ActionButton(extras,text='Настроить улучшение →',command=lambda:app.notebook.select(app.tab_enhance)).pack(side='right')

    progress=section(frm,'03  Результат')
    app.lbl_file=tk.Label(progress,text='Готово к обработке',anchor='w'); app.lbl_file.pack(fill='x')
    app.pb_file=ttk.Progressbar(progress); app.pb_file.pack(fill='x',pady=5)
    app.lbl_total=tk.Label(progress,text='Результаты появятся в отдельной папке рядом с видео.',wraplength=660,justify='left')
    app.lbl_total.pack(anchor='w')
    app.pb_total=ttk.Progressbar(progress); app.pb_total.pack(fill='x',pady=5)
    app.preview_frame=tk.Frame(progress); app.preview_frame.pack(fill='both',expand=True)
    app.log_text=scrolledtext.ScrolledText(app.preview_frame,height=7,width=32,relief='flat',padx=10,pady=10,state='disabled')
    app.log_text.pack(side='left',fill='both',expand=True)
    app.preview_container=tk.Frame(app.preview_frame,bg='black',width=240,height=150)
    app.preview_container.pack(side='right',padx=(12,0)); app.preview_container.pack_propagate(False)
    app.frame_info=tk.Label(app.preview_container,text='Предпросмотр',bg='black',fg='white')
    app.frame_info.pack(side='bottom',fill='x')
    app.preview_label=tk.Label(app.preview_container,bg='black'); app.preview_label.pack(fill='both',expand=True)

    fe=app.enhance_form.content
    box=section(fe,'01  Видео для улучшения','Увеличьте разрешение с помощью Real-ESRGAN. Начните с короткого фрагмента.')
    source(box,app.enh_source_var,app.enh_browse_file,app.enh_browse_folder)
    quality=section(fe,'02  Качество и масштаб')
    app.model_combo=choice(quality,'Для какого видео',app.realesrgan_model_var,{
        'Обычное видео · x4plus':'realesrgan-x4plus','Анимация · x4plus-anime':'realesrgan-x4plus-anime',
        'Аниме · animevideov3':'realesr-animevideov3','Восстановление · realesrnet':'realesrnet-x4plus'})
    app.scale_combo=ttk.Combobox(row(quality,'Увеличить в'),textvariable=app.realesrgan_scale_var,state='readonly',width=10)
    app.scale_combo.pack(side='left')
    def sync_scale(*_):
        allowed=MODELS.get(app.realesrgan_model_var.get(),(4,))
        app.scale_combo.configure(values=allowed)
    def model_selected(_=None):
        sync_scale()
        allowed=MODELS.get(app.realesrgan_model_var.get(),(4,))
        if app.realesrgan_scale_var.get() not in tuple(map(str,allowed)):
            app.realesrgan_scale_var.set(str(allowed[-1]))
    app.model_combo.bind('<<ComboboxSelected>>',model_selected,add='+')
    app.realesrgan_model_var.trace_add('write',sync_scale); sync_scale()
    tk.Label(quality,text='Модели скачиваются отдельно. Эти настройки общие для обеих вкладок.',wraplength=660,justify='left').pack(anchor='w',pady=8)
    setup=section(fe,'Подключить движок и модели',collapsed=True)
    for label,var,cmd in [('Движок Real-ESRGAN',app.realesrgan_path_var,app.browse_realesrgan),('Папка моделей',app.models_dir_var,app.browse_models)]:
        tk.Label(setup,text=label).pack(anchor='w',pady=(6,3))
        box=tk.Frame(setup); box.pack(fill='x')
        tk.Entry(box,textvariable=var,width=32,relief='flat',borderwidth=0,highlightthickness=0).pack(side='left',fill='x',expand=True,padx=(0,8))
        ActionButton(box,text='Выбрать…',command=cmd).pack(side='right')
    box=tk.Frame(setup); box.pack(fill='x',pady=10)
    ActionButton(box,text='Скачать с сайта проекта ↗',command=app.open_models_source).pack(side='left')
    ActionButton(box,text='Проверить файлы',command=app.check_model_files).pack(side='left',padx=8)
    tk.Label(setup,textvariable=app.model_status_var,wraplength=660,justify='left').pack(anchor='w')
    resources=section(fe,'Производительность и место на диске',collapsed=True)
    number(resources,'realesrgan_tile','Размер блока (0 — авто)',32)
    number(resources,'realesrgan_gpuid','Номер видеокарты')
    number(resources,'enhance_disk_limit_gb','Лимит временных файлов, ГиБ',1)
    number(resources,'enhance_disk_reserve_gb','Оставить свободно, ГиБ',1)
    result=section(fe,'03  Результат')
    app.lbl_enh=tk.Label(result,text='Готово к улучшению',wraplength=660,justify='left'); app.lbl_enh.pack(anchor='w')
    app.pb_enh=ttk.Progressbar(result); app.pb_enh.pack(fill='x',pady=8)
    app.log_enh=scrolledtext.ScrolledText(result,height=7,width=40,state='disabled',relief='flat',padx=10,pady=10)
    app.log_enh.pack(fill='both',expand=True)
    for variable in (app.models_dir_var,app.realesrgan_path_var,app.realesrgan_model_var,app.realesrgan_scale_var,app.realesrgan_tile_var,app.realesrgan_gpuid_var):
        variable.trace_add('write',lambda *_:app.model_status_var.set('Параметры изменены; проверьте файлы модели.'))
