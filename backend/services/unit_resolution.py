"""Разрешение единиц измерения при импорте и матчинге (AGENTS.md §4).

Единица — часть идентичности каталожной работы: уникальность `catalog_positions`
объявлена по паре `(normalized_job_title, COALESCE(unit_id, -1))`, и `unit_norm`
обязательно входит в `cache_key` матчинга. Поэтому разрешение алиасов нужно
одному и тому же ответу в обоих местах — отсюда общий модуль.

Отличие от `crud.units.load_alias_map`: та функция резолвит алиас до **базовой**
единицы своей размерности (кг → т) — это правильно для пересчёта величин, но
неправильно для идентичности. «Кладка, кг» и «кладка, т» — разные расценки,
поэтому здесь алиас резолвится в **свою** единицу, а `unit_norm` — её `code`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from crud.units import normalize_unit_key
from models import UnitAlias, UnitOfMeasure

#: Значение `unit_norm`, когда единицы нет. Зафиксировано §4: «'' если единицы нет».
NO_UNIT_NORM = ""


@dataclass(frozen=True)
class ResolvedUnit:
    """Результат разрешения единицы измерения."""

    raw_text: str | None
    """Исходный текст из файла — он ложится в `matching_cache.unit_text`."""

    unit_id: int | None
    """FK на `units_of_measure`; None, если единицы нет либо она неизвестна."""

    unit_norm: str
    """Каноническое имя (`units_of_measure.code`) либо `''`."""

    status: str
    """`absent` — единицы в файле нет; `known` — алиас разрешён; `unknown` — нет."""

    @property
    def is_unknown(self) -> bool:
        return self.status == "unknown"


_ABSENT = ResolvedUnit(raw_text=None, unit_id=None, unit_norm=NO_UNIT_NORM, status="absent")


class UnitResolver:
    """Разрешает текст единицы в `(unit_id, unit_norm)` по таблице алиасов.

    Карта алиасов читается один раз на импорт: справочник маленький, а обращений —
    по числу позиций.

    **Решение фазы 4 по неизвестной единице** (`docs/phase4-import.md` §2.3):
    неизвестная единица даёт `unit_id = NULL` и `unit_norm = ''`, то есть
    трактуется как «единицы нет», плюс предупреждение в `import_jobs.warnings`.
    Новая единица НЕ создаётся: `units_of_measure` — курируемый справочник
    физических размерностей (`dimension` NOT NULL, CHECK по шести значениям), и
    размерность произвольной строки из ячейки неизвестна — её пришлось бы
    выдумать. Штатное лечение — добавить алиас в справочник (данные, не код) и
    перезагрузить смету.
    """

    def __init__(self, db: Session) -> None:
        rows = db.execute(
            select(UnitAlias.raw_text, UnitAlias.unit_id, UnitOfMeasure.code).join(
                UnitOfMeasure, UnitOfMeasure.id == UnitAlias.unit_id
            )
        ).all()
        self._by_key: dict[str, tuple[int, str]] = {
            normalize_unit_key(raw_text): (unit_id, code) for raw_text, unit_id, code in rows
        }
        self._norm_by_unit_id: dict[int, str] = {
            unit_id: code for _raw, unit_id, code in rows
        }
        self.unknown_counts: Counter[str] = Counter()

    def resolve(self, raw: object) -> ResolvedUnit:
        """Разрешает значение ячейки «единица измерения».

        Args:
            raw: значение из JSON парсера (строка, число или None).

        Returns:
            `ResolvedUnit`; неизвестные тексты дополнительно копятся в
            `unknown_counts` для агрегированного предупреждения.
        """
        if raw is None:
            return _ABSENT

        text = str(raw).strip()
        if not text:
            return _ABSENT

        key = normalize_unit_key(text)
        if not key:
            return _ABSENT

        found = self._by_key.get(key)
        if found is None:
            self.unknown_counts[text] += 1
            return ResolvedUnit(
                raw_text=text, unit_id=None, unit_norm=NO_UNIT_NORM, status="unknown"
            )

        unit_id, code = found
        return ResolvedUnit(raw_text=text, unit_id=unit_id, unit_norm=code, status="known")

    def unit_norm_for_id(self, unit_id: int | None) -> str:
        """Обратное отображение: `unit_id` → `unit_norm`.

        Нужно там, где исходного текста единицы уже нет, а есть только строка БД
        (повторный матчинг, ручные решения Review).
        """
        if unit_id is None:
            return NO_UNIT_NORM
        return self._norm_by_unit_id.get(unit_id, NO_UNIT_NORM)

    def unknown_warnings(self) -> list[str]:
        """Агрегированные предупреждения о неизвестных единицах.

        Одно предупреждение на уникальный текст, а не на позицию: в смете
        2,5 тыс. строк, и построчные предупреждения утопили бы всё остальное.
        """
        return [
            f"Единица измерения «{text}» не найдена ни среди канонических, ни среди "
            f"алиасов (позиций: {count}). Такие позиции импортированы без единицы "
            "(unit_id = NULL) — добавьте алиас в справочник единиц и перезагрузите смету, "
            "иначе они не сойдутся с каталожной работой в нужной единице."
            for text, count in sorted(self.unknown_counts.items())
        ]
