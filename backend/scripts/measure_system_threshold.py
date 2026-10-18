"""Кривая порога автопринятия систем (спека 3б §2.7, решения 15 и 16).

Для порогов 0,80 ... 0,99 шагом 0,01 печатает: «проходит без человека» - долю
предложений систем с семьёй и уверенностью не ниже порога среди всех таких
предложений; «точность» - долю «модель права» среди сохранённых решений
человека с уверенностью не ниже порога; число этих решений. Затем - выполнено
ли условие достаточности (300 решений, из них не меньше 100 с уверенностью
не ниже 0,9) и сколько отказов исключено как отмена ожидания человеком.

Метки: `accepted`, `accepted_pending` - «модель права»; `rejected`,
`other_family` - «модель ошиблась»; `rejected` предложения, названного в
событии `context_family_pending` с исходом `cancelled` или `superseded`, -
отказ от смены семьи, а не ошибка модели: исключён и посчитан отдельно.
`auto_*`, `family_created`, предложения без решения и предложения работ
метками не бывают.

Скрипт только читает: транзакция открывается `SET TRANSACTION READ ONLY`, и
приложение он не импортирует (условие проверочного инструментария, `AGENTS.md`
§9.1) - только `sqlalchemy` и стандартная библиотека.

Запуск (из `backend/`, строка подключения - в окружении):
    DATABASE_URL=postgresql+psycopg://... PYTHONIOENCODING=utf-8 \\
        uv run python -m scripts.measure_system_threshold

`PYTHONIOENCODING` обязателен: скрипт печатает кириллицу, а консоль Windows по
умолчанию отдаёт её в cp1251 и роняет вывод `UnicodeEncodeError`.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

import sqlalchemy as sa

#: Достаточность (решение 16): всего решений и из них в верхней полосе.
MIN_LABELS: int = 300
MIN_HIGH_LABELS: int = 100
HIGH_CONFIDENCE: Decimal = Decimal("0.9")

#: Пороги кривой: 0,80 ... 0,99 шагом 0,01.
THRESHOLDS: tuple[Decimal, ...] = tuple(Decimal(n) / 100 for n in range(80, 100))

#: Знаков после запятой у долей в таблице и в тестах.
_SHARE_STEP = Decimal("0.0001")

_CORRECT_DECISIONS = ("accepted", "accepted_pending")
_WRONG_DECISIONS = ("rejected", "other_family")
_CANCELLING_OUTCOMES = ("cancelled", "superseded")

#: Предложения семьи контекстам-системам с семьёй и решением человека; для
#: `rejected` - с признаком «назван событием отмены ожидания».
_LABELS_SQL = sa.text(
    """
    SELECT s.id AS suggestion_id,
           s.confidence AS confidence,
           s.decision AS decision,
           EXISTS (
               SELECT 1
               FROM semantic_events e
               WHERE e.event_type = 'context_family_pending'
                 AND e.payload->>'outcome' IN ('cancelled', 'superseded')
                 AND e.payload->>'suggestion_id' = s.id::text
           ) AS pending_cancelled
    FROM family_suggestions s
    JOIN catalog_contexts c ON c.id = s.context_id
    WHERE c.semantic_kind = 'SYSTEM'
      AND s.family_id IS NOT NULL
      AND s.decision IN ('accepted', 'accepted_pending', 'rejected', 'other_family')
    ORDER BY s.id
    """
)

_CONFIDENCES_SQL = sa.text(
    """
    SELECT s.confidence AS confidence
    FROM family_suggestions s
    JOIN catalog_contexts c ON c.id = s.context_id
    WHERE c.semantic_kind = 'SYSTEM'
      AND s.family_id IS NOT NULL
    ORDER BY s.id
    """
)


@dataclass(frozen=True)
class Label:
    suggestion_id: int
    confidence: Decimal
    correct: bool


@dataclass(frozen=True)
class CurvePoint:
    threshold: Decimal
    pass_share: Decimal  # доля предложений систем с семьёй и уверенностью >= порога
    precision: Decimal | None  # доля «права» среди меток >= порога; None - меток нет
    labels: int


def load_labels(conn) -> tuple[list[Label], int]:
    """Метки кривой и число исключённых `rejected` отменённых ожиданий."""
    labels: list[Label] = []
    excluded = 0
    for row in conn.execute(_LABELS_SQL):
        if row.decision == "rejected" and row.pending_cancelled:
            excluded += 1
            continue
        labels.append(
            Label(
                suggestion_id=row.suggestion_id,
                confidence=Decimal(row.confidence),
                correct=row.decision in _CORRECT_DECISIONS,
            )
        )
    return labels, excluded


def load_confidences(conn) -> list[Decimal]:
    """Уверенности всех предложений систем с семьёй (любое решение, в том
    числе никакого)."""
    return [Decimal(row.confidence) for row in conn.execute(_CONFIDENCES_SQL)]


def _share(part: int, whole: int) -> Decimal:
    return (Decimal(part) / Decimal(whole)).quantize(_SHARE_STEP, rounding=ROUND_HALF_EVEN)


def curve(labels: Sequence[Label], confidences: Sequence[Decimal]) -> list[CurvePoint]:
    """Точка на каждый порог из `THRESHOLDS`. Без предложений доля прохода -
    ноль; без меток выше порога точность - `None`."""
    points: list[CurvePoint] = []
    for threshold in THRESHOLDS:
        passing = sum(1 for value in confidences if value >= threshold)
        above = [label for label in labels if label.confidence >= threshold]
        points.append(
            CurvePoint(
                threshold=threshold,
                pass_share=_share(passing, len(confidences)) if confidences else Decimal(0),
                precision=(
                    _share(sum(1 for label in above if label.correct), len(above))
                    if above
                    else None
                ),
                labels=len(above),
            )
        )
    return points


def sufficient(labels: Sequence[Label]) -> bool:
    """Решений не меньше `MIN_LABELS` и из них с уверенностью не ниже
    `HIGH_CONFIDENCE` не меньше `MIN_HIGH_LABELS`: обе границы обязательны."""
    high = sum(1 for label in labels if label.confidence >= HIGH_CONFIDENCE)
    return len(labels) >= MIN_LABELS and high >= MIN_HIGH_LABELS


@contextmanager
def connect_readonly(engine: sa.Engine) -> Iterator[sa.Connection]:
    """Соединение, чья единственная транзакция - только чтение: запись в ней
    база отвергает сама."""
    with engine.connect() as conn:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        try:
            yield conn
        finally:
            conn.rollback()


def _fmt(value: Decimal | None) -> str:
    return "-" if value is None else format(value, "f")


def render(labels: Sequence[Label], excluded: int, points: Sequence[CurvePoint]) -> str:
    lines = ["порог  проходит  точность  решений"]
    for point in points:
        lines.append(
            f"{point.threshold:.2f}   {_fmt(point.pass_share):<8}  "
            f"{_fmt(point.precision):<8}  {point.labels}"
        )
    high = sum(1 for label in labels if label.confidence >= HIGH_CONFIDENCE)
    verdict = "да" if sufficient(labels) else "нет"
    lines.append(
        f"Достаточность: {verdict} (решений {len(labels)} из {MIN_LABELS}, "
        f"с уверенностью не ниже {HIGH_CONFIDENCE}: {high} из {MIN_HIGH_LABELS})"
    )
    lines.append(f"Исключено отказов отменённых ожиданий: {excluded}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Кривая порога автопринятия систем (только чтение, DATABASE_URL из окружения)."
    )
    parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 2
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    engine = sa.create_engine(url)
    try:
        with connect_readonly(engine) as conn:
            labels, excluded = load_labels(conn)
            confidences = load_confidences(conn)
    finally:
        engine.dispose()
    print(render(labels, excluded, curve(labels, confidences)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
