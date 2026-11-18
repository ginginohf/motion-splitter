"""Supported upstream ncnn-vulkan models and explicit invocation contract."""
from pathlib import Path
import tempfile
from PIL import Image
from external_tools import ToolError, find_realesrgan

MODELS = {
    "realesrgan-x4plus": (4,),
    "realesrgan-x4plus-anime": (4,),
    "realesrnet-x4plus": (4,),
    "realesr-animevideov3": (2, 3, 4),
}


def configuration(executable, model, scale, tile, gpuid):
    if model not in MODELS:
        raise ToolError(f"Модель {model!r} не поддерживается стандартным ncnn-vulkan. "
                        "Выберите модель из списка; файлы .pth не подходят.")
    if type(scale) is not int or scale not in MODELS[model]:
        raise ToolError(f"Для {model} допустимый масштаб: {MODELS[model]}.")
    if type(tile) is not int or not (tile == 0 or 32 <= tile <= 8192):
        raise ToolError("Tile должен быть 0 (авто) или целым числом от 32 до 8192.")
    if type(gpuid) is not int or not 0 <= gpuid <= 255:
        raise ToolError("GPU ID должен быть целым числом от 0 до 255.")
    executable = find_realesrgan(executable)
    directory = Path(executable).parent / "models"
    stem = f"{model}-x{scale}" if model == "realesr-animevideov3" else model
    for suffix in (".param", ".bin"):
        path = directory / (stem + suffix)
        if len(str(path).encode('utf-8')) >= 256:
            raise ToolError("Путь модели слишком длинный для ncnn-vulkan: " + str(path))
        try:
            with path.open('rb') as stream:
                if not stream.read(1):
                    raise OSError("пустой файл")
        except OSError as error:
            raise ToolError(f"Не удалось прочитать модель {path}: {error}. "
                            "Распакуйте каталог models рядом с программой.") from error
    return [executable, '-m', str(directory), '-n', model, '-s', str(scale),
            '-t', str(tile), '-g', str(gpuid), '-f', 'png']


def check_image(path, expected_size):
    try:
        with Image.open(path) as img:
            if img.format != 'PNG' or img.size != expected_size:
                raise ValueError(f"ожидался PNG {expected_size}, получен {img.format} {img.size}")
            img.verify()
        with Image.open(path) as img:
            img.load()
    except (OSError, ValueError, SyntaxError) as error:
        raise ToolError(f"Некорректный кадр Real-ESRGAN {path}: {error}") from error


def preflight(executable, model, scale, tile, gpuid, runner):
    """Exercise the actual model/GPU before extracting a full video."""
    runner.check_cancel()
    command = configuration(executable, model, scale, tile, gpuid)
    runner.log_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='esrgan-probe-', dir=runner.log_directory) as temp:
        source, target = Path(temp) / 'input.png', Path(temp) / 'output.png'
        Image.new('RGB', (32, 32), (80, 120, 160)).save(source)
        runner.run(command + ['-i', str(source), '-o', str(target)],
                   'Real-ESRGAN: пробный запуск модели/GPU', timeout=120)
        check_image(target, (32 * scale, 32 * scale))
    runner.note('Real-ESRGAN', f'Проверена конфигурация: {command}')


def validate_frames(source_dir, target_dir, scale, runner):
    expected = sorted(source_dir.glob('*.png'))
    actual = sorted(target_dir.glob('*.png'))
    if not expected or [p.name for p in expected] != [p.name for p in actual]:
        raise ToolError('Real-ESRGAN: набор выходных кадров не совпадает с исходным.')
    for index, source in enumerate(expected, 1):
        runner.check_cancel()
        if source.name != f'{index:08d}.png':
            raise ToolError('Нарушена последовательность исходных кадров.')
        with Image.open(source) as img:
            width, height = img.size
        check_image(target_dir / source.name, (width * scale, height * scale))
