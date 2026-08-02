"""Очистка и нормализация текста.

Перенос `app/excel_parser/sanitize_text.py` из `parser_tender_xlsx@0e178c0`.

Главное отличие от исходника — поведение при недоступной spaCy.
В исходнике модель грузилась на import, а при её отсутствии
`normalize_job_title_with_lemmatization` молча возвращала нелемматизированный
текст. Для GCA это недопустимо: `normalized_job_title` — идентичность работы в
каталоге (AGENTS.md §4), и на нём же строятся `cache_key` матчинга. Молчаливая
деградация развела бы каталог, кэш и матчер (AGENTS.md §11: «Нормализация должна
быть детерминированной»). Поэтому здесь:

* модель грузится лениво, при первом вызове (импорт модуля остаётся дешёвым);
* отсутствие spaCy или модели — громкая ошибка `NormalizationUnavailableError`,
  а не тихий фолбэк;
* базовая очистка вынесена в отдельную функцию `basic_clean_job_title`,
  чтобы её можно было тестировать, не подменяя флаги.
"""

from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

# Модель лемматизации. Версии закреплены в pyproject.toml (spacy и
# ru-core-news-sm ставятся точными версиями): смена любой из них меняет
# результат нормализации, а значит требует переиздания ключей matching_cache
# и пересчёта catalog_positions.normalized_job_title (AGENTS.md §11).
SPACY_MODEL_NAME = "ru_core_news_sm"

_nlp = None
_nlp_lock = threading.Lock()


class NormalizationUnavailableError(RuntimeError):
    """spaCy или модель лемматизации недоступны.

    Нормализация — часть контракта данных, а не украшение: тихо отдать
    нелемматизированную строку значит записать в каталог другую «идентичность»
    работы. Поэтому единственный корректный ответ — отказ.
    """


def get_nlp():
    """Возвращает загруженный spaCy-пайплайн, загружая его при первом вызове.

    Загрузка идемпотентна и потокобезопасна; модель живёт до конца процесса.

    Raises:
        NormalizationUnavailableError: если spaCy не установлена или модель
            `ru_core_news_sm` не найдена.
    """
    global _nlp
    if _nlp is not None:
        return _nlp

    with _nlp_lock:
        if _nlp is not None:  # другой поток успел загрузить, пока мы ждали лок
            return _nlp
        try:
            import spacy
        except ImportError as exc:
            raise NormalizationUnavailableError(
                "Библиотека spaCy не установлена, а нормализация наименований работ "
                "обязательна: на ней держится идентичность позиций каталога. "
                "Установите зависимости backend (`just install-backend`)."
            ) from exc

        try:
            _nlp = spacy.load(SPACY_MODEL_NAME)
        except OSError as exc:
            raise NormalizationUnavailableError(
                f"Модель spaCy '{SPACY_MODEL_NAME}' не найдена. Она ставится как "
                "обычная зависимость backend (см. pyproject.toml) — выполните "
                "`just install-backend`."
            ) from exc

        log.info("Модель spaCy '%s' загружена", SPACY_MODEL_NAME)
        return _nlp


def sanitize_text(text: Any) -> Any:
    """Базовая очистка строки: `\\n` → пробел, удаление `\\r`, обрезка краёв.

    Кавычки не трогаются. Не-строки возвращаются без изменений.

    Примеры:
        sanitize_text("  Пример\\nтекста\\r\\n с пробелами  ") == "Пример текста  с пробелами"
        sanitize_text(None) is None
        sanitize_text(123) == 123
    """
    if isinstance(text, str):
        sanitized_string = text.replace("\n", " ")
        sanitized_string = sanitized_string.replace("\r", "").strip()
        return sanitized_string

    return text


def sanitize_object_and_address_text(text: Any) -> Any:
    """Очистка названий объектов и адресов: убрать точки, нижний регистр, обрезка.

    Кавычки не трогаются, `\\n`/`\\r` не заменяются. Не-строки возвращаются
    без изменений.

    Примеры:
        sanitize_object_and_address_text("Ул. Ленина, д. 5.") == "ул ленина, д 5"
        sanitize_object_and_address_text(None) is None
    """
    if isinstance(text, str):
        return text.replace(".", "").lower().strip()
    return sanitize_text(text)


def basic_clean_job_title(text: str | None) -> str | None:
    """Очистка наименования работы до лемматизации.

    Шаги: нижний регистр → снятие базовой Markdown-разметки → замена пунктуации
    пробелами (дефис внутри слова сохраняется) → схлопывание пробелов.

    Returns:
        Очищенная строка либо None, если на входе None или после очистки пусто.
    """
    if text is None:
        return None

    cleaned_text = str(text).lower()
    cleaned_text = re.sub(r"(\*\*|__)(.+?)(\1)", r"\2", cleaned_text)
    cleaned_text = re.sub(r"(?<![\wА-Яа-я])(\*|_)(.+?)(\1)(?![\wА-Яа-я])", r"\2", cleaned_text)
    cleaned_text = cleaned_text.replace("---", " ")
    cleaned_text = re.sub(r"[^\w\s-]", " ", cleaned_text)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

    return cleaned_text or None


@lru_cache(maxsize=8192)
def _lemmatize(cleaned_text: str) -> str:
    """Лемматизирует уже очищенную строку.

    Кэш здесь безопасен: функция чистая (один вход — один выход, состояние
    пайплайна не меняется), поэтому результат не зависит от того, был ли кэш
    прогрет. Смысл кэша чисто экономический: в смете наименования сильно
    повторяются — на реальном образце 2576 позиций дают 1070 уникальных строк.
    """
    doc = get_nlp()(cleaned_text)
    lemmatized_words = [
        token.lemma_ for token in doc if not token.is_punct and not token.is_space and token.lemma_
    ]
    processed_text = " ".join(lemmatized_words) if lemmatized_words else cleaned_text
    return re.sub(r"\s+", " ", processed_text).strip()


def normalize_job_title_with_lemmatization(text: str | None) -> str | None:
    """Нормализует наименование работы: очистка + лемматизация spaCy.

    Результат — то самое представление строки, по которому работают
    `catalog_positions.normalized_job_title`, точный матчинг и `cache_key`
    (AGENTS.md §4–§5). Функция детерминирована: одна строка на входе всегда
    даёт один результат.

    Args:
        text: исходное наименование.

    Returns:
        Нормализованный текст либо None (если на входе None или результат пуст).

    Raises:
        NormalizationUnavailableError: если spaCy/модель недоступны.
    """
    cleaned_text = basic_clean_job_title(text)
    if cleaned_text is None:
        return None

    return _lemmatize(cleaned_text) or None


def prepare_for_fts_query(text: str | None) -> str | None:
    """Готовит строку лемм для `to_tsquery('simple', ...)`.

    'монтаж кабель силовой' → 'монтаж & кабель & силовой'.
    """
    lemmatized = normalize_job_title_with_lemmatization(text)
    if not lemmatized:
        return None
    return " & ".join(lemmatized.split())
