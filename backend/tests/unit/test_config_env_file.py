"""Тесты резолва env_file в Settings (независимость от CWD)."""
from pathlib import Path

import pydantic
import pytest

from config import Settings


def test_env_file_is_absolute():
    """env_file — абсолютный путь, иначе значения зависят от CWD процесса."""
    env_file = Settings.model_config["env_file"]
    assert Path(env_file).is_absolute()


def test_env_file_points_at_backend_dir():
    """env_file указывает на backend/.env рядом с config.py.

    Обе части сравнения приводим к resolve(): config.py строит путь через
    Path(__file__).parent (не resolve()), и symlink/junction где-либо в пути
    чекаута дал бы ложное расхождение, не связанное с поведением под тестом.
    """
    env_file = Path(Settings.model_config["env_file"])
    assert env_file.name == ".env"
    assert env_file.parent.resolve() == Path(__file__).resolve().parent.parent.parent


def test_app_env_defaults_to_dev(monkeypatch):
    """APP_ENV по умолчанию dev — fail-safe: dev защищён из коробки.

    _env_file=None изолирует от dotenv-источника: одного delenv недостаточно,
    поскольку env_file теперь абсолютный и указывает на реальный backend/.env
    — если там когда-нибудь появится APP_ENV=prod (как в test_db_extra_targets_
    empty_env_means_empty_list с DB_EXTRA_TARGETS), тест был бы зелёным в CI
    (файла там нет) и красным на машине разработчика. _env_file=None проверено
    эмпирически: обходит dotenv-источник и не задевает env-источник (delenv
    выше гасит его отдельно), SECRET_KEY здесь берётся из os.environ (conftest
    задаёт его через setdefault до сбора тестов).
    """
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    assert Settings(_env_file=None).APP_ENV == "dev"


def test_app_env_rejects_invalid_value(monkeypatch):
    """Невалидное значение APP_ENV падает на валидации, а не молча."""
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(pydantic.ValidationError):
        Settings()


def test_db_extra_targets_empty_env_means_empty_list(monkeypatch):
    """Пустое значение в окружении означает «список пуст».

    Пиним через setenv, а не delenv: env_file абсолютный, и Settings() прочитал бы
    backend/.env, где после MANUAL-задачи (Task 2) лежит непустой список. Тогда
    тест был бы зелёным в CI (файла нет) и красным на машине разработчика.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    assert Settings().DB_EXTRA_TARGETS == ""
