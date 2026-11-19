# Разработка и проверки

[Главная](../README.md) · [Участие](../CONTRIBUTING.md) · [Упаковка](../packaging/README.md)

## Среда

Текущая локальная проверка: macOS arm64, Python 3.12.16, Tcl/Tk 9.1.0.
Системный Python macOS с Tk 8.5 давал почти пустое окно; не используйте его.
Упаковщик требует Tk не ниже 8.6, но это не обещание совместимости с любой
версией macOS или любой сборкой Tk.

Python должен включать Tkinter; `pip install tkinter` не является способом
установить его. Для разработки нужны FFmpeg и FFprobe в PATH.
В готовой frozen-сборке используются только включённые инструменты.

Пример для macOS с уже установленным Homebrew:

```sh
brew install python@3.12 python-tk@3.12 ffmpeg
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m tkinter
python motion_splitter.py
```

Команды выполняются из корня проекта. `python -m tkinter` должен открыть окно
с версией Tcl/Tk. Если используете другой Python, сначала проверьте его Tk.

На других системах подготовьте Python с Tk и FFmpeg/FFprobe, затем:

```sh
python -m venv .venv
python -m pip install -r requirements-dev.txt
```

Перед второй командой активируйте окружение: `source .venv/bin/activate`
на Linux или `.venv\Scripts\Activate.ps1` в PowerShell. Нативная приёмка
Windows/Linux пока не выполнена; список CI-окружений не равен поддержке продукта.

`requirements.txt` содержит runtime-библиотеки. `requirements-dev.txt` добавляет
imageio-ffmpeg для тестовых материалов. `python tools/run_local.py` помещает
комплектный development-FFmpeg в `tools/bin` и добавляет его в PATH только
текущего процесса; FFprobe всё равно устанавливается отдельно.
Real-ESRGAN для обычной нарезки не требуется.

## Проверки

```sh
python tools/check_ci.py
python tools/check_ci.py --gui-only
```

Первая команда проверяет зависимости/синтаксис, создаёт недостающие синтетические
видео и запускает unittest. Неполный набор fixtures не перезаписывается.
Вторая требует графическую сессию и готовые fixtures; проверяет настоящее
окно, настройки, потоки, отмену при закрытии, темы и навигацию.
На Linux предусмотрен запуск через Xvfb в CI.

Журналы и JSON-итоги — в `tests/ci-artifacts/`; они исключены из Git.
Текущая локальная база — 148 unit/media integration тестов и четыре GUI-проверки.
CI подготовлен для Ubuntu 24.04 / Python 3.10 и 3.12, Windows 2022 / Python 3.12,
macOS 15 / Python 3.12. GitHub remote пока не подключён, Actions не запускались.

Большинство тестов нейросети используют имитацию движка и настоящий FFmpeg.
Реальные проверки Apple M5 описаны отдельно в [отчёте приёмки](ACCEPTANCE_2026-11-19.md).
Их нельзя переносить на другие GPU или на длительные видео.

## Где искать код

| Файл | Назначение |
|---|---|
| `motion_splitter.py` | Запуск и совместимые импорты функций ядра |
| `gui.py` | Состояния окна, задачи, настройки, обработка событий |
| `gui_layout.py` | Компоновка, подписи и привязка элементов к настройкам |
| `gui_components.py` | Прокрутка, темы, списки, раскрывающиеся секции и числовые поля |
| `processing.py` | Задачи нарезки и улучшения без GUI |
| `video_analysis.py` | Детектор, автонастройка и интервалы движения |
| `video_export.py`, `media_probe.py` | Экспорт и проверка видео/аудио |
| `video_enhance.py`, `video_timing.py` | Покадровое улучшение и временные метки |
| `external_tools.py`, `output_safety.py` | Внешние процессы, отмена и безопасное сохранение |
| `settings_store.py` | Настройки и миграция схемы 6 |
| `task_ui.py`, `job_contracts.py`, `batch_report.py` | Состояния, события, контракт задач и отчёты |

[Алгоритм и ограничения](REFERENCE.md), [тестовые материалы](../tests/fixtures/README.md).
Личные видео не должны использоваться в автоматических тестах или попадать в Git.

## Сборка

См. [инструкцию упаковки](../packaging/README.md). Сборки выполняются на целевой ОС:
Windows — Inno Setup EXE, macOS — PKG, Linux — DEB. Нужен проверенный vendor-комплект
FFmpeg/FFprobe с происхождением, контрольными суммами и уведомлениями.

`--development` разрешает непроверенный для распространения vendor-манифест
только для локальной проверки. Он не делает сборку пригодной к публикации.
Иконки воспроизводятся командой `python tools/build_icons.py`.
