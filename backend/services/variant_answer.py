"""Формы ответа модели для заданий `family_schema` и `context_values` (спека
`2026-10-02-catalog-variants-design.md` §2.3, §2.6).

Обе формы входят в тело запроса (`response_format`) и потому в его хэш: смена
любой из них меняет `request_hash`. Строгий режим провайдера требует
`additionalProperties: false` и перечисления ВСЕХ ключей в `required`; пустое
значение допускается только как `null`, а не как пустая строка.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from services.semantic_answer import (
    AnswerSchemaError,
    ErrorCode,
    _extract_json_text,
    _parse_single_object,
)

if TYPE_CHECKING:
    from services.variant_request import SchemaParameterIn

#: Порядковые номера параметров схемы семьи (спека §2.2: от 0 до 3 параметров).
_ORDINALS: Final = [1, 2, 3]

#: `{"parameters": [{"ordinal", "name", "values": [...]}]}`; число параметров
#: (0–3), число значений (1–30) и непустота проверяются при разборе ответа, а не
#: схемой: строгий режим поддерживает не все ограничения размера.
SCHEMA_RESPONSE_FORMAT: Final = {
    "type": "json_schema",
    "json_schema": {
        "name": "family_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ordinal": {"type": "integer", "enum": _ORDINALS},
                            "name": {"type": "string"},
                            "values": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["ordinal", "name", "values"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["parameters"],
            "additionalProperties": False,
        },
    },
}

#: `{"values": [{"ordinal", "kind", "value", "source"}]}` — тегированный
#: объект: `kind` ∈ `value|new|conflict|none`; `value` — строка или `null`;
#: `source` — `name`, `path` или `null` (через `anyOf`: строгий режим провайдера
#: отвергает `enum` рядом с `type`-списком).
VALUES_RESPONSE_FORMAT: Final = {
    "type": "json_schema",
    "json_schema": {
        "name": "context_values",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ordinal": {"type": "integer", "enum": _ORDINALS},
                            "kind": {
                                "type": "string",
                                "enum": ["value", "new", "conflict", "none"],
                            },
                            "value": {"type": ["string", "null"]},
                            "source": {
                                "anyOf": [
                                    {"type": "string", "enum": ["name", "path"]},
                                    {"type": "null"},
                                ],
                            },
                        },
                        "required": ["ordinal", "kind", "value", "source"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["values"],
            "additionalProperties": False,
        },
    },
}


#: Версия формы ответа открытия семей (спека 3б §2.3): пишется на задании как
#: `response_schema_version`; растёт вместе со сменой `DISCOVERY_RESPONSE_FORMAT`.
DISCOVERY_RESPONSE_SCHEMA_VERSION: Final = "discovery:1"

#: `{"groups": [...], "not_work": [...], "family_categories": [...]}`: строгий
#: режим, все ключи обязательны, пустое значение — явный `null`. Непустота
#: `names` задана схемой и повторно проверяется при разборе; связи с
#: отправленным материалом (номера, семьи, справочник) схема выразить не может и
#: их проверяет `parse_discovery_answer`.
DISCOVERY_RESPONSE_FORMAT: Final = {
    "type": "json_schema",
    "json_schema": {
        "name": "family_discovery",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "family_id": {"type": ["integer", "null"]},
                            "title": {"type": ["string", "null"]},
                            "definition": {"type": ["string", "null"]},
                            "category_id": {"type": ["integer", "null"]},
                            "similar_family_id": {"type": ["integer", "null"]},
                            "names": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 1,
                            },
                        },
                        "required": [
                            "family_id",
                            "title",
                            "definition",
                            "category_id",
                            "similar_family_id",
                            "names",
                        ],
                        "additionalProperties": False,
                    },
                },
                "not_work": {"type": "array", "items": {"type": "integer"}},
                "family_categories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "family_id": {"type": "integer"},
                            "category_id": {"type": "integer"},
                        },
                        "required": ["family_id", "category_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["groups", "not_work", "family_categories"],
            "additionalProperties": False,
        },
    },
}


def values_response_format_for(ordinals: Iterable[int], base: dict | None = None) -> dict:
    """Формат ответа `context_values` для схемы с данными порядковыми номерами:
    копия `base` (по умолчанию `VALUES_RESPONSE_FORMAT`), у которой перечисление
    `ordinal` — ровно номера этой схемы по возрастанию (допустимы пропуски:
    `[1, 3]` после удаления параметра). Модель не может вернуть объект для
    параметра, которого в схеме нет."""
    fmt = copy.deepcopy(VALUES_RESPONSE_FORMAT if base is None else base)
    item = fmt["json_schema"]["schema"]["properties"]["values"]["items"]
    item["properties"]["ordinal"]["enum"] = sorted(ordinals)
    return fmt

# ---------------------------------------------------------------------------
#  Строгий разбор ответов
# ---------------------------------------------------------------------------

#: Новые коды отказа сверх кодов разбора ответа фичи «Семантические
#: предложения»; класс исключения тот же — `AnswerSchemaError`.
VariantErrorCode = Literal[
    "bad_ordinal",
    "empty_value",
    "bad_count",
    "bad_kind",
    "value_not_in_list",
    # Разбор ответа открытия семей (спека 3б §2.3); `unknown_family` — из кодов
    # разбора предложений.
    "unknown_category",
    "bad_index",
    "duplicate_index",
    "duplicate_family",
    "unexpected_value",
]

MAX_PARAMETERS: Final = 3
MAX_VALUES_PER_PARAMETER: Final = 30

ValueKind = Literal["value", "new", "conflict", "none"]
_KINDS: Final = ("value", "new", "conflict", "none")
_SOURCES: Final = ("name", "path")

_SCHEMA_KEYS: Final = ("ordinal", "name", "values")
_VALUE_KEYS: Final = ("ordinal", "kind", "value", "source")


@dataclass(frozen=True)
class SchemaAnswer:
    """Разобранный ответ `family_schema`: 0-3 параметра по возрастанию
    `ordinal`."""

    parameters: tuple[SchemaParameterIn, ...]


@dataclass(frozen=True)
class ValueItem:
    ordinal: int
    kind: ValueKind
    value: str | None
    source: Literal["name", "path"] | None


@dataclass(frozen=True)
class ValuesAnswer:
    """Разобранный ответ `context_values`: по объекту на каждый параметр схемы,
    по возрастанию `ordinal`."""

    items: tuple[ValueItem, ...]


def _fail(code: ErrorCode | VariantErrorCode, detail: str) -> AnswerSchemaError:
    """Исключение того же класса, что у фичи 2; аннотация кода там уже, чем
    набор кодов этого модуля, поэтому код передаётся через `Any`."""
    code_any: Any = code
    return AnswerSchemaError(code_any, detail)


def _object_of(raw: str) -> dict:
    return _parse_single_object(_extract_json_text(raw))


def _check_keys(obj: dict, keys: tuple[str, ...], where: str) -> None:
    """Все ключи на месте (`missing_field`), и лишних нет (`bad_type`: ответ по
    строгой схеме с `additionalProperties: false` с лишним ключом схему не
    соблюл)."""
    missing = [key for key in keys if key not in obj]
    if missing:
        raise _fail("missing_field", f"{where}: нет обязательных полей: {', '.join(missing)}")
    extra = sorted(set(obj) - set(keys))
    if extra:
        raise _fail("bad_type", f"{where}: лишние ключи: {', '.join(extra)}")


def _int_field(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_type", f"{where}: не целое число")
    return value


def _list_field(value: object, where: str) -> list:
    if not isinstance(value, list):
        raise _fail("bad_type", f"{where}: не массив")
    return value


def _dict_item(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise _fail("bad_type", f"{where}: не объект")
    return value


def _nonblank(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise _fail("bad_type", f"{where}: не строка")
    if not value.strip():
        raise _fail("empty_value", f"{where}: пустое или пробельное значение")
    return value


def parse_schema_answer(raw: str) -> SchemaAnswer:
    """Строгий разбор ответа `family_schema` (спека §2.3, §2.6).

    Обрамление, единственный объект и повтор ключа — как у фичи 2. Затем:
    ключи верхнего уровня, число параметров (0-3), на каждый параметр — ключи,
    `ordinal`, имя, значения (1-30, каждое непустое); в конце `ordinal` обязаны
    образовать `1..N` без дублей и пропусков. Строки не нормализуются: имя и
    значения возвращаются как пришли (нормализация — при записи в базу)."""
    from services.variant_request import SchemaParameterIn

    obj = _object_of(raw)
    _check_keys(obj, ("parameters",), "ответ")
    items = _list_field(obj["parameters"], "parameters")
    if len(items) > MAX_PARAMETERS:
        raise _fail("bad_count", f"параметров {len(items)}, допустимо не больше {MAX_PARAMETERS}")

    parsed: list[SchemaParameterIn] = []
    for position, item in enumerate(items):
        where = f"parameters[{position}]"
        param = _dict_item(item, where)
        _check_keys(param, _SCHEMA_KEYS, where)
        ordinal = _int_field(param["ordinal"], f"{where}.ordinal")
        if ordinal not in range(1, MAX_PARAMETERS + 1):
            raise _fail("bad_ordinal", f"{where}: ordinal {ordinal} вне 1-{MAX_PARAMETERS}")
        name = _nonblank(param["name"], f"{where}.name")
        values = _list_field(param["values"], f"{where}.values")
        if not 1 <= len(values) <= MAX_VALUES_PER_PARAMETER:
            raise _fail(
                "bad_count",
                f"{where}: значений {len(values)}, допустимо 1-{MAX_VALUES_PER_PARAMETER}",
            )
        checked = tuple(
            _nonblank(value, f"{where}.values[{index}]") for index, value in enumerate(values)
        )
        parsed.append(SchemaParameterIn(ordinal=ordinal, name=name, values=checked))

    ordinals = sorted(p.ordinal for p in parsed)
    if ordinals != list(range(1, len(parsed) + 1)):
        raise _fail("bad_ordinal", f"ordinal параметров {ordinals}: ожидалось 1..{len(parsed)}")
    return SchemaAnswer(parameters=tuple(sorted(parsed, key=lambda p: p.ordinal)))


def _parse_value_item(item: object, position: int) -> ValueItem:
    """Форма одного объекта ответа `context_values`; связь с параметрами схемы
    проверяет вызывающий."""
    where = f"values[{position}]"
    obj = _dict_item(item, where)
    _check_keys(obj, _VALUE_KEYS, where)
    ordinal = _int_field(obj["ordinal"], f"{where}.ordinal")
    kind = obj["kind"]
    if not isinstance(kind, str):
        raise _fail("bad_type", f"{where}.kind: не строка")
    if kind not in _KINDS:
        raise _fail("bad_kind", f"{where}: неизвестный kind {kind!r}")
    value = obj["value"]
    if value is not None and not isinstance(value, str):
        raise _fail("bad_type", f"{where}.value: не строка и не null")
    source = obj["source"]
    if source is not None and not isinstance(source, str):
        raise _fail("bad_type", f"{where}.source: не строка и не null")
    if source is not None and source not in _SOURCES:
        raise _fail("bad_kind", f"{where}: неизвестный source {source!r}")

    if kind in ("value", "new"):
        if source is None:
            raise _fail("bad_kind", f"{where}: при kind={kind} source обязателен")
        if value is None or not value.strip():
            raise _fail("empty_value", f"{where}: при kind={kind} value обязателен и непуст")
    else:
        if source is not None:
            raise _fail("bad_kind", f"{where}: при kind={kind} source запрещён")
        if value is not None:
            raise _fail("bad_kind", f"{where}: при kind={kind} value запрещён")
    return ValueItem(ordinal=ordinal, kind=kind, value=value, source=source)  # type: ignore[arg-type]


def parse_values_answer(raw: str, parameters: Sequence[SchemaParameterIn]) -> ValuesAnswer:
    """Строгий разбор ответа `context_values` (спека §2.3, §2.6, шаги (1а) и
    (2)). `parameters` — схема версии, по которой строился запрос.

    Множество `ordinal` ответа обязано совпасть с множеством `ordinal`
    параметров: пропуск, дубль и чужой номер — `bad_ordinal`. Для тега `value`
    значение сравнивается со списком своего параметра точно, без нормализации;
    тег `new` со значением, совпавшим со значением списка, ошибкой разбора не
    является (канонизация — при записи)."""
    obj = _object_of(raw)
    _check_keys(obj, ("values",), "ответ")
    raw_items = _list_field(obj["values"], "values")
    items = [_parse_value_item(item, position) for position, item in enumerate(raw_items)]

    answered = sorted(item.ordinal for item in items)
    expected = sorted(p.ordinal for p in parameters)
    if answered != expected:
        raise _fail(
            "bad_ordinal", f"ordinal ответа {answered} не совпадают с параметрами схемы {expected}"
        )

    lists = {p.ordinal: p.values for p in parameters}
    for item in items:
        if item.kind == "value" and item.value not in lists[item.ordinal]:
            raise _fail(
                "value_not_in_list",
                f"ordinal {item.ordinal}: значение не из списка параметра",
            )
    return ValuesAnswer(items=tuple(sorted(items, key=lambda i: i.ordinal)))


# ---------------------------------------------------------------------------
#  Ответ открытия семей (спека 3б §2.3)
# ---------------------------------------------------------------------------

_DISCOVERY_KEYS: Final = ("groups", "not_work", "family_categories")
_DISCOVERY_GROUP_KEYS: Final = (
    "family_id",
    "title",
    "definition",
    "category_id",
    "similar_family_id",
    "names",
)
_DISCOVERY_CATEGORY_KEYS: Final = ("family_id", "category_id")


@dataclass(frozen=True)
class DiscoverySent:
    """Что ушло модели: по этому множеству проверяются ссылки ответа. Номера
    имён — `1..names_count`; семьи — активные семьи единицы, из них отдельно
    «без категории»; категории — справочник целиком."""

    names_count: int
    active_family_ids: frozenset[int]
    uncategorized_family_ids: frozenset[int]
    category_ids: frozenset[int]


@dataclass(frozen=True)
class DiscoveryGroup:
    """Группа ответа. `family_id IS NULL` — новый черновик (имя, определение и
    категория заданы, `similar_family_id` — активная семья, на которую группа
    похожа, или `None`); иначе — имена, отнесённые к активной семье, остальные
    четыре поля `None`. `names` — номера имён по возрастанию."""

    family_id: int | None
    title: str | None
    definition: str | None
    category_id: int | None
    similar_family_id: int | None
    names: tuple[int, ...]


@dataclass(frozen=True)
class DiscoveryAnswer:
    """Разобранный ответ открытия. `not_work` и номера групп — по возрастанию;
    `unassigned` — пропущенные номера имён, не ошибка."""

    groups: tuple[DiscoveryGroup, ...]
    not_work: tuple[int, ...]
    family_categories: tuple[tuple[int, int], ...]
    unassigned: tuple[int, ...]


def _optional_int(value: object, where: str) -> int | None:
    return None if value is None else _int_field(value, where)


def _required_text(value: object, where: str) -> str:
    """Новый черновик: имя и определение — строка, непустая после `strip`;
    `null` и пробельная строка — `empty_value`, не строка — `bad_type`."""
    if value is None:
        raise _fail("empty_value", f"{where}: обязательно для новой семьи")
    return _nonblank(value, where)


def _claim_names(
    values: object, where: str, names_count: int, seen: set[int], *, nonempty: bool
) -> tuple[int, ...]:
    """Номера имён одного места ответа: целые, в `1..N`, каждый номер ответа —
    только в одном месте (две группы, группа и «не работа», повтор внутри
    одного места)."""
    items = _list_field(values, where)
    if nonempty and not items:
        raise _fail("bad_count", f"{where}: пустой список номеров")
    numbers: list[int] = []
    for position, value in enumerate(items):
        number = _int_field(value, f"{where}[{position}]")
        if not 1 <= number <= names_count:
            raise _fail("bad_index", f"{where}[{position}]: номер {number} вне 1..{names_count}")
        if number in seen:
            raise _fail("duplicate_index", f"{where}[{position}]: номер {number} уже занят")
        seen.add(number)
        numbers.append(number)
    return tuple(sorted(numbers))


def _parse_discovery_group(
    item: object,
    position: int,
    sent: DiscoverySent,
    seen_names: set[int],
    seen_families: set[int],
) -> DiscoveryGroup:
    where = f"groups[{position}]"
    group = _dict_item(item, where)
    _check_keys(group, _DISCOVERY_GROUP_KEYS, where)
    family_id = _optional_int(group["family_id"], f"{where}.family_id")
    category_id = _optional_int(group["category_id"], f"{where}.category_id")
    similar = _optional_int(group["similar_family_id"], f"{where}.similar_family_id")

    if family_id is None:
        title = _required_text(group["title"], f"{where}.title")
        definition = _required_text(group["definition"], f"{where}.definition")
        if category_id not in sent.category_ids:
            raise _fail("unknown_category", f"{where}.category_id: не из справочника")
        if similar is not None and similar not in sent.active_family_ids:
            raise _fail("unknown_family", f"{where}.similar_family_id: не из активных семей")
    else:
        if family_id not in sent.active_family_ids:
            raise _fail("unknown_family", f"{where}.family_id: не из активных семей")
        if family_id in seen_families:
            raise _fail("duplicate_family", f"{where}.family_id: семья уже в другой группе")
        seen_families.add(family_id)
        for field in ("title", "definition", "category_id", "similar_family_id"):
            if group[field] is not None:
                raise _fail("unexpected_value", f"{where}.{field}: у группы активной семьи null")
        title = definition = None

    names = _claim_names(group["names"], f"{where}.names", sent.names_count, seen_names, nonempty=True)
    return DiscoveryGroup(
        family_id=family_id,
        title=title,
        definition=definition,
        category_id=category_id,
        similar_family_id=similar,
        names=names,
    )


def parse_discovery_answer(raw: str, sent: DiscoverySent) -> DiscoveryAnswer:
    """Строгий разбор ответа открытия семей (спека 3б §2.3, «Разбор»).

    Обрамление, единственный объект и повтор ключа — как у разборов 3а; лишний
    ключ — `bad_type`. Дальше — связи с отправленным: новый черновик без имени
    или определения, категория не из справочника, похожая и названная семья не
    из активных, поля группы активной семьи не `null`, семья в двух группах,
    номер вне `1..N` или в двух местах, `family_categories` с семьёй не «без
    категории», с повтором или с категорией не из справочника — схемная ошибка.
    Пропущенные номера имён и пропущенная семья в `family_categories` ошибкой не
    являются: первые идут в `unassigned`."""
    obj = _object_of(raw)
    _check_keys(obj, _DISCOVERY_KEYS, "ответ")

    seen_names: set[int] = set()
    seen_families: set[int] = set()
    groups = tuple(
        _parse_discovery_group(item, position, sent, seen_names, seen_families)
        for position, item in enumerate(_list_field(obj["groups"], "groups"))
    )
    not_work = _claim_names(obj["not_work"], "not_work", sent.names_count, seen_names, nonempty=False)

    categorized: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for position, item in enumerate(_list_field(obj["family_categories"], "family_categories")):
        where = f"family_categories[{position}]"
        pair = _dict_item(item, where)
        _check_keys(pair, _DISCOVERY_CATEGORY_KEYS, where)
        family_id = _int_field(pair["family_id"], f"{where}.family_id")
        category_id = _int_field(pair["category_id"], f"{where}.category_id")
        if family_id in categorized:
            raise _fail("duplicate_family", f"{where}.family_id: семья повторена")
        if family_id not in sent.uncategorized_family_ids:
            raise _fail("unknown_family", f"{where}.family_id: не из перечня «без категории»")
        if category_id not in sent.category_ids:
            raise _fail("unknown_category", f"{where}.category_id: не из справочника")
        categorized.add(family_id)
        pairs.append((family_id, category_id))

    unassigned = tuple(n for n in range(1, sent.names_count + 1) if n not in seen_names)
    return DiscoveryAnswer(
        groups=groups,
        not_work=not_work,
        family_categories=tuple(pairs),
        unassigned=unassigned,
    )
