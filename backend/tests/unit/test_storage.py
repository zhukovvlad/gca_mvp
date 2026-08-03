"""Файловое хранилище (AGENTS.md §8)."""
from __future__ import annotations

import pytest

from storage import (
    KEY_PATTERN,
    LocalStorage,
    StorageFileNotFound,
    StorageKeyError,
    new_key,
)


class TestKeys:
    def test_key_is_opaque_uuid_hex(self):
        assert KEY_PATTERN.fullmatch(new_key())

    def test_keys_are_unique(self):
        assert len({new_key() for _ in range(100)}) == 100


class TestRoundTrip:
    def test_save_then_get_returns_same_bytes(self, tmp_path):
        storage = LocalStorage(tmp_path)
        key = storage.save(b"PK\x03\x04payload")
        with storage.get(key) as fh:
            assert fh.read() == b"PK\x03\x04payload"

    def test_save_creates_root_directory(self, tmp_path):
        storage = LocalStorage(tmp_path / "deep" / "storage")
        key = storage.save(b"x")
        assert (tmp_path / "deep" / "storage" / key).is_file()

    def test_file_name_on_disk_is_the_key_only(self, tmp_path):
        """Оригинальное имя живёт только в БД (§8): на диске — непрозрачный ключ."""
        storage = LocalStorage(tmp_path)
        key = storage.save(b"x")
        assert [p.name for p in tmp_path.iterdir()] == [key]

    def test_no_partial_files_left_after_save(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.save(b"x")
        assert not list(tmp_path.glob("*.part"))

    def test_exists_and_delete(self, tmp_path):
        storage = LocalStorage(tmp_path)
        key = storage.save(b"x")
        assert storage.exists(key) is True
        assert storage.delete(key) is True
        assert storage.exists(key) is False

    def test_delete_is_idempotent(self, tmp_path):
        storage = LocalStorage(tmp_path)
        key = storage.save(b"x")
        storage.delete(key)
        assert storage.delete(key) is False

    def test_get_missing_key_raises(self, tmp_path):
        storage = LocalStorage(tmp_path)
        with pytest.raises(StorageFileNotFound):
            storage.get(new_key())

    def test_exists_on_empty_root(self, tmp_path):
        """Директории может не быть вовсе — это не ошибка, а «файла нет»."""
        storage = LocalStorage(tmp_path / "missing")
        assert storage.exists(new_key()) is False


class TestKeyValidation:
    """Никаких путей от клиента (§8): всё, что не uuid-hex, отвергается."""

    @pytest.mark.parametrize(
        "bad_key",
        [
            "../../../etc/passwd",
            "..",
            "",
            "abc",
            "0123456789abcdef0123456789abcdeG",   # не hex
            "0123456789ABCDEF0123456789ABCDEF",   # верхний регистр
            "0123456789abcdef0123456789abcdef0",  # 33 символа
            "0123456789abcdef0123456789abcde/",
            "sub/0123456789abcdef0123456789abcd",
        ],
    )
    def test_bad_keys_rejected_everywhere(self, tmp_path, bad_key):
        storage = LocalStorage(tmp_path)
        for operation in (storage.get, storage.delete, storage.exists):
            with pytest.raises(StorageKeyError):
                operation(bad_key)

    def test_traversal_cannot_reach_outside_root(self, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_bytes(b"secret")
        storage = LocalStorage(tmp_path / "storage")
        with pytest.raises(StorageKeyError):
            storage.delete("../secret.txt")
        assert outside.exists()
