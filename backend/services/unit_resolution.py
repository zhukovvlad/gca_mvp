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

import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from crud.units import normalize_unit_key
from models import UnitAlias, UnitOfMeasure

log = logging.getLogger(__name__)

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
    """Разрешает текст единицы в `(unit_id, unit_norm)` по справочнику единиц.

    Распознаются **канонические формы каждой единицы** (`code`, `name`, `symbol`)
    и **алиасы** из `unit_aliases`. Порядок наложения важен: сначала канонические
    формы, затем алиасы поверх — алиас курируется человеком и потому авторитетнее
    совпадения по названию.

    Только алиасов недостаточно, хотя обещание «ни среди канонических, ни среди
    алиасов» стоит в тексте предупреждения: у засеянного справочника коды `SET` и
    `MON` и названия «Метр», «Кв. метр», «Куб. метр», «Штука», «Килограмм»,
    «Литр» алиасов не имеют, и без канонических форм такая единица считалась бы
    неизвестной — то есть валидная единица молча теряла бы `unit_id`.

    Карта читается один раз на импорт: справочник маленький, а обращений — по
    числу позиций.

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
        self._by_key: dict[str, tuple[int, str]] = {}
        #: unit_id → каноническое имя. Строится по САМОМУ справочнику, а не по
        #: алиасам: единица без алиасов иначе давала бы unit_norm = '' и схлопывала
        #: бы разные каталожные пары в одну.
        self._norm_by_unit_id: dict[int, str] = {}
        self.unknown_counts: Counter[str] = Counter()

        units = db.execute(
            select(UnitOfMeasure.id, UnitOfMeasure.code, UnitOfMeasure.name, UnitOfMeasure.symbol)
            .order_by(UnitOfMeasure.id)
        ).all()
        for unit_id, code, name, symbol in units:
            self._norm_by_unit_id[unit_id] = code
            for form in (code, name, symbol):
                self._register(normalize_unit_key(form), unit_id, code, source="справочник")

        aliases = db.execute(
            select(UnitAlias.raw_text, UnitAlias.unit_id, UnitOfMeasure.code)
            .join(UnitOfMeasure, UnitOfMeasure.id == UnitAlias.unit_id)
            .order_by(UnitAlias.id)
        ).all()
        for raw_text, unit_id, code in aliases:
            # Алиас перекрывает каноническую форму: он заведён человеком осознанно.
            self._by_key[normalize_unit_key(raw_text)] = (unit_id, code)

    def _register(self, key: str, unit_id: int, code: str, *, source: str) -> None:
        """Заносит каноническую форму, не затирая чужую и не молча.

        Столкновение канонических форм разных единиц — дефект справочника
        (например, одинаковое название у двух строк). Тихо выбрать одну значило бы
        привязывать позиции к произвольной единице, поэтому побеждает первая по
        `id`, а расхождение попадает в лог.
        """
        if not key:
            return
        claimed = self._by_key.get(key)
        if claimed is not None and claimed[0] != unit_id:
            log.warning(
                "Форма единицы «%s» (%s) уже занята единицей %s; строка %s не будет "
                "распознаваться по этой форме — проверьте справочник единиц.",
                key,
                source,
                claimed[1],
                code,
            )
            return
        self._by_key[key] = (unit_id, code)

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
        (повторный матчинг, ручные решения Review). Отвечает по справочнику
        единиц, а не по алиасам: у единицы может не быть ни одного алиаса, и
        `''` для неё означал бы «единицы нет» — то есть чужую идентичность.
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
