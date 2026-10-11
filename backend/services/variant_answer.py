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
DISCOVERY_RESPONSE_SCHEMA_VERSION: Final = "discovery:2"

#: `{"groups": [...], "not_work": [...], "family_categories": [...]}`: строгий
#: режим, все ключи обязательны, пустое значение — явный `null`. Группа несёт
#: `draft_ref` — номер черновика этого открытия, который она продолжает (спека 3б
#: §2.3.1). Связи с отправленным материалом (номера, семьи, черновики,
#: справочник) схема выразить не может: их проверяет разбор порции.
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
                            "draft_ref": {"type": ["integer", "null"]},
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
                            "draft_ref",
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
_DISCOVERY_CATEGORY_KEYS: Final = ("family_id", "category_id")


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


# ---------------------------------------------------------------------------
#  Открытие порциями (спека 3б §2.3.1)
# ---------------------------------------------------------------------------

#: Предельная длина имени и определения черновика: длиннее — режется с «…».
#: Предел нужен резерву: он ограничивает строку блока черновиков порции.
DRAFT_TITLE_MAX_CHARS: Final = 100
DRAFT_DEFINITION_MAX_CHARS: Final = 300

_CHUNK_GROUP_KEYS: Final = (
    "family_id",
    "draft_ref",
    "title",
    "definition",
    "category_id",
    "similar_family_id",
    "names",
)


@dataclass(frozen=True)
class ChunkSent:
    """Что ушло модели в одной порции: по этому набору проверяются ссылки её
    ответа. `bounds` — номера имён порции `(a, b)` включительно (`(1, 0)` — порция
    без имён); `draft_numbers` — номера черновиков из блока; перечень семей без
    категории непуст только в порции 1."""

    bounds: tuple[int, int]
    draft_numbers: frozenset[int]
    active_family_ids: frozenset[int]
    uncategorized_family_ids: frozenset[int]
    category_ids: frozenset[int]


@dataclass(frozen=True)
class ChunkGroup:
    """Группа порции после проверки содержания. Ровно один из трёх видов:
    `family_id` — имена активной семьи (остальные поля `None`); `draft_ref` —
    имена в черновик этого открытия (остальные поля `None`); иначе — новая
    группа: имя, определение и категория заданы, `similar_family_id` — активная
    семья или `None`. `names` — номера из этой порции по возрастанию, без повторов."""

    family_id: int | None
    draft_ref: int | None
    title: str | None
    definition: str | None
    category_id: int | None
    similar_family_id: int | None
    names: tuple[int, ...]


@dataclass(frozen=True)
class ChunkAnswer:
    """Разобранный ответ одной порции: только то, что прошло проверку содержания.
    Имена, не попавшие ни в группу, ни в `not_work`, — остаток; отдельно они не
    хранятся (их считает `merge_chunks`)."""

    groups: tuple[ChunkGroup, ...]
    not_work: tuple[int, ...]
    family_categories: tuple[tuple[int, int], ...]


def _optional_text(value: object, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail("bad_type", f"{where}: не строка и не null")
    return value


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _int_list(values: object, where: str) -> list[int]:
    return [
        _int_field(value, f"{where}[{position}]")
        for position, value in enumerate(_list_field(values, where))
    ]


def parse_discovery_chunk(raw: str, sent: ChunkSent) -> ChunkAnswer:
    """Разбор ответа одной порции (спека 3б §2.3.1): строг к форме, снисходителен
    к содержанию.

    Форма — обрамление, единственный объект, повтор ключа, ключи верхнего уровня,
    группы и пар `family_categories`, типы значений — ошибка разбора
    (`AnswerSchemaError`). Содержание ошибок не бросает, а уводит свои имена в
    остаток (их не будет ни в одной группе и не в `not_work`): ссылка группы на
    семью не из отправленных (`family_id`, `similar_family_id`), на черновик не из
    блока, на категорию не из справочника; группа с `family_id` и `draft_ref`
    разом; новая группа без имени или определения после `strip`; номер вне
    порции; номер, встреченный в порции больше одного раза — все его вхождения.
    Остальные группы порции целы. У группы с `draft_ref` и у группы активной
    семьи прочие поля игнорируются. Пара `family_categories` пропускается, если
    семья не из перечня «без категории», категория не из справочника или семья
    встречена в ответе больше одного раза (все её пары)."""
    obj = _object_of(raw)
    _check_keys(obj, _DISCOVERY_KEYS, "ответ")

    items: list[tuple[int | None, int | None, str | None, str | None,
                      int | None, int | None, list[int]]] = []
    for position, item in enumerate(_list_field(obj["groups"], "groups")):
        where = f"groups[{position}]"
        group = _dict_item(item, where)
        _check_keys(group, _CHUNK_GROUP_KEYS, where)
        items.append(
            (
                _optional_int(group["family_id"], f"{where}.family_id"),
                _optional_int(group["draft_ref"], f"{where}.draft_ref"),
                _optional_text(group["title"], f"{where}.title"),
                _optional_text(group["definition"], f"{where}.definition"),
                _optional_int(group["category_id"], f"{where}.category_id"),
                _optional_int(group["similar_family_id"], f"{where}.similar_family_id"),
                _int_list(group["names"], f"{where}.names"),
            )
        )
    not_work_raw = _int_list(obj["not_work"], "not_work")
    pairs_raw: list[tuple[int, int]] = []
    for position, item in enumerate(_list_field(obj["family_categories"], "family_categories")):
        where = f"family_categories[{position}]"
        pair = _dict_item(item, where)
        _check_keys(pair, _DISCOVERY_CATEGORY_KEYS, where)
        pairs_raw.append(
            (
                _int_field(pair["family_id"], f"{where}.family_id"),
                _int_field(pair["category_id"], f"{where}.category_id"),
            )
        )

    first, last = sent.bounds
    occurrences: dict[int, int] = {}
    for entry in items:
        for number in entry[6]:
            occurrences[number] = occurrences.get(number, 0) + 1
    for number in not_work_raw:
        occurrences[number] = occurrences.get(number, 0) + 1

    def kept(numbers: Sequence[int]) -> tuple[int, ...]:
        return tuple(sorted(n for n in numbers if first <= n <= last and occurrences[n] == 1))

    groups: list[ChunkGroup] = []
    for family_id, draft_ref, title, definition, category_id, similar, numbers in items:
        names = kept(numbers)
        if not names:
            continue
        if family_id is not None and draft_ref is not None:
            continue
        if draft_ref is not None:
            if draft_ref not in sent.draft_numbers:
                continue
            groups.append(ChunkGroup(None, draft_ref, None, None, None, None, names))
        elif family_id is not None:
            if family_id not in sent.active_family_ids:
                continue
            groups.append(ChunkGroup(family_id, None, None, None, None, None, names))
        else:
            if _blank(title) or _blank(definition):
                continue
            if category_id not in sent.category_ids:
                continue
            if similar is not None and similar not in sent.active_family_ids:
                continue
            assert title is not None and definition is not None
            groups.append(
                ChunkGroup(None, None, title.strip(), definition.strip(), category_id, similar, names)
            )

    family_seen: dict[int, int] = {}
    for family_id, _category in pairs_raw:
        family_seen[family_id] = family_seen.get(family_id, 0) + 1
    pairs = tuple(
        (family_id, category_id)
        for family_id, category_id in pairs_raw
        if family_seen[family_id] == 1
        and family_id in sent.uncategorized_family_ids
        and category_id in sent.category_ids
    )
    return ChunkAnswer(groups=tuple(groups), not_work=kept(not_work_raw), family_categories=pairs)


def _cut_text(text: str, limit: int) -> str:
    """Не длиннее `limit` знаков: длиннее — режется, последний знак «…»."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def draft_key(title: str) -> str:
    """Ключ имени черновика для слияния одноимённых: пробелы свёрнуты, регистр не
    различается."""
    return " ".join(title.split()).casefold()


def merge_chunks(
    chunks: Sequence[ChunkAnswer], names_count: int, *, max_drafts: int
) -> DiscoveryAnswer:
    """Сборка ответов порций в `DiscoveryAnswer`, который проходит прежний
    `apply_discovery` (спека 3б §2.3.1, «Нормализация» и «Сборка ответа»).

    Группы идут в порядке порций и появления; новые группы нумеруются
    черновиками `1, 2, …` в порядке создания (по ним порция ссылается через
    `draft_ref`). Группа с `draft_ref` дописывает имена в свой черновик, а её поля
    игнорируются; новая группа (без `draft_ref`) сопоставляется с черновиком этого
    открытия в таком порядке (без учёта регистра и пробелов): (а) её полное имя
    равно полному имени черновика — дописывается в него; (б) иначе оно равно
    ОБРЕЗАННОМУ имени ровно одного черновика (модель повторила укороченную форму из
    блока) — дописывается в него, а равное обрезанному имени двух и более
    черновиков неоднозначно — имена группы в остаток; (в) иначе — новый черновик,
    даже если его обрезанное имя совпало с чужим. Ключ полного имени берётся ДО
    обрезки, поэтому два длинных имени с общим началом остаются разными черновиками;
    имя режется до `DRAFT_TITLE_MAX_CHARS`, определение — до
    `DRAFT_DEFINITION_MAX_CHARS`;
    новая группа сверх `max_drafts` черновиков уводит имена в остаток; группы
    одной активной семьи — из одной порции и из разных — сливаются в одну группу
    `existing`. Пары `family_categories` семьи, встреченной больше одного раза,
    отбрасываются все. Номера имён, не попавшие ни в группу, ни в `not_work`, —
    `unassigned`."""
    # Группа: ["new", [номер черновика, имя, определение, категория, похожая, имена]]
    # либо ["existing", [family_id, имена]]: порядок появления.
    order: list[tuple[str, int]] = []
    drafts: list[list] = []
    full_keys: dict[str, int] = {}
    cut_keys: dict[str, list[int]] = {}
    existing: dict[int, list[int]] = {}
    not_work: set[int] = set()
    pair_count: dict[int, int] = {}
    pairs: list[tuple[int, int]] = []

    for chunk in chunks:
        for group in chunk.groups:
            if group.family_id is not None:
                if group.family_id not in existing:
                    existing[group.family_id] = []
                    order.append(("existing", group.family_id))
                existing[group.family_id].extend(group.names)
            elif group.draft_ref is not None:
                if 1 <= group.draft_ref <= len(drafts):
                    drafts[group.draft_ref - 1][5].extend(group.names)
            else:
                assert group.title is not None and group.definition is not None
                title = _cut_text(group.title.strip(), DRAFT_TITLE_MAX_CHARS)
                definition = _cut_text(group.definition.strip(), DRAFT_DEFINITION_MAX_CHARS)
                full_key = draft_key(group.title)
                echoed = cut_keys.get(full_key, [])
                if full_key in full_keys:
                    drafts[full_keys[full_key]][5].extend(group.names)
                elif len(echoed) == 1:
                    drafts[echoed[0]][5].extend(group.names)
                elif len(echoed) > 1:
                    pass  # неоднозначное повторение укороченного имени: имена в остаток
                elif len(drafts) < max_drafts:
                    full_keys[full_key] = len(drafts)
                    cut_keys.setdefault(draft_key(title), []).append(len(drafts))
                    drafts.append(
                        [len(drafts) + 1, title, definition, group.category_id,
                         group.similar_family_id, list(group.names)]
                    )
                    order.append(("new", len(drafts)))
        not_work.update(chunk.not_work)
        for family_id, category_id in chunk.family_categories:
            pair_count[family_id] = pair_count.get(family_id, 0) + 1
            pairs.append((family_id, category_id))

    groups: list[DiscoveryGroup] = []
    placed: set[int] = set(not_work)
    for kind, ref in order:
        if kind == "existing":
            names = tuple(sorted(set(existing[ref])))
            groups.append(DiscoveryGroup(ref, None, None, None, None, names))
        else:
            _number, title, definition, category_id, similar, raw_names = drafts[ref - 1]
            names = tuple(sorted(set(raw_names)))
            groups.append(DiscoveryGroup(None, title, definition, category_id, similar, names))
        placed.update(names)
    return DiscoveryAnswer(
        groups=tuple(groups),
        not_work=tuple(sorted(not_work)),
        family_categories=tuple(p for p in pairs if pair_count[p[0]] == 1),
        unassigned=tuple(n for n in range(1, names_count + 1) if n not in placed),
    )
