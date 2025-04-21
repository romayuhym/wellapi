import logging
import os
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

# Конфігурація
WATCH_DIRECTORIES = ["."]  # Директорії для відстеження змін
WATCH_EXTENSIONS = [".py"]  # Розширення файлів для відстеження


class ModuleReloader(FileSystemEventHandler):
    def __init__(self, server):
        self.server = server
        self.last_reload_time = time.time()
        self.reload_delay = 1  # Мінімальний час між перезавантаженнями (секунди)

    def get_module_name_from_path(self, file_path):
        """Отримати назву модуля Python з шляху до файлу"""
        try:
            path = Path(file_path)

            # Перевіряємо, чи це файл Python
            if path.suffix != ".py":
                return None

            # Отримуємо абсолютний шлях до директорії проекту
            project_dir = Path(os.getcwd())

            # Обчислюємо відносний шлях до файлу
            rel_path = path.relative_to(project_dir)

            # Перетворюємо шлях у назву модуля
            module_path = str(rel_path.with_suffix(""))
            module_name = module_path.replace(os.sep, ".")

            return module_name
        except Exception as e:
            logging.error(f"Помилка при визначенні назви модуля: {e}")
            return None

    def on_any_event(self, event):
        # Перевіряємо, чи файл має потрібне розширення
        if event.is_directory:
            return

        file_ext = os.path.splitext(event.src_path)[1].lower()
        if file_ext not in WATCH_EXTENSIONS:
            return

        # Запобігаємо багаторазовим перезавантаженням
        current_time = time.time()
        if (current_time - self.last_reload_time) < self.reload_delay:
            return

        self.last_reload_time = current_time

        logging.info(f"Зміни виявлено у файлі: {event.src_path}")

        # Отримуємо назву модуля з шляху до файлу
        module_name = self.get_module_name_from_path(event.src_path)
        if not module_name:
            return

        self.server.on_reload()


def run_with_reloader(server):
    reloader = ModuleReloader(server)
    observer = Observer()

    for directory in WATCH_DIRECTORIES:
        abs_path = os.path.abspath(directory)
        logging.info(f"Відстеження змін у {abs_path}")
        observer.schedule(reloader, abs_path, recursive=True)

    observer.start()

    server.start_server()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Завершення роботи...")
        observer.stop()

    observer.join()
