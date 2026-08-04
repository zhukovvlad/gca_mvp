"""Аналитика фазы 6: паспорт объекта и сквозная матрица (AGENTS.md §6, §7.4, §7.5).

Модуль опирается на **готовый** VIEW `v_position_deviations` (фаза 2) и ничего из
его логики не повторяет. Это прямое требование §5 брифинга фазы 6: семантика §4
(дата сравнения и её фолбэк, выбор норматива по классу ДОГОВОРА, «нет норматива» =
`NULL`, исключение разделов, строк без цены и не-`POSITION`) уже реализована в SQL
и покрыта `test_deviations_view.py`. Второе её представление в Python неизбежно
разъехалось бы с первым.

Что фаза 6 добавляет **поверх** VIEW — ровно две вещи, обе из §6:

* фильтр «только последняя смета договора» (`latest_estimates`) — в VIEW он не
  входит намеренно, потому что VIEW нужен и для истории;
* средневзвешенную ставку `SUM(unit_cost_total * w) / SUM(w)`.

**Считает SQL, а не Python** — решение §6.3, принятое замером
(`docs/phase6-analytics.md` §1.3): на масштабе брифинга 75 мс против 114 мс, а на
четырёхкратном 240 против 476, причём вариант с Python перекачивает 140 896 строк,
чтобы отдать 1210 ячеек. Деление на `numeric` заодно даёт точный `Decimal` без float
(§3) — без ручной реализации того, что БД уже делает.
"""
from __future__ import annotations

import datetime as dt
import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from crud.settings import get_passport_top_n
from models import (
    CatalogKind,
    CatalogPosition,
    Contract,
    Contractor,
    Estimate,
    Lot,
    ObjectModel,
    PositionItem,
    Proposal,
    RateClass,
    UnitOfMeasure,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  VIEW отклонений как объект SQLAlchemy
# ---------------------------------------------------------------------------

#: `v_position_deviations` создаётся raw SQL в миграции 0002 и потому невидим для
#: `Base.metadata` — как и три индекса из `RAW_SQL_INDEXES` (`alembic/env.py`).
#: Здесь объявлено его отражение, чтобы запросы собирались `select()`-ом, а не
#: склеивались из строк: строковый SQL не проверяется ничем до попадания в БД.
#:
#: Цена отражения — оно может разъехаться с настоящим VIEW. Это ловит
#: `test_analytics_api.py::test_declared_view_columns_match_the_database`, сверяя
#: объявление с `information_schema`. Без такой сверки переименованная в миграции
#: колонка проявилась бы как ошибка выполнения на живом стенде.
DEVIATIONS = sa.table(
    "v_position_deviations",
    sa.column("position_item_id", sa.BigInteger),
    sa.column("proposal_id", sa.BigInteger),
    sa.column("lot_id", sa.BigInteger),
    sa.column("estimate_id", sa.BigInteger),
    sa.column("contract_id", sa.BigInteger),
    sa.column("object_id", sa.BigInteger),
    sa.column("rate_class_id", sa.BigInteger),
    sa.column("catalog_position_id", sa.BigInteger),
    sa.column("job_title_in_proposal", sa.Text),
    sa.column("unit_id", sa.Integer),
    sa.column("unit_cost_total", sa.Numeric),
    sa.column("weight", sa.Numeric),
    sa.column("comparison_date", sa.Date),
    sa.column("rate_standard_id", sa.BigInteger),
    sa.column("standard_unit_rate", sa.Numeric),
    sa.column("deviation_pct", sa.Numeric),
)

#: Объявленный порядок колонок — он же ожидаемый в БД (снят из `information_schema`
#: и зафиксирован §1 брифинга фазы 6).
DECLARED_VIEW_COLUMNS = tuple(c.name for c in DEVIATIONS.columns)


# ---------------------------------------------------------------------------
#  Последняя смета договора (§6)
# ---------------------------------------------------------------------------

def latest_estimates():
    """Подзапрос «последняя смета каждого договора» (§6).

    Правило §6 дословно: «участвует только последняя смета (максимальный
    `amendment_no`, NULL — минимальный)». Отсюда `DESC NULLS LAST`, а не просто
    `DESC`: в PostgreSQL при `DESC` по умолчанию `NULLS FIRST`, и исходная смета
    (`amendment_no IS NULL`) вытеснила бы все допсоглашения — то есть матрица
    показывала бы **первую** смету вместо последней. Ошибка была бы тихой: цифры
    на месте, просто не те.

    Одно определение на четыре потребителя (паспорт, матрица, обе выгрузки):
    правило «последняя смета» одно, и второй его копии в проекте быть не должно.
    """
    return (
        sa.select(
            Estimate.id.label("estimate_id"),
            Estimate.contract_id.label("contract_id"),
            Estimate.amendment_no.label("amendment_no"),
            Estimate.data_prepared_on_date.label("data_prepared_on_date"),
            Estimate.title.label("title"),
        )
        .distinct(Estimate.contract_id)
        .order_by(Estimate.contract_id, Estimate.amendment_no.desc().nulls_last())
        .subquery("latest_estimates")
    )


def get_latest_estimate(db: Session, contract_id: int) -> Estimate | None:
    """Последняя смета одного договора — то же правило, что в `latest_estimates`."""
    return db.execute(
        sa.select(Estimate)
        .where(Estimate.contract_id == contract_id)
        .order_by(Estimate.amendment_no.desc().nulls_last())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
#  Паспорт объекта (§7.4)
# ---------------------------------------------------------------------------

def _contract_requisites(db: Session, contract_id: int) -> dict:
    """Реквизиты договора для паспорта (§1 пункт 2: номер, подписант, дата, сумма, класс)."""
    row = db.execute(
        sa.select(Contract, ObjectModel.title, Contractor.title, RateClass.title)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
        .where(Contract.id == contract_id)
    ).first()
    if row is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    contract, object_title, contractor_title, rate_class_title = row
    return {
        "id": contract.id,
        "contract_number": contract.contract_number,
        "title": contract.title,
        "object_id": contract.object_id,
        "object_title": object_title,
        "contractor_id": contract.contractor_id,
        "contractor_title": contractor_title,
        "rate_class_id": contract.rate_class_id,
        "rate_class_title": rate_class_title,
        "signer": contract.signer,
        "signed_date": iso(contract.signed_date),
        "total_amount": contract.total_amount,
        "notes": contract.notes,
    }


def _priced_positions_select(estimate_id: int):
    """Расценённые работы сметы — ровно состав строк VIEW, плюс сумма и единица.

    `total_cost_total` и код единицы в VIEW не входят, поэтому доезжают join-ами;
    отклонения при этом остаются целиком за VIEW — здесь ни строчки его логики.
    """
    return (
        sa.select(
            DEVIATIONS.c.position_item_id,
            DEVIATIONS.c.catalog_position_id,
            DEVIATIONS.c.job_title_in_proposal,
            DEVIATIONS.c.unit_cost_total,
            DEVIATIONS.c.weight,
            DEVIATIONS.c.standard_unit_rate,
            DEVIATIONS.c.deviation_pct,
            PositionItem.total_cost_total,
            UnitOfMeasure.code.label("unit_code"),
            CatalogPosition.standard_job_title,
        )
        .join(PositionItem, PositionItem.id == DEVIATIONS.c.position_item_id)
        .join(CatalogPosition, CatalogPosition.id == DEVIATIONS.c.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == DEVIATIONS.c.unit_id)
        .where(DEVIATIONS.c.estimate_id == estimate_id)
    )


def get_passport(db: Session, contract_id: int) -> dict:
    """Данные паспорта объекта (§7.4): реквизиты, ключевые расценки, отклонения.

    **«Ключевые расценки» = топ-N позиций последней сметы по `total_cost_total`**
    (§7.4 дословно), где N — из БД (`app_settings.passport_top_n`, решение §6.2).

    Совокупность, из которой берётся топ, — **строки VIEW отклонений**, то есть
    расценённые работы сметы. Это не сужение ради удобства, а то же определение:
    VIEW исключает разделы, строки без цены и каталожные строки с `kind <>
    'POSITION'` (§4) — а у раздела и у мусорной строки нет ставки, значит нет и
    «расценки», которую паспорт называет ключевой.

    Название работы берётся **из сметы** (`job_title_in_proposal`), а не из
    каталога: паспорт — документ по конкретному договору, и в нём должна стоять
    формулировка этого договора. Каталожное название отдаётся рядом
    (`catalog_job_title`) — по нему видно, с какой работой позиция сопоставлена, и
    по нему же считается норматив.

    Итоги считаются по **всей** совокупности, не по показанному топу: иначе
    «показаны 15 из 1100» соседствовало бы с суммой пятнадцати строк, и её легко
    было бы прочитать как сумму сметы.
    """
    top_n = get_passport_top_n(db)
    body: dict = {
        "contract": _contract_requisites(db, contract_id),
        "top_n": top_n,
        "generated_for": None,
    }

    estimate = get_latest_estimate(db, contract_id)
    if estimate is None:
        # Договор без сметы — не ошибка: карточка заведена, файл ещё не загружен.
        # Паспорт печатается и в этом виде (реквизиты уже есть), поэтому 404 здесь
        # был бы неверным ответом.
        body["estimate"] = None
        body["key_rates"] = []
        body["totals"] = _empty_totals()
        return body

    body["estimate"] = {
        "id": estimate.id,
        "amendment_no": estimate.amendment_no,
        "title": estimate.title,
        "data_prepared_on_date": iso(estimate.data_prepared_on_date),
    }

    rows = db.execute(
        _priced_positions_select(estimate.id)
        # Сумма по позиции — критерий топа (§7.4). id — тай-брейк: без него порядок
        # при равных суммах не определён, и топ «дрожал» бы между запросами.
        .order_by(
            PositionItem.total_cost_total.desc().nulls_last(),
            DEVIATIONS.c.position_item_id.asc(),
        )
        .limit(top_n)
    ).all()

    body["key_rates"] = [
        {
            "position_item_id": r.position_item_id,
            "catalog_position_id": r.catalog_position_id,
            "job_title": r.job_title_in_proposal,
            "catalog_job_title": r.standard_job_title,
            "unit_code": r.unit_code,
            "weight": r.weight,
            "unit_cost_total": r.unit_cost_total,
            "total_cost_total": r.total_cost_total,
            "standard_unit_rate": r.standard_unit_rate,
            # Точный Decimal; округление до 0.1 п.п. — только на слое
            # представления (§4). В JSON уедет строкой.
            "deviation_pct": r.deviation_pct,
        }
        for r in rows
    ]
    body["totals"] = _passport_totals(db, estimate.id)
    # Сколько строк реально показано — это длина топа, а не отдельный запрос:
    # топ мог оказаться короче N, если расценённых работ в смете меньше.
    body["totals"]["positions_shown"] = len(body["key_rates"])
    return body


def _empty_totals() -> dict:
    return {
        "positions_priced": 0,
        "positions_shown": 0,
        "priced_amount": None,
        "with_standard": 0,
        "without_standard": 0,
        "over_standard": 0,
        "positions_pending_review": 0,
        "positions_non_work": 0,
    }


def _pending_review_condition():
    """Позиция расценена, но её работа ещё не утверждена в каталоге.

    Такая позиция **не попадает** в VIEW отклонений: он берёт только каталожные
    строки `kind='POSITION'` (§4), а свежая загрузка кладёт незнакомые работы в
    `TO_REVIEW`. Значит паспорт и матрица по свежезагруженной смете пусты — и это
    правильно, но объяснить это обязан экран.

    **Найдено прогоном стенда, а не тестом.** На живой базе все 1830 позиций
    реальной сметы имели цену, но каталог целиком состоял из `TO_REVIEW`, и паспорт
    сообщал «у позиций не заполнена цена за единицу» — то есть называл неверную
    причину. Ни один тест этого не поймал: в фикстурах каталожные строки создаются
    сразу `POSITION`. Тот же класс, что находка §2.2 брифинга — путь от пустой базы.

    Считаются ровно два состояния, и оба означают «человеку есть что разобрать»:

    * `kind = 'TO_REVIEW'` — строка в очереди ручного матчинга (§5);
    * `cp.id IS NULL` — позиция вовсе не сматчена (импорт прерван между импортом и
      матчингом, §5); для человека это тот же случай.

    **`HEADER`, `TRASH` и `LOT_HEADER` сюда НЕ входят** — замечание внешнего ревью,
    подтверждённое тестом до правки. Первая редакция брала всё, что `kind !=
    'POSITION'`, но это неверно: §5.4.3 описывает `HEADER`/`TRASH` как строку, которая
    «уже вручную размечена как не-работа», и в очередь Review она не попадает. То есть
    экран советовал бы «разобрать очередь», в которой этих строк нет, — человек открыл
    бы Review и не нашёл там ничего. Ошибка тем и опасна, что подсказка выглядит
    осмысленной.
    """
    return sa.and_(
        PositionItem.is_chapter.is_(False),
        PositionItem.unit_cost_total.isnot(None),
        sa.or_(
            CatalogPosition.id.is_(None),
            CatalogPosition.kind == CatalogKind.TO_REVIEW.value,
        ),
    )


def _non_work_condition():
    """Позиция расценена, но её каталожная строка помечена как НЕ-работа.

    `HEADER`, `TRASH`, `LOT_HEADER` — уже разобранные строки (§5.4.3), и разбирать в
    них нечего. Но в VIEW отклонений они тоже не попадают (§4), то есть остаются
    третьей причиной пустого паспорта — и она не равна ни «ждут матчинга», ни «нет
    цены».

    Счётчик появился как следствие правки по замечанию ревью: как только `HEADER`
    перестал считаться «ожидающим матчинга», паспорт для сметы из одних таких строк
    начал утверждать «у позиций не заполнена цена за единицу» — неправду, потому что
    цена как раз заполнена. Экран не должен называть причину, которой не знает.
    """
    return sa.and_(
        PositionItem.is_chapter.is_(False),
        PositionItem.unit_cost_total.isnot(None),
        CatalogPosition.kind.in_(
            [
                CatalogKind.HEADER.value,
                CatalogKind.TRASH.value,
                CatalogKind.LOT_HEADER.value,
            ]
        ),
    )


def _unmatched_counts_select():
    """Оба счётчика непопадания в VIEW — ОДНИМ запросом.

    `FILTER` вместо двух запросов: обе выборки идут по одной и той же цепочке
    позиция → предложение → лот → каталожная строка, и второй проход был бы платой
    только за форму кода.
    """
    return (
        sa.select(
            sa.func.count().filter(_pending_review_condition()).label("pending_review"),
            sa.func.count().filter(_non_work_condition()).label("non_work"),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
    )


def _passport_totals(db: Session, estimate_id: int) -> dict:
    """Итоги по ВСЕЙ совокупности расценённых работ сметы.

    `over_standard` — сколько работ дороже норматива. Позиции без норматива в этот
    счётчик не входят и учтены отдельным (`without_standard`): §10 требует, чтобы
    «нет норматива» было отличимо от «0 %», а слив их в один счётчик стёр бы
    разницу ровно там, где она нужна.
    """
    row = db.execute(
        sa.select(
            sa.func.count().label("positions_priced"),
            sa.func.sum(PositionItem.total_cost_total).label("priced_amount"),
            sa.func.count(DEVIATIONS.c.rate_standard_id).label("with_standard"),
            sa.func.count(sa.case((DEVIATIONS.c.deviation_pct > 0, 1))).label("over_standard"),
        )
        .select_from(DEVIATIONS)
        .join(PositionItem, PositionItem.id == DEVIATIONS.c.position_item_id)
        .where(DEVIATIONS.c.estimate_id == estimate_id)
    ).one()
    counts = db.execute(
        _unmatched_counts_select().where(Lot.estimate_id == estimate_id)
    ).one()
    return {
        "positions_priced": row.positions_priced,
        # Перезаписывается вызывающим на длину топа (см. `get_passport`).
        "positions_shown": 0,
        "priced_amount": row.priced_amount,
        "with_standard": row.with_standard,
        "without_standard": row.positions_priced - row.with_standard,
        "over_standard": row.over_standard,
        # Две причины пустого топа при непустой смете, и они РАЗНЫЕ: первую человек
        # исправляет в очереди Review, вторую исправлять не нужно вовсе (§5.4.3).
        "positions_pending_review": counts.pending_review,
        "positions_non_work": counts.non_work,
    }


# ---------------------------------------------------------------------------
#  Сквозная матрица (§6, §7.5)
# ---------------------------------------------------------------------------

#: Размер страницы матрицы по умолчанию и потолок (решение §6.4: серверная
#: пагинация по строкам). Потолок отдельный от `common.MAX_PAGE_SIZE`, потому что
#: цена страницы здесь — не строки, а **ячейки**: строки × договоры выборки.
MATRIX_PAGE_SIZE_DEFAULT = 50
MATRIX_PAGE_SIZE_MAX = 200


def scope_filters(
    *,
    rate_class_id: int | None,
    date_from: dt.date | None,
    date_to: dt.date | None,
) -> list:
    """Фильтры выборки матрицы: класс и период (§6).

    Период фильтруется по **дате сравнения** (`comparison_date` VIEW-а —
    `COALESCE(estimates.data_prepared_on_date, contracts.signed_date)`), а не по
    дате подписания договора: именно по этой дате подбирается норматив, и фильтр
    обязан отбирать то же, по чему считается показанное отклонение. Фильтруй мы по
    `signed_date`, смета с собственной датой подготовки попадала бы в период, к
    которому её норматив не относится.
    """
    conditions = []
    if rate_class_id is not None:
        conditions.append(DEVIATIONS.c.rate_class_id == rate_class_id)
    if date_from is not None:
        conditions.append(DEVIATIONS.c.comparison_date >= date_from)
    if date_to is not None:
        conditions.append(DEVIATIONS.c.comparison_date <= date_to)
    return conditions


def _cells_cte(scope_filters: list):
    """Ячейки матрицы: средневзвешенная ставка по (работа × договор) (§6).

    Формула §6 дословно: ячейка = `SUM(unit_cost_total * w) / SUM(w)` только по
    строкам, где `unit_cost_total IS NOT NULL` и `w > 0`. Первое условие уже держит
    VIEW, второе — `weight > 0` здесь; `NULL > 0` даёт `NULL`, поэтому позиция без
    обоих количеств отсекается тем же условием, без отдельной проверки.

    `MIN(standard_unit_rate)` — не «какой-нибудь из нескольких», а единственный:
    внутри группы (работа, договор) последней сметы дата сравнения одна (смета одна)
    и класс один (договор один), а `EXCLUDE` на `rate_standards` не даёт двум
    нормативам действовать на одну дату для одной пары (§4). Значит у всех строк
    группы норматив один и тот же, и агрегат нужен лишь чтобы вынести его из
    `GROUP BY`. Закреплено тестом на работе, встречающейся в смете дважды.

    `amount` — вес строки в деньгах; по нему упорядочены строки матрицы (§6.4).
    Это не «min/max/разброс», исключённые §6: новых колонок не появляется.
    """
    latest = latest_estimates()
    weighted = sa.func.sum(DEVIATIONS.c.unit_cost_total * DEVIATIONS.c.weight)
    return (
        sa.select(
            DEVIATIONS.c.catalog_position_id.label("catalog_position_id"),
            DEVIATIONS.c.contract_id.label("contract_id"),
            (weighted / sa.func.sum(DEVIATIONS.c.weight)).label("rate"),
            weighted.label("amount"),
            sa.func.min(DEVIATIONS.c.standard_unit_rate).label("standard_unit_rate"),
        )
        .select_from(
            DEVIATIONS.join(latest, latest.c.estimate_id == DEVIATIONS.c.estimate_id)
        )
        .where(DEVIATIONS.c.weight > 0, *scope_filters)
        .group_by(DEVIATIONS.c.catalog_position_id, DEVIATIONS.c.contract_id)
        .cte("cells")
    )


def matrix_columns(
    db: Session,
    *,
    rate_class_id: int | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
) -> list[dict]:
    """Колонки матрицы — договоры выборки, с группировкой по объекту (§6).

    Считаются **независимо от страницы**: набор колонок обязан быть одинаковым на
    всех страницах, иначе таблица «прыгала» бы при листании и сравнение по строке
    ломалось бы. Поэтому здесь тот же фильтр выборки, но без ограничения по
    работам.
    """
    latest = latest_estimates()
    rows = db.execute(
        sa.select(
            Contract.id.label("contract_id"),
            Contract.contract_number,
            Contract.signed_date,
            ObjectModel.id.label("object_id"),
            ObjectModel.title.label("object_title"),
            Contractor.title.label("contractor_title"),
            RateClass.id.label("rate_class_id"),
            RateClass.title.label("rate_class_title"),
            latest.c.estimate_id,
            latest.c.amendment_no,
            sa.func.coalesce(latest.c.data_prepared_on_date, Contract.signed_date).label(
                "comparison_date"
            ),
        )
        .select_from(
            sa.join(latest, Contract, Contract.id == latest.c.contract_id)
            .join(ObjectModel, ObjectModel.id == Contract.object_id)
            .join(Contractor, Contractor.id == Contract.contractor_id)
            .join(RateClass, RateClass.id == Contract.rate_class_id)
        )
        .where(
            *column_scope_filters(
                rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
            )
        )
        # Группировка по объекту (§6): колонки одного объекта стоят рядом.
        .order_by(ObjectModel.title.asc(), Contract.signed_date.asc(), Contract.id.asc())
    ).all()
    return [
        {
            "contract_id": r.contract_id,
            "contract_number": r.contract_number,
            "object_id": r.object_id,
            "object_title": r.object_title,
            "contractor_title": r.contractor_title,
            "rate_class_id": r.rate_class_id,
            "rate_class_title": r.rate_class_title,
            "estimate_id": r.estimate_id,
            "amendment_no": r.amendment_no,
            "comparison_date": iso(r.comparison_date),
        }
        for r in rows
    ]


def column_scope_filters(*, rate_class_id, date_from, date_to, latest) -> list:
    """Тот же фильтр выборки, выраженный через `contracts`/`latest`, а не через VIEW.

    Отдельная функция, потому что колонки строятся **не** по VIEW: договор с
    заведённой сметой, у которой ещё нет ни одной расценённой работы, всё равно
    остаётся колонкой выборки — иначе он молча исчезал бы из матрицы, и человек не
    отличил бы «нет данных по этой работе» от «договора нет в выборке».

    Дата сравнения выражена тем же `COALESCE`, что в VIEW (§4), — второго правила
    даты в системе нет.
    """
    comparison_date = sa.func.coalesce(latest.c.data_prepared_on_date, Contract.signed_date)
    conditions = []
    if rate_class_id is not None:
        conditions.append(Contract.rate_class_id == rate_class_id)
    if date_from is not None:
        conditions.append(comparison_date >= date_from)
    if date_to is not None:
        conditions.append(comparison_date <= date_to)
    return conditions


def get_matrix(
    db: Session,
    *,
    rate_class_id: int | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = MATRIX_PAGE_SIZE_DEFAULT,
) -> dict:
    """Сквозная матрица (§6, §7.5): строки — работы каталога, колонки — договоры.

    Три запроса: колонки выборки, число строк, страница строк вместе с ячейками.
    Ячейки едут в том же запросе, что строки, — их немного (страница × договоры), а
    отдельный запрос потребовал бы повторить агрегацию.
    """
    page = max(1, page)
    page_size = max(1, min(MATRIX_PAGE_SIZE_MAX, page_size))

    columns = matrix_columns(
        db, rate_class_id=rate_class_id, date_from=date_from, date_to=date_to
    )

    filters = scope_filters(
        rate_class_id=rate_class_id, date_from=date_from, date_to=date_to
    )
    cells = _cells_cte(filters)

    row_totals = (
        sa.select(
            cells.c.catalog_position_id.label("catalog_position_id"),
            sa.func.sum(cells.c.amount).label("row_amount"),
        )
        .group_by(cells.c.catalog_position_id)
        .cte("row_totals")
    )

    # Строки матрицы — каталожные POSITION (§6). Отдельного условия по `kind` здесь
    # НЕТ, и это проверено: совокупность задана VIEW-ом (`cp.kind = 'POSITION'` в его
    # определении), поэтому такое условие было бы мёртвым — снятие его не валит ни
    # одного теста. Мёртвая проверка хуже отсутствующей: она читается как защита.
    # Сохранность самого условия VIEW-а держит `test_deviations_view.py`, а
    # соответствие объявления VIEW действительности — сверка с `information_schema`.
    # Join к каталогу нужен за названием и единицей, а не за фильтром.
    row_base = (
        sa.select(
            row_totals.c.catalog_position_id,
            row_totals.c.row_amount,
            CatalogPosition.standard_job_title,
            UnitOfMeasure.code.label("unit_code"),
        )
        .select_from(
            row_totals.join(
                CatalogPosition, CatalogPosition.id == row_totals.c.catalog_position_id
            ).outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        )
    )
    if q and q.strip():
        row_base = row_base.where(CatalogPosition.standard_job_title.ilike(f"%{q.strip()}%"))

    total = db.execute(
        sa.select(sa.func.count()).select_from(row_base.subquery())
    ).scalar_one()

    page_rows = (
        row_base
        # Значимые работы сверху (§6.4); catalog_position_id — тай-брейк, без него
        # при равных суммах строка могла бы попасть на две страницы сразу.
        .order_by(row_totals.c.row_amount.desc(), row_totals.c.catalog_position_id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .subquery("page_rows")
    )

    rows = db.execute(
        sa.select(
            page_rows.c.catalog_position_id,
            page_rows.c.standard_job_title,
            page_rows.c.unit_code,
            page_rows.c.row_amount,
            cells.c.contract_id,
            cells.c.rate,
            cells.c.standard_unit_rate,
            sa.case(
                (cells.c.standard_unit_rate.is_(None), sa.null()),
                else_=(cells.c.rate / cells.c.standard_unit_rate - 1) * 100,
            ).label("deviation_pct"),
        )
        .select_from(
            page_rows.join(
                cells, cells.c.catalog_position_id == page_rows.c.catalog_position_id
            )
        )
        .order_by(page_rows.c.row_amount.desc(), page_rows.c.catalog_position_id.asc())
    ).all()

    return {
        "columns": columns,
        "rows": _shape_matrix_rows(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        # Почему матрица может быть пуста при непустых сметах. Считается по договорам
        # выборки, а не по всей базе: иначе подсказка говорила бы о работах, которых
        # человек на этом экране всё равно не видит.
        **_unmatched_in_scope(
            db, rate_class_id=rate_class_id, date_from=date_from, date_to=date_to
        ),
    }


def _unmatched_in_scope(
    db: Session,
    *,
    rate_class_id: int | None,
    date_from: dt.date | None,
    date_to: dt.date | None,
) -> dict:
    """Два счётчика непопадания в матрицу по договорам ВЫБОРКИ.

    Выборка та же, что у колонок (последние сметы договоров плюс фильтры класса и
    периода), поэтому счётчики отвечают именно про то, что человек смотрит. Считать по
    всей базе значило бы говорить о работах, которых на этом экране всё равно нет.

    Возвращает `dict` под распаковку в ответ: имена ключей — часть контракта API, и
    держать их в одном месте надёжнее, чем повторять на стороне вызова.
    """
    latest = latest_estimates()
    row = db.execute(
        _unmatched_counts_select()
        .join(latest, latest.c.estimate_id == Lot.estimate_id)
        .join(Contract, Contract.id == latest.c.contract_id)
        .where(
            *column_scope_filters(
                rate_class_id=rate_class_id,
                date_from=date_from,
                date_to=date_to,
                latest=latest,
            )
        )
    ).one()
    return {
        "positions_pending_review": row.pending_review,
        "positions_non_work": row.non_work,
    }


def _shape_matrix_rows(rows) -> list[dict]:
    """Плоский результат запроса → строки с ячейками. Только форма, без счёта.

    Ячейки отдаются **списком**, а не объектом с числовыми ключами: ключи JSON
    обязаны быть строками, и объект заставил бы фронтенд приводить `contract_id` к
    строке на каждом обращении — лишний повод для промаха по ключу.
    """
    shaped: dict[int, dict] = {}
    order: list[int] = []
    for r in rows:
        row = shaped.get(r.catalog_position_id)
        if row is None:
            row = {
                "catalog_position_id": r.catalog_position_id,
                "job_title": r.standard_job_title,
                "unit_code": r.unit_code,
                "row_amount": r.row_amount,
                "cells": [],
            }
            shaped[r.catalog_position_id] = row
            order.append(r.catalog_position_id)
        row["cells"].append(
            {
                "contract_id": r.contract_id,
                "rate": r.rate,
                "standard_unit_rate": r.standard_unit_rate,
                "deviation_pct": r.deviation_pct,
            }
        )
    return [shaped[cp] for cp in order]


def get_matrix_cell(db: Session, *, contract_id: int, catalog_position_id: int) -> dict:
    """Drill-down по ячейке (§6): позиции, из которых сложилась средневзвешенная ставка.

    Показывает именно те строки, которые участвовали в расчёте, — из последней сметы
    договора и с `weight > 0`. Иначе человек, проверяя цифру, складывал бы не то, что
    сложила система.
    """
    estimate = get_latest_estimate(db, contract_id)
    if estimate is None:
        raise DomainError(404, f"У договора {contract_id} нет ни одной сметы.")

    rows = db.execute(
        _priced_positions_select(estimate.id)
        .where(
            DEVIATIONS.c.catalog_position_id == catalog_position_id,
            DEVIATIONS.c.weight > 0,
        )
        .order_by(PositionItem.total_cost_total.desc().nulls_last(), DEVIATIONS.c.position_item_id)
    ).all()

    return {
        "contract_id": contract_id,
        "catalog_position_id": catalog_position_id,
        "estimate_id": estimate.id,
        "amendment_no": estimate.amendment_no,
        "items": [
            {
                "position_item_id": r.position_item_id,
                "job_title": r.job_title_in_proposal,
                "unit_code": r.unit_code,
                "weight": r.weight,
                "unit_cost_total": r.unit_cost_total,
                "total_cost_total": r.total_cost_total,
                "standard_unit_rate": r.standard_unit_rate,
                "deviation_pct": r.deviation_pct,
            }
            for r in rows
        ],
    }
