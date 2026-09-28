"""Строгий разбор ответа модели семантических предложений и его схема (спека
`2026-09-28-semantic-suggestions-design.md` §2.2, §2.6).

Задача 3 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). Чистая функция:
в базу не ходит, от ORM не зависит. Потребитель — исполнитель захвата (worker,
следующая задача), вызывающий `parse_model_answer` на каждый ответ модели;
`AnswerSchemaError.detail` ложится в `semantic_job_attempts.validation_error`
(без полного текста ответа — он хранится отдельно в `raw_response`).

Разбор НЕ извлекает JSON регулярным поиском по тексту (в отличие от
нестрогого `parse` из замера `tasks/catalog-pilot-2026-09-18/
exp_family_assign.py`, который так делал) — допустимо ровно два обрамления:
весь ответ после `strip()` — один JSON-объект, либо весь ответ — один блок
```` ```json ```` … ```` ``` ```` (метка `json` в любом регистре или без
метки), внутри которого после `strip()` ровно один объект. Любой текст
вокруг, иная метка ограды или второй объект следом — `extra_text`; невалидный
JSON — `not_json`; JSON-значение не-объект — `bad_type`.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from services.semantic_request import CandidateFamily

#: Версия JSON-схемы ответа, не отправляемой провайдеру (спека §2.2) —
#: аудиторская колонка `response_schema_version`; растёт при смене состава
#: или семантики полей `ModelAnswer`.
RESPONSE_SCHEMA_VERSION = 1

#: Обрамление ответа блоком ```` ```<метка>\n...\n``` ```` на весь ответ
#: (после `strip()`) целиком — единственная допустимая ограда; метка
#: захватывается для отдельной проверки (json/без метки — можно, любая
#: другая — `extra_text`).
_FENCE_RE = re.compile(r"^```([^\n`]*)\n(.*)\n```$", re.DOTALL)

_SYSTEM_NAME = "система"

ErrorCode = Literal[
    "not_json",
    "extra_text",
    "missing_field",
    "bad_type",
    "unknown_family",
    "bad_confidence",
    "empty_name",
    "empty_reason",
]

#: Обязательные ключи ответа (спека §2.2): лишние ключи допустимы и
#: игнорируются — модель иногда добавляет поле.
_REQUIRED_FIELDS: tuple[str, ...] = ("family_id", "new_family_name", "confidence", "reason")


class AnswerSchemaError(Exception):
    """Постоянная ошибка разбора/схемы ответа модели (спека §2.6: не
    получает повтора в поколении — воспринимается как политика, а не довод
    о детерминизме). `detail` — краткое человекочитаемое описание по-русски,
    без текста ответа целиком."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code: ErrorCode = code
        self.detail = detail


@dataclass(frozen=True)
class ModelAnswer:
    """Разобранный и провалидированный ответ модели (спека §2.2).

    `family_id is None` значит «новая семья» — как обычная (`new_family_name`
    задано, `is_system=False`), так и системная строка (`is_system=True`,
    `new_family_name` — текст «СИСТЕМА», как пришло). При совпадении с
    существующей семьёй `family_id` — её id, а `new_family_name` — всегда
    `None` (поле игнорируется моделью же по правилам промпта)."""

    family_id: int | None
    new_family_name: str | None
    is_system: bool
    confidence: Decimal
    reason: str


def _extract_json_text(raw: str) -> str:
    """Возвращает текст, который предстоит разобрать как ровно один
    JSON-объект: снятый блок ```` ```json ```` … ```` ``` ```` либо весь
    ответ как есть — обрамление одним блоком или без него, но не то и другое
    вперемешку (спека §2.2). Ограда с посторонней меткой — `extra_text`."""
    stripped = raw.strip()
    match = _FENCE_RE.match(stripped)
    if match is None:
        return stripped
    label = match.group(1).strip()
    if label and label.casefold() != "json":
        raise AnswerSchemaError("extra_text", f"неизвестная метка ограды кода: {label!r}")
    return match.group(2).strip()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """`object_pairs_hook` для `json.JSONDecoder`: по умолчанию модуль `json`
    при повторе ключа молча берёт последнее значение — для строгой
    валидации (спека §2.2) это двусмысленный ответ, а не совпадение;
    повтор — ошибка разбора `not_json`, а не результат с одним из двух
    значений."""
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise AnswerSchemaError("not_json", f"повторяющийся ключ в ответе: {key!r}")
        seen[key] = value
    return seen


def _parse_single_object(json_text: str) -> dict:
    """Разбирает `json_text` как ровно один JSON-объект: `raw_decode` с
    проверкой хвоста — так объект не извлекается регулярным поиском из
    окружающего текста (спека §2.2). Числа — `parse_float`/`parse_constant`
    в `Decimal`, целые — обычным `int` (умолчание `parse_int`); повторяющийся
    ключ отвергает `_reject_duplicate_keys`."""
    decoder = json.JSONDecoder(
        parse_float=Decimal,
        parse_constant=Decimal,
        object_pairs_hook=_reject_duplicate_keys,
    )
    try:
        obj, end = decoder.raw_decode(json_text)
    except json.JSONDecodeError as exc:
        raise AnswerSchemaError("not_json", f"ответ не разбирается как JSON: {exc}") from exc
    tail = json_text[end:].strip()
    if tail:
        raise AnswerSchemaError("extra_text", "после JSON-объекта остался текст")
    if not isinstance(obj, dict):
        raise AnswerSchemaError("bad_type", "разобранный JSON — не объект")
    return obj


def _parse_family_id(value: object, candidates: Sequence[CandidateFamily]) -> int:
    """Возвращает «сырой» `family_id` из ответа: `0` — «новая» (обрабатывает
    вызывающий), положительный — обязан быть id из ПЕРЕДАННОГО снимка
    кандидатов. `bool` — не целое (в Python `True`/`False` — подкласс `int`,
    поэтому проверяется первым, до `isinstance(int)`)."""
    if isinstance(value, bool):
        raise AnswerSchemaError("bad_type", "family_id — bool, а не целое число")
    if not isinstance(value, int):
        raise AnswerSchemaError("bad_type", "family_id — не целое число")
    if value < 0:
        raise AnswerSchemaError("unknown_family", f"family_id отрицательный: {value}")
    if value != 0 and value not in {c.id for c in candidates}:
        raise AnswerSchemaError(
            "unknown_family", f"family_id {value} не входит в переданный список кандидатов"
        )
    return value


def _parse_confidence(value: object) -> Decimal:
    """`confidence` — `int` или `Decimal` (не `bool`!, `bool` — подкласс
    `int` в Python, но не число ответа), приводится к `Decimal`, обязан быть
    конечным и лежать в `[0, 1]` включительно; любое другое — `bad_confidence`
    (план, задача 3: нечисловое значение и значение вне диапазона — один и
    тот же код). NaN/Infinity
    доходят сюда тоже `Decimal` (через `parse_constant`) — их отсеивает
    проверка `is_finite()`, а не отдельная ветка типа."""
    if isinstance(value, bool):
        raise AnswerSchemaError("bad_confidence", "confidence — bool, а не число")
    if isinstance(value, int):
        confidence = Decimal(value)
    elif isinstance(value, Decimal):
        confidence = value
    else:
        raise AnswerSchemaError("bad_confidence", "confidence не является числом")
    if not confidence.is_finite():
        raise AnswerSchemaError("bad_confidence", "confidence не является конечным числом")
    if confidence < 0 or confidence > 1:
        raise AnswerSchemaError("bad_confidence", f"confidence вне диапазона [0, 1]: {confidence}")
    return confidence


def _parse_name(value: object, raw_family_id: int) -> tuple[str | None, bool]:
    """При `raw_family_id != 0` поле игнорируется целиком — результат
    `(None, False)`, без проверки типа/содержимого: по правилам промпта модель
    заполняет `new_family_name` только при `family_id = 0`, при совпадении с
    семьёй его содержимое ничего не значит. При `0` — по промпту `null`
    ставится, если имени нет, поэтому `null` — то же «имени нет», что пустая
    или пробельная строка: `empty_name`. Любое другое не-строковое значение
    (число, список, `bool`) — `bad_type`. Непустая после `strip()` строка
    «СИСТЕМА» (без учёта регистра) даёт `is_system=True`."""
    if raw_family_id != 0:
        return None, False
    if value is None:
        raise AnswerSchemaError(
            "empty_name", "new_family_name отсутствует (null) при family_id = 0"
        )
    if not isinstance(value, str):
        raise AnswerSchemaError("bad_type", "new_family_name — не строка при family_id = 0")
    stripped = value.strip()
    if not stripped:
        raise AnswerSchemaError("empty_name", "new_family_name пуст при family_id = 0")
    is_system = stripped.casefold() == _SYSTEM_NAME.casefold()
    return stripped, is_system


def _parse_reason(value: object) -> str:
    """`reason` — непустая после `strip()` строка; не-строка — `bad_type`."""
    if not isinstance(value, str):
        raise AnswerSchemaError("bad_type", "reason — не строка")
    stripped = value.strip()
    if not stripped:
        raise AnswerSchemaError("empty_reason", "reason пуст")
    return stripped


def parse_model_answer(raw: str, candidates: Sequence[CandidateFamily]) -> ModelAnswer:
    """Строгий разбор и схемная валидация ответа модели (спека §2.2, §2.6).

    Порядок проверок детерминирован: обрамление → JSON → тип → обязательные
    поля → `family_id` → `confidence` → имя → `reason`; каждый код ошибки
    нарушает ровно один шаг — так одному входу соответствует один код, а не
    первый попавшийся из нескольких нарушенных. `candidates` —
    снимок, ОТПРАВЛЕННЫЙ этому контексту (не текущее состояние справочника),
    им проверяется `family_id`."""
    json_text = _extract_json_text(raw)
    obj = _parse_single_object(json_text)

    missing = [key for key in _REQUIRED_FIELDS if key not in obj]
    if missing:
        raise AnswerSchemaError(
            "missing_field", f"нет обязательных полей: {', '.join(missing)}"
        )

    raw_family_id = _parse_family_id(obj["family_id"], candidates)
    confidence = _parse_confidence(obj["confidence"])
    new_family_name, is_system = _parse_name(obj["new_family_name"], raw_family_id)
    reason = _parse_reason(obj["reason"])

    return ModelAnswer(
        family_id=None if raw_family_id == 0 else raw_family_id,
        new_family_name=new_family_name,
        is_system=is_system,
        confidence=confidence,
        reason=reason,
    )
