"""Централизованная настройка логгирования."""
import logging
import logging.handlers
import os
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging():
    """Настройка root-логгера: вывод в stdout + ротация файлов."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Файл с ротацией: 10 МБ x 5 файлов
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Stdout
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # Отдельный файл только для ошибок
    error_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "errors.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.WARNING)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Очищаем предыдущие хендлеры (на случай reload)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.addHandler(error_handler)

    # Усмиряем шумные библиотеки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(f"Логгирование инициализировано (уровень: {log_level}, директория: {LOG_DIR})")
