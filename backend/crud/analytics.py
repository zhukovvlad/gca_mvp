"""Аналитика фазы 6: паспорт объекта и сквозная матрица (AGENTS.md §6, §7.4, §7.5).

Модуль опирается на **готовый** VIEW `v_position_deviation_inputs` (фаза 2,
переименован и лишён `deviation_pct` миграцией 0012 — спека пересчёта НДС §2.1)
и ничего из его логики не повторяет. Это прямое требование §5 брифинга фазы 6:
семантика §4 (дата сравнения и её фолбэк, выбор норматива по классу ДОГОВОРА,
«нет норматива» = `NULL`, исключение разделов, строк без цены и не-`POSITION`)
уже реализована в SQL и покрыта `test_deviations_view.py`. Второе её представление
в Python неизбежно разъехалось бы с первым.

`deviation_pct` больше не входит в VIEW: норматив объявлен ценой БЕЗ НДС (спека
пересчёта §1), а VIEW отдавал бы отклонение на валовой цене — молча неверное.
Отклонение теперь считает Python, от нетто (`_deviation`), а VIEW отдаёт только
входные данные для этого расчёта — отсюда и новое имя.

Что фаза 6 добавляет **поверх** VIEW — ровно две вещи, обе из §6:

* фильтр «только последняя смета договора» (`latest_estimates`) — в VIEW он не
  входит намеренно, потому что VIEW нужен и для истории;
* средневзвешенную ставку `SUM(unit_cost_total * w) / SUM(w)`, теперь приведённую
  к нетто по базовой ставке НДС предложения (спека пересчёта §2.4, ветка
  тождества: `vat_rate_base = 0` не меняет величину).

**Считает SQL, а не Python** — решение §6.3, принятое замером
(`docs/phase6-analytics.md` §1.3): на масштабе брифинга 75 мс против 114 мс, а на
четырёхкратном 240 против 476, причём вариант с Python перекачивает 140 896 строк,
чтобы отдать 1210 ячеек. Деление на `numeric` заодно даёт точный `Decimal` без float
(§3) — без ручной реализации того, что БД уже делает. Группировка по базовой
ставке НДС (замер Б задачи 0 пересчёта) сохраняет это же свойство: делит на
`Decimal`, а не Python-цикл по строкам.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

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
from money.vat import (
    AmountStatus,
    effective_display_rate,
    gross_to_net,
    net_to_gross,
    quantize_money,
    restate_gross,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  VIEW входных данных отклонения как объект SQLAlchemy
# ---------------------------------------------------------------------------

#: `v_position_deviation_inputs` создаётся raw SQL в миграции 0012 (переименован
#: из `v_position_deviations` миграции 0002) и потому невидим для `Base.metadata`
#: — как и три индекса из `RAW_SQL_INDEXES` (`alembic/env.py`). Здесь объявлено
#: его отражение, чтобы запросы собирались `select()`-ом, а не склеивались из
#: строк: строковый SQL не проверяется ничем до попадания в БД.
#:
#: Цена отражения — оно может разъехаться с настоящим VIEW. Это ловит
#: `test_analytics_api.py::test_declared_view_columns_match_the_database`, сверяя
#: объявление с `information_schema` ПО ПОРЯДКУ КОЛОНОК. Без такой сверки
#: переименованная в миграции колонка проявилась бы как ошибка выполнения на
#: живом стенде.
DEVIATION_INPUTS = sa.table(
    "v_position_deviation_inputs",
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
    sa.column("vat_rate_base", sa.Numeric),
    sa.column("vat_rate_target", sa.Numeric),
)

#: Объявленный порядок колонок — он же ожидаемый в БД (снят из `information_schema`
#: и зафиксирован §1 брифинга фазы 6, обновлён задачей 3 пересчёта НДС).
DECLARED_VIEW_COLUMNS = tuple(c.name for c in DEVIATION_INPUTS.columns)


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

    `total_cost_total` и код единицы в VIEW не входят, поэтому доезжают join-ами.
    `deviation_pct` VIEW больше не несёт (миграция 0012): вместо него — база и
    целевая ставка НДС, из которых нетто и отклонение считает Python
    (`_net_deviation`), одинаково для паспорта и drill-down (оба потребителя
    этого select-а).
    """
    return (
        sa.select(
            DEVIATION_INPUTS.c.position_item_id,
            DEVIATION_INPUTS.c.catalog_position_id,
            DEVIATION_INPUTS.c.job_title_in_proposal,
            DEVIATION_INPUTS.c.unit_cost_total,
            DEVIATION_INPUTS.c.weight,
            DEVIATION_INPUTS.c.standard_unit_rate,
            DEVIATION_INPUTS.c.vat_rate_base,
            DEVIATION_INPUTS.c.vat_rate_target,
            PositionItem.total_cost_total,
            UnitOfMeasure.code.label("unit_code"),
            CatalogPosition.standard_job_title,
        )
        .join(PositionItem, PositionItem.id == DEVIATION_INPUTS.c.position_item_id)
        .join(CatalogPosition, CatalogPosition.id == DEVIATION_INPUTS.c.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == DEVIATION_INPUTS.c.unit_id)
        .where(DEVIATION_INPUTS.c.estimate_id == estimate_id)
    )


def _deviation(net_rate: Decimal | None, standard: Decimal | None) -> Decimal | None:
    """Отклонение фактической (уже нетто) ставки от норматива, в процентах.

    `None` при отсутствующей ставке или нормативе, и когда норматив равен нулю
    (деление на ноль здесь не ошибка ввода — просто «сравнить не с чем»).
    """
    if net_rate is None or standard is None or standard == 0:
        return None
    return (net_rate / standard - 1) * 100


def _net_deviation(
    unit_cost_total: Decimal | None,
    vat_rate_base: Decimal | None,
    standard_unit_rate: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Нетто, отклонение и причина его отсутствия — ОДНА формула на паспорт
    (`get_passport`) и drill-down (`get_matrix_cell`): оба читают одни и те же
    строки `_priced_positions_select`, и вторая независимая копия этого правила
    рано или поздно разошлась бы с первой (тот же довод, что у самого VIEW).

    Возвращает `(net, deviation_pct, deviation_reason)`. `deviation_reason`
    различает ДВЕ разные причины пустого отклонения (§10): база НДС неизвестна
    (нетто вообще не выведено) и норматива нет (нетто есть, сравнивать не с чем).
    """
    net = None if vat_rate_base is None else gross_to_net(unit_cost_total, vat_rate_base)
    reason = (
        "unknown_vat_base" if net is None
        else ("no_standard" if standard_unit_rate is None else None)
    )
    return net, _deviation(net, standard_unit_rate), reason


def _declared_rates(db: Session, estimate_id: int) -> list[Decimal | None]:
    """Заявленные ставки НДС предложений сметы — СЫРОЙ список для
    `effective_display_rate` (задача 8, ревью — Правка 2, спека §5.1):
    функция сама сворачивает его в единогласную ставку или `None` при
    разногласии/незнании/отсутствии предложений.

    Тот же приём, что у `crud.reports._declared_rates`/`crud.project_
    passport._vat_rate` — сознательно НЕ переиспользован ни один из них (эти
    три модуля не тянут друг друга ни в одну сторону, см. их собственные
    докстроки о циклах импорта): три строки SQL дешевле дублировать, чем
    заводить межмодульный импорт приватного имени.
    """
    return list(
        db.execute(
            sa.select(Proposal.vat_rate)
            .select_from(Proposal)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimate_id)
        ).scalars().all()
    )


def _standard_in_display_rate(standard: Decimal | None, rate: Decimal | None) -> Decimal | None:
    """Норматив (нетто) в ставке показа (задача 8, ревью — Правка 2, спека
    §5.1) — парная граница с `crud.reports._standard_in_display_rate`: то же
    правило, не общий код (те же причины, что у `_declared_rates`).

    `rate is None` (разногласие заявленных ставок предложений) гасит
    норматив целиком: показать «в какой-то из» ставок нельзя (§5.1).
    """
    if standard is None or rate is None:
        return None
    return net_to_gross(standard, rate)


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

    **Однодоговорная поверхность в СТАВКЕ ПОКАЗА (задача 8, ревью — Правка 2,
    спека §5.1).** До этой правки `unit_cost_total`/`total_cost_total` были
    валовыми файловыми, а `standard_unit_rate` — сырым нетто: факт и
    норматив стояли в РАЗНЫХ единицах, ровно тот дефект, ради которого
    заведена `effective_display_rate`. Теперь обе величины приводятся к ОДНОЙ
    ставке показа — факт через `restate_gross` по СВОЕЙ базе строки,
    норматив через `_standard_in_display_rate`. **`deviation_pct` и
    `over_standard` НЕ ТРОНУТЫ** — они считаются от нетто (`_net_deviation`)
    и от ставки показа не зависят (спека §2.5, строка 253): норматив-нетто
    против факта-нетто — ось, независимая от того, в какой ставке экран
    ПОКАЗЫВАЕТ те же самые числа.
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

    effective_rate = effective_display_rate(
        estimate.vat_rate_target, estimate.vat_rate_base_override, _declared_rates(db, estimate.id)
    )

    body["estimate"] = {
        "id": estimate.id,
        "amendment_no": estimate.amendment_no,
        "title": estimate.title,
        "data_prepared_on_date": iso(estimate.data_prepared_on_date),
        # Ставка показа (задача 8, спека §5.1) — экран подписывает ключевые
        # расценки этой ставкой («суммы показаны с НДС N %» либо «без НДС»
        # при 0); `None` — при разногласии заявленных ставок предложений.
        "vat_display_rate": effective_rate,
    }

    rows = db.execute(
        _priced_positions_select(estimate.id)
        # Сумма по позиции — критерий топа (§7.4). id — тай-брейк: без него порядок
        # при равных суммах не определён, и топ «дрожал» бы между запросами.
        .order_by(
            PositionItem.total_cost_total.desc().nulls_last(),
            DEVIATION_INPUTS.c.position_item_id.asc(),
        )
        .limit(top_n)
    ).all()

    key_rates = []
    key_rates_restated_any = False
    for r in rows:
        # Отклонение — от НЕТТО, по СЫРЫМ полям строки: ось, не зависящая от
        # ставки показа (см. докстроку функции, "не трогай deviation_pct").
        net, deviation_pct, reason = _net_deviation(
            r.unit_cost_total, r.vat_rate_base, r.standard_unit_rate
        )
        # Показ факта — в ставке показа (задача 8): ветка тождества
        # `restate_gross` сохраняет исходное значение посимвольно, когда
        # эффективная ставка совпадает с базой СТРОКИ (нет цели/перекрытой
        # базы, и предложение одно или все предложения сметы единогласны).
        display_unit_cost = restate_gross(r.unit_cost_total, r.vat_rate_base, effective_rate)
        display_total_cost = restate_gross(r.total_cost_total, r.vat_rate_base, effective_rate)
        if AmountStatus.RESTATED in (display_unit_cost.status, display_total_cost.status):
            key_rates_restated_any = True
        # Норматив (нетто) — в ту же ставку показа: у него нет ветки
        # тождества (это ВСЕГДА реальная конвертация нетто->гросс), поэтому
        # его появление само по себе включает квантование строки ниже.
        display_standard = _standard_in_display_rate(r.standard_unit_rate, effective_rate)
        if display_standard is not None:
            key_rates_restated_any = True
        key_rates.append(
            {
                "position_item_id": r.position_item_id,
                "catalog_position_id": r.catalog_position_id,
                "job_title": r.job_title_in_proposal,
                "catalog_job_title": r.standard_job_title,
                "unit_code": r.unit_code,
                "weight": r.weight,
                "unit_cost_total": display_unit_cost.amount,
                "unit_cost_net": quantize_money(net),
                "vat_rate_base": r.vat_rate_base,
                "total_cost_total": display_total_cost.amount,
                "standard_unit_rate": display_standard,
                # Точный Decimal; округление до 0.1 п.п. — только на слое
                # представления (§4). В JSON уедет строкой. Теперь считается от
                # НЕТТО (норматив — цена без НДС, спека пересчёта §1).
                "deviation_pct": deviation_pct,
                "deviation_reason": reason,
            }
        )
    if key_rates_restated_any:
        # Округление — ОДИН раз, на границе, и только если хоть что-то реально
        # пересчиталось (тот же принцип, что в паспорте проекта и в своде по
        # договору, ревью задачи 8): без цели/перекрытой базы/нормы поле
        # осталось бы посимвольно тем же, что и до этой правки.
        for item in key_rates:
            item["unit_cost_total"] = quantize_money(item["unit_cost_total"])
            item["total_cost_total"] = quantize_money(item["total_cost_total"])
            item["standard_unit_rate"] = quantize_money(item["standard_unit_rate"])
    body["key_rates"] = key_rates
    body["totals"] = _passport_totals(db, estimate.id, effective_rate)
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


def _passport_totals(db: Session, estimate_id: int, effective_rate: Decimal | None) -> dict:
    """Итоги по ВСЕЙ совокупности расценённых работ сметы.

    `over_standard` — сколько работ дороже норматива. Позиции без норматива в этот
    счётчик не входят и учтены отдельным (`without_standard`): §10 требует, чтобы
    «нет норматива» было отличимо от «0 %», а слив их в один счётчик стёр бы
    разницу ровно там, где она нужна. Он считается от НЕТТО и от ставки показа
    не зависит (см. докстроку `get_passport`, "не трогай").

    `priced_amount` — задача 8 (ревью, Правка 2, спека §5.1): раньше это была
    прямая `SUM(PositionItem.total_cost_total)` без всякого отношения к базе
    НДС; теперь каждая строка приводится к ставке показа СВОЕЙ базой (тот же
    приём, что у `crud.project_passport._direct_totals` и `crud.reports.
    _fold_summary_work`) ДО накопления — отсюда и Python-цикл вместо
    SQL-`SUM`: строки с разными базами внутри одной сметы нельзя просуммировать
    сырыми, а потом пересчитать одним вызовом.

    VIEW больше не несёт `deviation_pct` (миграция 0012), поэтому `over_standard`
    считается в Python, по нетто (спека пересчёта §1) — теми же строками, что и
    `priced_amount`, ОДНИМ узким запросом (только поля, нужные обоим расчётам),
    а не перекачкой всего `_priced_positions_select`.
    """
    counts_row = db.execute(
        sa.select(
            sa.func.count().label("positions_priced"),
            sa.func.count(DEVIATION_INPUTS.c.rate_standard_id).label("with_standard"),
        )
        .select_from(DEVIATION_INPUTS)
        .where(DEVIATION_INPUTS.c.estimate_id == estimate_id)
    ).one()

    deviation_rows = db.execute(
        sa.select(
            DEVIATION_INPUTS.c.unit_cost_total,
            DEVIATION_INPUTS.c.vat_rate_base,
            DEVIATION_INPUTS.c.standard_unit_rate,
            PositionItem.total_cost_total,
        )
        .select_from(DEVIATION_INPUTS)
        .join(PositionItem, PositionItem.id == DEVIATION_INPUTS.c.position_item_id)
        .where(DEVIATION_INPUTS.c.estimate_id == estimate_id)
    ).all()
    over_standard = 0
    priced_amount: Decimal | None = None
    priced_amount_restated_any = False
    for r in deviation_rows:
        _net, deviation_pct, _reason = _net_deviation(
            r.unit_cost_total, r.vat_rate_base, r.standard_unit_rate
        )
        if deviation_pct is not None and deviation_pct > 0:
            over_standard += 1

        restated_total = restate_gross(r.total_cost_total, r.vat_rate_base, effective_rate)
        if restated_total.status is AmountStatus.RESTATED:
            priced_amount_restated_any = True
        if restated_total.amount is not None:
            priced_amount = (
                restated_total.amount if priced_amount is None else priced_amount + restated_total.amount
            )
    # Округление — ОДИН раз, на границе, и только если хоть что-то реально
    # пересчиталось (тот же принцип, что в паспорте проекта и в своде по
    # договору): без цели/перекрытой базы поле остаётся посимвольно тем же,
    # что и до задачи 8.
    if priced_amount_restated_any:
        priced_amount = quantize_money(priced_amount)

    counts = db.execute(
        _unmatched_counts_select().where(Lot.estimate_id == estimate_id)
    ).one()
    return {
        "positions_priced": counts_row.positions_priced,
        # Перезаписывается вызывающим на длину топа (см. `get_passport`).
        "positions_shown": 0,
        "priced_amount": priced_amount,
        "with_standard": counts_row.with_standard,
        "without_standard": counts_row.positions_priced - counts_row.with_standard,
        "over_standard": over_standard,
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
        conditions.append(DEVIATION_INPUTS.c.rate_class_id == rate_class_id)
    if date_from is not None:
        conditions.append(DEVIATION_INPUTS.c.comparison_date >= date_from)
    if date_to is not None:
        conditions.append(DEVIATION_INPUTS.c.comparison_date <= date_to)
    return conditions


def _cell_groups_cte(scope_filters: list):
    """Слагаемые ячеек матрицы, в разрезе базы НДС (спека пересчёта §2.4, §6).

    Формула §6 дословно: слагаемые ячейки = `SUM(unit_cost_total * w)` и `SUM(w)`
    только по строкам, где `unit_cost_total IS NOT NULL` и `w > 0`. Первое условие
    уже держит VIEW, второе — `weight > 0` здесь; `NULL > 0` даёт `NULL`, поэтому
    позиция без обоих количеств отсекается тем же условием, без отдельной проверки.

    Группировка ДОПОЛНИТЕЛЬНО идёт по `vat_rate_base` — уровню, на котором
    множитель пересчёта в нетто постоянен (замер Б задачи 0 пересчёта: время ниже
    и обращений к буферам в 22 раза меньше варианта `VALUES`). Деление на нетто и
    свод групп в одну ячейку делает Python (`_fold_cell`): правило пересчёта
    обязано быть записано ОДИН раз, а не дважды — в SQL и в Python.

    `MIN(standard_unit_rate)` — не «какой-нибудь из нескольких», а единственный:
    внутри группы (работа, договор, база) последней сметы дата сравнения одна
    (смета одна) и класс один (договор один), а `EXCLUDE` на `rate_standards` не
    даёт двум нормативам действовать на одну дату для одной пары (§4). Значит у
    всех строк группы норматив один и тот же, и агрегат нужен лишь чтобы вынести
    его из `GROUP BY`. Закреплено тестом на работе, встречающейся в смете дважды.
    """
    latest = latest_estimates()
    weighted = sa.func.sum(DEVIATION_INPUTS.c.unit_cost_total * DEVIATION_INPUTS.c.weight)
    return (
        sa.select(
            DEVIATION_INPUTS.c.catalog_position_id.label("catalog_position_id"),
            DEVIATION_INPUTS.c.contract_id.label("contract_id"),
            DEVIATION_INPUTS.c.vat_rate_base.label("vat_rate_base"),
            weighted.label("weighted_cost"),
            sa.func.sum(DEVIATION_INPUTS.c.weight).label("weight_total"),
            sa.func.min(DEVIATION_INPUTS.c.standard_unit_rate).label("standard_unit_rate"),
        )
        .select_from(
            DEVIATION_INPUTS.join(latest, latest.c.estimate_id == DEVIATION_INPUTS.c.estimate_id)
        )
        .where(DEVIATION_INPUTS.c.weight > 0, *scope_filters)
        .group_by(
            DEVIATION_INPUTS.c.catalog_position_id,
            DEVIATION_INPUTS.c.contract_id,
            DEVIATION_INPUTS.c.vat_rate_base,
        )
        .cte("cell_groups")
    )


def _fold_cell(groups) -> dict:
    """Одна ячейка матрицы из групп по базе НДС (спека пересчёта §2.4, §6).

    Ровно одна ячейка на пару (работа, договор), даже если у нескольких её
    предложений — одна и та же база НДС (`test_matrix_yields_one_cell_per_
    position_and_contract`): группировка SQL их уже слила в одну строку.

    Хотя бы одна неизвестная база делает `rate`/`amount` пустыми ЦЕЛИКОМ: показать
    средневзвешенное по части строк значило бы выдать неполную величину за
    полную (та же логика, что у `row_amount_incomplete`, только на уровне ячейки).

    **`standard_unit_rate` при этом НЕ гаснет** — отступление от буквального
    текста плана в пользу спеки (задача 3 пересчёта НДС, найдено ревью): норматив
    от НДС не зависит и есть нетто по определению (спека §2.5, «норматив при
    неизвестной базе показывается как нетто; не вычисляется только отклонение»).
    Гасить его значило бы стирать разницу между «норматив есть, сравнить не с
    чем» (`unknown_vat_base`) и «норматива нет вовсе» (`no_standard`) — а ради
    этой самой разницы и заведена пара кодов причины. Норматив берётся с ТЕКУЩЕЙ
    группы, а не с накопленной по прошлым: в пределах одной пары (работа,
    договор) дата сравнения и класс договора одни на все группы независимо от
    базы (см. докстроку `_cell_groups_cte`), поэтому `standard_unit_rate` любой
    группы этой пары — то же самое значение, и брать его из группы, на которой
    сработал ранний выход, корректно независимо от порядка групп.
    """
    net_cost = Decimal(0)
    weight_total = Decimal(0)
    standard = None
    for group in groups:
        if group.vat_rate_base is None:
            return {
                "rate": None,
                "amount": None,
                "standard_unit_rate": group.standard_unit_rate,
                "deviation_pct": None,
                "deviation_reason": "unknown_vat_base",
            }
        if not group.weight_total:
            # Недостижимо сегодня: `_cell_groups_cte` фильтрует `weight > 0`, и
            # группа не может существовать без хотя бы одной такой строки —
            # SUM(weight) группы поэтому не бывает нулём/NULL. Оставлено защитой
            # на случай будущей правки фильтра, с ПРАВИЛЬНОЙ (не заимствованной у
            # соседней ветки) причиной — найдено ревью задачи 3.
            return {
                "rate": None,
                "amount": None,
                "standard_unit_rate": group.standard_unit_rate,
                "deviation_pct": None,
                "deviation_reason": "no_weight",
            }
        net_cost += gross_to_net(group.weighted_cost, group.vat_rate_base)
        weight_total += group.weight_total
        standard = group.standard_unit_rate if standard is None else standard

    rate = net_cost / weight_total if weight_total else None
    return {
        "rate": quantize_money(rate),
        "amount": quantize_money(net_cost),
        "standard_unit_rate": standard,
        "deviation_pct": _deviation(rate, standard),
        "deviation_reason": None if standard is not None else "no_standard",
    }


#: Единственное нетто-выражение в SQL (исключение §2.6 спеки, задокументированное
#: в `global-constraints.md`): `row_amount` служит ключом `ORDER BY` и пагинации и
#: одновременно показывается, поэтому его вес считает SQL, а не Python — в отличие
#: от самих ячеек (`_fold_cell`). Пришпилено к `money.vat.gross_to_net` тестом
#: (`test_sql_net_weight_agrees_with_python`) — две площадки одного правила
#: обязаны совпадать, и расхождение падает в CI, а не проявляется на стенде.
_NET_COST = (
    DEVIATION_INPUTS.c.unit_cost_total
    * DEVIATION_INPUTS.c.weight
    * 100
    / (100 + DEVIATION_INPUTS.c.vat_rate_base)
)


def _cell_weights_cte(scope_filters: list):
    """Нетто-вес КАЖДОЙ ячейки и признак её невычислимости (спека §2.6, исключение).

    Гранулярность — ячейка (работа × договор), та же, что у `_fold_cell`: вес
    строки обязан сходиться с суммой показанных ячеек, а показывается ячейка
    только целиком. Единица неполноты — ЯЧЕЙКА, а не строка VIEW: суммируй мы
    нетто по строкам VIEW напрямую, известная часть скрытой (частично неизвестной)
    ячейки всё равно попала бы в `row_amount`, и вес строки перестал бы сходиться
    с суммой показанных `cell.amount` (`test_row_amount_excludes_partially_
    unknown_cell`).
    """
    latest = latest_estimates()
    return (
        sa.select(
            DEVIATION_INPUTS.c.catalog_position_id.label("catalog_position_id"),
            DEVIATION_INPUTS.c.contract_id.label("contract_id"),
            sa.func.sum(_NET_COST).label("cell_net"),
            sa.func.bool_or(DEVIATION_INPUTS.c.vat_rate_base.is_(None)).label("cell_unknown"),
        )
        .select_from(
            DEVIATION_INPUTS.join(latest, latest.c.estimate_id == DEVIATION_INPUTS.c.estimate_id)
        )
        .where(DEVIATION_INPUTS.c.weight > 0, *scope_filters)
        .group_by(DEVIATION_INPUTS.c.catalog_position_id, DEVIATION_INPUTS.c.contract_id)
        .cte("cell_weights")
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
    cell_groups = _cell_groups_cte(filters)
    cell_weights = _cell_weights_cte(filters)

    row_totals = (
        sa.select(
            cell_weights.c.catalog_position_id.label("catalog_position_id"),
            # В вес входят ТОЛЬКО вычислимые ячейки — те, что видит аналитик
            # (спека §2.6, исключение: `SUM` игнорирует `NULL`, и без явного
            # `CASE` известная часть скрытой ячейки молча попала бы в вес).
            sa.func.sum(
                sa.case((cell_weights.c.cell_unknown.is_(False), cell_weights.c.cell_net))
            ).label("row_amount"),
            sa.func.bool_or(cell_weights.c.cell_unknown).label("row_amount_incomplete"),
        )
        .group_by(cell_weights.c.catalog_position_id)
        .cte("row_totals")
    )

    # Строки матрицы — каталожные POSITION (§6). Отдельного условия по `kind` здесь
    # НЕТ, и это проверено: совокупность задана VIEW-ом (`cp.kind = 'POSITION'` в его
    # определении), поэтому такое условие было бы мёртвым — снятие его не валит ни
    # одного теста. Мёртвая проверка хуже отсутствующей: она читается как защита.
    # Сохранность самого условия VIEW-а держит `test_deviations_view.py`, а
    # соответствие объявления VIEW действительности — сверка с `information_schema`.
    # Join к каталогу нужен за названием и единицей, а не за фильтром.
    row_base = sa.select(
        row_totals.c.catalog_position_id,
        row_totals.c.row_amount,
        row_totals.c.row_amount_incomplete,
        CatalogPosition.standard_job_title,
        UnitOfMeasure.code.label("unit_code"),
    ).select_from(
        row_totals.join(
            CatalogPosition, CatalogPosition.id == row_totals.c.catalog_position_id
        ).outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
    )
    if q and q.strip():
        row_base = row_base.where(CatalogPosition.standard_job_title.ilike(f"%{q.strip()}%"))

    total = db.execute(
        sa.select(sa.func.count()).select_from(row_base.subquery())
    ).scalar_one()

    page_rows = (
        row_base
        # Значимые работы сверху (§6.4), НЕИЗВЕСТНЫЕ — в конец (`COALESCE(…, 0)`
        # в вес не ставится: ноль увёл бы строку в середину сортировки и читался
        # бы как «работы на ноль рублей», а не «сумма неизвестна»).
        # catalog_position_id — тай-брейк, без него при равных суммах строка
        # могла бы попасть на две страницы сразу.
        .order_by(
            row_totals.c.row_amount.desc().nullslast(),
            row_totals.c.catalog_position_id.asc(),
        )
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
            page_rows.c.row_amount_incomplete,
            cell_groups.c.contract_id,
            cell_groups.c.vat_rate_base,
            cell_groups.c.weighted_cost,
            cell_groups.c.weight_total,
            cell_groups.c.standard_unit_rate,
        )
        .select_from(
            page_rows.join(
                cell_groups, cell_groups.c.catalog_position_id == page_rows.c.catalog_position_id
            )
        )
        .order_by(
            page_rows.c.row_amount.desc().nullslast(),
            page_rows.c.catalog_position_id.asc(),
        )
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
    """Плоский результат запроса → строки с ячейками. Только форма, почти без счёта.

    Ячейки отдаются **списком**, а не объектом с числовыми ключами: ключи JSON
    обязаны быть строками, и объект заставил бы фронтенд приводить `contract_id` к
    строке на каждом обращении — лишний повод для промаха по ключу.

    Группировка по базе НДС (`_cell_groups_cte`) отдаёт НЕСКОЛЬКО строк на одну
    ячейку матрицы (по одной на встретившуюся базу), поэтому здесь ДВА прохода:
    первый заводит скелет строки (по первой встреченной группе — job_title и
    unit_code от неё не зависят), второй собирает группы каждой ячейки и
    сворачивает их `_fold_cell`-ом. Одного прохода недостаточно: группы одной
    ячейки не обязаны идти в результате подряд.
    """
    shaped: dict[int, dict] = {}
    order: list[int] = []
    groups: dict[tuple[int, int], list] = {}
    group_order: list[tuple[int, int]] = []
    for r in rows:
        if r.catalog_position_id not in shaped:
            shaped[r.catalog_position_id] = {
                "catalog_position_id": r.catalog_position_id,
                "job_title": r.standard_job_title,
                "unit_code": r.unit_code,
                "row_amount": quantize_money(r.row_amount),
                "row_amount_incomplete": r.row_amount_incomplete,
                "cells": [],
            }
            order.append(r.catalog_position_id)
        key = (r.catalog_position_id, r.contract_id)
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(r)

    for catalog_position_id, contract_id in group_order:
        shaped[catalog_position_id]["cells"].append(
            {"contract_id": contract_id, **_fold_cell(groups[(catalog_position_id, contract_id)])}
        )
    return [shaped[cp] for cp in order]


def _cell_item(r) -> dict:
    """Строка drill-down: валовое из файла, нетто из ячейки и база между ними.

    `unit_cost_total` НЕ трогается — это исходные деньги файла, и спека пересчёта
    §2.4 обещает их посимвольное совпадение. `unit_cost_net` добавляется рядом:
    без него человек складывал бы валовые, а ячейка показывала бы нетто
    (`test_matrix_cell_drilldown_survives_the_migration`).
    """
    net, deviation_pct, reason = _net_deviation(
        r.unit_cost_total, r.vat_rate_base, r.standard_unit_rate
    )
    return {
        "position_item_id": r.position_item_id,
        "job_title": r.job_title_in_proposal,
        "unit_code": r.unit_code,
        "weight": r.weight,
        "unit_cost_total": r.unit_cost_total,
        "unit_cost_net": quantize_money(net),
        "vat_rate_base": r.vat_rate_base,
        "total_cost_total": r.total_cost_total,
        "standard_unit_rate": r.standard_unit_rate,
        "deviation_pct": deviation_pct,
        "deviation_reason": reason,
    }


def get_matrix_cell(db: Session, *, contract_id: int, catalog_position_id: int) -> dict:
    """Drill-down по ячейке (§6): позиции, из которых сложилась средневзвешенная ставка.

    Показывает именно те строки, которые участвовали в расчёте, — из последней сметы
    договора и с `weight > 0`. Иначе человек, проверяя цифру, складывал бы не то, что
    сложила система (ставка теперь нетто — спека пересчёта §2.4).
    """
    estimate = get_latest_estimate(db, contract_id)
    if estimate is None:
        raise DomainError(404, f"У договора {contract_id} нет ни одной сметы.")

    rows = db.execute(
        _priced_positions_select(estimate.id)
        .where(
            DEVIATION_INPUTS.c.catalog_position_id == catalog_position_id,
            DEVIATION_INPUTS.c.weight > 0,
        )
        .order_by(
            PositionItem.total_cost_total.desc().nulls_last(),
            DEVIATION_INPUTS.c.position_item_id,
        )
    ).all()

    return {
        "contract_id": contract_id,
        "catalog_position_id": catalog_position_id,
        "estimate_id": estimate.id,
        "amendment_no": estimate.amendment_no,
        "items": [_cell_item(r) for r in rows],
    }
