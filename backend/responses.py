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

**Почему `format(obj, 'f')`, а не `str(obj)` (Ф6a фазы 7, спека §2.1).** `str`
сохраняет «идеальную экспоненту» `Decimal`, и в JSON уезжает форма, которой
контракт §3 не обещает: обещано `-?цифры[.цифры]`, а приезжало `0E+2`. Фронтовая
`DECIMAL_RE` E-нотацию не разбирает и по своему правилу «неожиданный формат
отдаём как есть» печатала её дословно — «0E+2 ₽» на стенде. Замер:

```
Decimal('0') / Decimal('50000.00')  ->  Decimal('0E+2')
str(...)                            ->  '0E+2'      (контракт нарушен)
format(..., 'f')                    ->  '0'
format(Decimal('10.00'), 'f')       ->  '10.00'     (не меняется)
```

Источник не только питоновские деления: psycopg отдаёт `1e-20` из `numeric` как
`Decimal('1E-20')`, то есть экспонента приезжает и из базы.

**Граница правки.** `format(x, 'f')` меняет изображение ТОЛЬКО значений в
экспоненциальной записи; обычные суммы со шкалой не переписываются (замер выше,
и он же закреплён тестом). Разворот экспоненты линеен по величине —
`1E+100000` даёт строку в 100 001 символ, — но такая форма до энкодера не
доходит: PostgreSQL отдаёт положительные экспоненты развёрнутыми, а арифметика
паспорта ограничена масштабами операндов из базы. **Недостижимость проверена
замером, поэтому защиты против такого входа здесь нет:** код, который нельзя
исполнить, нельзя и проверить снятием — а защита, которую нечем уронить,
защитой не является (`docs/insights/verifying-guards.md` §7).

Прежняя редакция этого абзаца ссылалась на «§11 `AGENTS.md`, запрещающий
оборонительный код против недостижимого входа». Такого правила в `AGENTS.md`
нет: §11 — каталог известных граблей, а не раздел правил, и слов
«оборонительный»/«недостижимый» в документе не встречается вовсе. Ссылка была
ложной, и это дороже отсутствующей: она разошлась копированием (та же формула
осталась в плане `docs/superpowers/plans/2026-08-10-passport-number-display.md`
и однажды переехала в `crud/dashboard.py`), а грепающий §11 не находит там
ничего и либо теряет время, либо считает несуществующее правило действующим.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import Response, status
from fastapi.responses import JSONResponse


def _decimal_encoder(obj: Any) -> Any:
    # Деньги в JSON — строки, не float (AGENTS.md §3)
    if isinstance(obj, Decimal):
        return format(obj, "f")
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


# --- Ответы-выгрузки xlsx (перенесены из `routers/reports.py`, план фичи
# «Выгрузка Изменения КП», Task 1) ---
#
# Второе написание этого правила в другом роутере (`routers/tenders.py`,
# задача 5 того же плана) разъехалось бы с первым: оба хелпера здесь — общая
# точка правды для файла, а не два параллельных.

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_response(content: bytes, filename: str) -> Response:
    """Ответ с готовой книгой xlsx: `bytes` целиком в памяти, не `StreamingResponse`.

    Starlette итерирует файловый объект `StreamingResponse` построчно и не
    закрывает хендл (грабля фазы 4) — здесь книга уже собрана, и правильный
    ответ отдаёт её содержимое целиком.

    Имя файла уезжает в `filename*=UTF-8''<percent>` (percent-кодирование):
    русские буквы и `/` в номере тендера/договора искажаются либо ломают путь
    в ASCII-форме `filename=`, поэтому она здесь не заводится вовсе.
    """
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def safe_filename_part(value: str, *, fallback: str) -> str:
    """Убирает из части имени файла то, что ломает путь.

    Номер договора/тендера приходит из карточки и может содержать `/` или `\\`
    (нумерация вида «12/2025» встречается), а такой символ в имени файла
    Windows и часть браузеров трактуют как разделитель пути.

    `fallback` — ОБЯЗАТЕЛЬНЫЙ именованный параметр, а не зашитое в хелпере
    слово: книга тендера, подписавшаяся словом «договор» по умолчанию общего
    хелпера, была бы скрытой договорённостью, а не контрактом каждого
    вызывающего (план фичи «Выгрузка Изменения КП», решение 2).
    """
    for bad in '/\\:*?"<>|':
        value = value.replace(bad, "-")
    return value.strip() or fallback
