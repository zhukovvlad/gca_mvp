"""Ответы API: деньги — строками, а не числами с плавающей точкой (AGENTS.md §3).

**Найденный дефект (фаза 5).** `DecimalJSONResponse` как `default_response_class`
приложения выглядит достаточным, но сам по себе §3 не обеспечивает: когда хендлер
возвращает `dict`, FastAPI прогоняет его через `jsonable_encoder` **до** рендера
ответа, а тот превращает `Decimal` во `float` (`ENCODERS_BY_TYPE[Decimal]`).
Замер:

```
jsonable_encoder(Decimal("1234567890.12"))  →  1234567890.12   (float)
DecimalJSONResponse({}).render({...})       →  b'{"total_amount":"1234567890.12"}'
```

То есть класс ответа делает всё правильно, но до него значение доезжает уже
испорченным. До фазы 5 это не проявлялось: деньги в ответах не возвращал никто —
`job_response` отдаёт только счётчики и строки.

**Решение:** хендлер, в ответе которого есть деньги, возвращает `decimal_json(...)`
явно. Экземпляр `Response` FastAPI пропускает мимо `jsonable_encoder`, и `Decimal`
доходит до `render` живым. Выбрано именно это, а не построчное приведение
`Decimal → str` в CRUD-слое: приведение по полю надо помнить на каждом поле, и
первое же забытое даёт молчаливый float, тогда как здесь решение принимается один
раз на эндпоинт и работает на любой вложенности — это важно для фазы 6, где
матрица несёт деньги в каждой ячейке.

Энкодер намеренно строгий: он умеет только `Decimal` и бросает `TypeError` на
`datetime`. Это защита, а не недоделка — даты обязаны приводиться к ISO в
доменном слое (`crud.common.iso`), и забывчивость видна сразу, а не как «дата
в неожиданном формате» на экране.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse


def _decimal_encoder(obj: Any) -> Any:
    # Деньги в JSON — строки, не float (AGENTS.md §3)
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class DecimalJSONResponse(JSONResponse):
    """Стандартный JSONResponse с поддержкой Decimal → str для dict-ответов."""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), default=_decimal_encoder,
        ).encode("utf-8")


def decimal_json(content: Any, status_code: int = status.HTTP_200_OK) -> DecimalJSONResponse:
    """Ответ, в котором `Decimal` доезжает до JSON строкой.

    Обязателен для всего, что несёт деньги: `contracts.total_amount`,
    `rate_standards.standard_unit_rate`, `inflation_index`. Без него FastAPI
    отдал бы float (см. модульную документацию).
    """
    return DecimalJSONResponse(content, status_code=status_code)
