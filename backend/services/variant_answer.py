"""Формы ответа модели для заданий `family_schema` и `context_values` (спека
`2026-10-02-catalog-variants-design.md` §2.3, §2.6).

Обе формы входят в тело запроса (`response_format`) и потому в его хэш: смена
любой из них меняет `request_hash`. Строгий режим провайдера требует
`additionalProperties: false` и перечисления ВСЕХ ключей в `required`; пустое
значение допускается только как `null`, а не как пустая строка.
"""
from __future__ import annotations

from typing import Final

#: Порядковые номера параметров схемы семьи (спека §2.2: от 0 до 3 параметров).
_ORDINALS: Final = [1, 2, 3]

#: `{"parameters": [{"ordinal", "name", "values": [...]}]}`; число параметров
#: (0–3), число значений (1–8) и непустота проверяются при разборе ответа, а не
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
#: `source` — `name`, `path` или `null`.
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
                            "source": {"type": ["string", "null"], "enum": ["name", "path", None]},
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
