"""Файловое хранилище исходных XLSX (AGENTS.md §8).

Абстракция `Storage` (save/get/delete) поверх локальной директории. S3-реализация
добавляется позже без правки вызывающих мест — поэтому в интерфейсе нет ни путей,
ни файловых объектов ОС: `get` отдаёт двоичный поток, а не `Path`.

Требования §8, которые здесь закреплены:

* имя на диске — непрозрачный ключ (uuid4 hex), оригинальное имя живёт только в
  `import_jobs.filename`;
* никаких путей от клиента: ключ проверяется регуляркой перед любым обращением к
  ФС, поэтому «../» в ключе не может выйти за корень хранилища;
* выдача файла — только через авторизованный эндпоинт, статики над этой
  директорией нет.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from config import settings

log = logging.getLogger(__name__)

#: Формат ключа: 32 hex-символа (uuid4().hex). Ключ приходит из БД, но проверяется
#: всё равно — это последний барьер между строкой из внешнего мира и путём в ФС.
KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class StorageKeyError(ValueError):
    """Ключ не похож на ключ хранилища (защита от обхода пути)."""


class StorageFileNotFound(FileNotFoundError):
    """Файла с таким ключом в хранилище нет.

    Штатная ситуация: файлы error-jobs удаляются по ретенции (§8), а сама запись
    job остаётся навсегда — это аудит.
    """


def new_key() -> str:
    """Новый непрозрачный ключ файла."""
    return uuid.uuid4().hex


class Storage(ABC):
    """Хранилище исходных файлов: save/get/delete (AGENTS.md §8)."""

    @abstractmethod
    def save(self, data: bytes) -> str:
        """Сохраняет содержимое и возвращает непрозрачный ключ."""

    @abstractmethod
    def get(self, key: str) -> BinaryIO:
        """Открывает файл на чтение.

        Raises:
            StorageKeyError: ключ не соответствует формату.
            StorageFileNotFound: файла с таким ключом нет.
        """

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Удаляет файл. Возвращает True, если файл был и удалён."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Есть ли файл с таким ключом."""


class LocalStorage(Storage):
    """Локальная директория. Путь — из настроек (`STORAGE_DIR`)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not KEY_PATTERN.fullmatch(key):
            raise StorageKeyError(f"Некорректный ключ хранилища: {key!r}")
        return self.root / key

    def save(self, data: bytes) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        key = new_key()
        target = self._path(key)
        # Запись через временное имя + rename: недописанный файл никогда не
        # виден под своим ключом (иначе импорт мог бы прочитать половину XLSX).
        tmp = target.with_name(f"{key}.part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        return key

    def get(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise StorageFileNotFound(f"Файл {key} не найден в хранилище") from exc

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


_storage: Storage | None = None


def get_storage() -> Storage:
    """FastAPI-зависимость: хранилище приложения.

    Синглтон, а не новый объект на запрос: `LocalStorage` без состояния, но
    подмена в тестах делается через `app.dependency_overrides`, как для `get_db`.
    """
    global _storage
    if _storage is None:
        _storage = LocalStorage(settings.STORAGE_DIR)
    return _storage
