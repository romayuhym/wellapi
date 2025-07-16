import importlib
import os
import sys
from pathlib import Path


def import_app(app: str, handlers_dir: str):
    app_modal, app_name = app.split(":")
    app_path = f"{os.path.abspath(Path(app_modal))}.py"

    spec = importlib.util.spec_from_file_location(app_modal, app_path)
    main = importlib.util.module_from_spec(spec)
    sys.modules[app_modal] = main
    spec.loader.exec_module(main)

    load_handlers(handlers_dir)

    return getattr(main, app_name)


def load_handlers(handlers_dir: str):
    handlers_path = Path(handlers_dir)
    handlers_module = handlers_path.name
    base_path = Path(os.path.dirname(handlers_path))

    if not handlers_path.exists() or not handlers_path.is_dir():
        print(f"Директорія {handlers_path} не існує")
        return

    # Додаємо шлях до директорії у sys.path для імпорту
    sys.path.insert(0, str(base_path))

    # Імпортуємо всі Python файли з директорії
    for file_path in handlers_path.glob("*.py"):
        if file_path.stem == "__init__":
            continue

        module_name = f"{handlers_module}.{file_path.stem}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        try:
            importlib.import_module(module_name)
        except ImportError as e:
            print(f"Помилка імпорту {module_name}: {e}")
            raise e
