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
    RateStandard,
    UnitOfMeasure,
)
from money.price import is_price, is_weight
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
    """Расценённые работы сметы — ключевые ставки паспорта (`get_passport`).

    **Фильтрует ЦЕНУ САМА** (`_price_ok`, задача 3 плана правила цены), а не
    полагается на то, что негодную цену отсеял VIEW. **Миграция 0016 (задача 4)
    с тех пор сама сузила `v_position_deviation_inputs` тем же предикатом**, и
    сегодня это `WHERE` — конъюнкция предиката с самим собой: VIEW уже не
    отдаёт сюда ноль/отрицательное/`NaN`/`Infinity`, поэтому применение здесь
    ИНЕРТНО по отношению к текущему тексту VIEW (см. докстроку `_price_ok` —
    там названо, почему инертность не повод убирать условие и почему это не
    наблюдается тестом). До задачи 4 картина была другой: VIEW отсеивал только
    `unit_cost_total IS NOT NULL`, и без этого `where` ноль/отрицательное/`NaN`
    доезжали бы сюда как цена (спека §1.1/§1.2) — это и было независимым
    утверждением, доказанным тем, что предикат уже работал ДО сужения VIEW
    (`TestPassportKeyRatesPricePredicate`, `test_analytics_api.py`, зелёный и
    тогда, и сейчас).

    `total_cost_total` и код единицы в VIEW не входят, поэтому доезжают join-ами.
    `deviation_pct` VIEW больше не несёт (миграция 0012): вместо него — база и
    целевая ставка НДС, из которых нетто и отклонение считает Python
    (`_net_deviation`).

    Не потребитель drill-down (задача 3 развела их): drill-down обязан
    показать и НЕВОШЕДШИЕ строки, а этот select их не видит по построению —
    носитель drill-down теперь `_all_positions_select`.
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
        .where(
            DEVIATION_INPUTS.c.estimate_id == estimate_id,
            _price_ok(DEVIATION_INPUTS.c.unit_cost_total),
        )
    )


def _all_positions_select(estimate_id: int):
    """ВСЕ позиции работы сметы — носитель drill-down (`get_matrix_cell`,
    задача 3 плана правила цены, спека §2.8): в отличие от
    `_priced_positions_select`, без фильтра цены.

    Читает `PositionItem` СВОИМИ join-ами, а не VIEW (решение о носителе,
    отчёт задачи 2 плана правила цены): после задачи 4 невошедшие ценой строки
    в VIEW жить не будут вовсе, а drill-down обязан показать ИХ ТОЖЕ — с
    признаком невхождения и его причиной (спека §2.8: иначе ячейка `no_price`
    открывалась бы пустым списком, и утверждение экрана «работа есть, цены
    нет» нечем было бы проверить).

    Норматив и база НДС не выводятся заново второй копией правила выбора —
    они подтягиваются LEFT JOIN-ом к тому же `DEVIATION_INPUTS`, чьё правило
    здесь ЕДИНСТВЕННОЕ. **ON этого JOIN-а несёт `_price_ok` ЯВНО** (правка
    ревью, круг 1, блокер): без него норматив/база доезжали бы и до
    НЕВОШЕДШЕЙ строки, пока сегодняшний VIEW (миграция 0012) их ещё не
    исключил, — строка называла бы основание сравнения, которого не
    производила (`standard_unit_rate`/`vat_rate_base` непустые при
    `included=false`), и обещание задачи 4 «ответы drill-down не меняются
    миграцией» было бы ложно уже сегодня и ничем не застраховано: сузив VIEW
    вручную (добавив `_price_ok` в WHERE самого VIEW, а не в ON), можно было
    бы обнулить оба поля молча, и набор остался бы зелёным. С `_price_ok`
    прямо в ON поведение уже СЕЙЧАС равно тому, каким станет после задачи 4
    (`test_matrix_cell_drilldown_excluded_row_has_no_standard_rate`).

    Вес — `_PRESENCE_WEIGHT`, та же именованная копия правила `COALESCE(
    suggested_quantity, quantity)`, что уже несёт сторона присутствия: у
    `PositionItem` своей колонки «вес» нет, а копия правила ОДНА на все
    площадки вне VIEW (докстрока `_PRESENCE_WEIGHT` ниже).

    **Вторая копия условий VIEW.** `PositionItem.is_chapter.is_(False)` и
    `CatalogPosition.kind == POSITION` дословно повторяют часть `WHERE`
    текста миграции 0012 (`v_position_deviation_inputs`) — копия неизбежна
    (этот select читает `PositionItem` напрямую, а не VIEW), но она НАЗВАНА
    здесь этим абзацем и застрахована тестом ПОВЕДЕНИЯ, а не сверкой текста:
    строка-раздел (`is_chapter=true`) и позиция каталожной строки НЕ
    `POSITION` не обязаны появляться в drill-down
    (`test_matrix_cell_drilldown_excludes_chapter_rows`,
    `test_matrix_cell_drilldown_excludes_non_position_catalog_rows`).
    """
    return (
        sa.select(
            PositionItem.id.label("position_item_id"),
            PositionItem.catalog_position_id,
            PositionItem.job_title_in_proposal,
            PositionItem.unit_cost_total,
            _PRESENCE_WEIGHT.label("weight"),
            DEVIATION_INPUTS.c.standard_unit_rate,
            DEVIATION_INPUTS.c.vat_rate_base,
            DEVIATION_INPUTS.c.vat_rate_target,
            PositionItem.total_cost_total,
            UnitOfMeasure.code.label("unit_code"),
            CatalogPosition.standard_job_title,
        )
        .select_from(
            sa.join(
                PositionItem, CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id
            )
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
            .outerjoin(
                DEVIATION_INPUTS,
                sa.and_(
                    DEVIATION_INPUTS.c.position_item_id == PositionItem.id,
                    _price_ok(DEVIATION_INPUTS.c.unit_cost_total),
                ),
            )
        )
        .where(
            Lot.estimate_id == estimate_id,
            PositionItem.is_chapter.is_(False),
            CatalogPosition.kind == CatalogKind.POSITION.value,
        )
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
    различает ТРИ разные причины пустого отклонения (§10): база НДС неизвестна
    (нетто вообще не выведено), норматива нет (нетто есть, сравнивать не с
    чем) и — ре-ревью Codex, PR #21, дефект 1 — сама величина не число.

    **`NaN`/`Infinity` в `unit_cost_total`** доезжают сюда открытым хвостом Ф4
    (§5.6: импорт не проверяет годность цены). Арифметика (`gross_to_net`,
    `_deviation`) тихо распространяет нефинитное значение дальше — так и
    задумано для сумм (см. `quantize_money`), — но ОТКЛОНЕНИЕ затем
    СРАВНИВАЕТСЯ с нулём (`_passport_totals`, `deviation_pct > 0`), а сравнение
    нефинитного `Decimal` бросает `InvalidOperation` (тот же класс, что уже
    чинили в `crud.reports._amount_sort_key` и `services.excel.deviation_font`
    — здесь третья поверхность, найдена внешним ревью). Возвращать нефинитное
    `deviation_pct` из этой функции поэтому нельзя вовсе — не только ради
    вызывающего кода `_passport_totals`, а как собственный инвариант функции.

    Причина в этом случае — ЧЕТВЁРТЫЙ код, `not_finite`, и он не может
    подменяться существующими: норматив у строки может БЫТЬ (`no_standard`
    было бы ложью), а база НДС может быть ИЗВЕСТНА (`unknown_vat_base` было
    бы ложью тоже) — сама величина просто не число, и это другой факт (§2.5:
    две причины пустоты нельзя сводить к одной, здесь их уже три, и сведение
    к любой из существующих настолько же неверно, как исходное `None`).
    """
    net = None if vat_rate_base is None else gross_to_net(unit_cost_total, vat_rate_base)
    if net is not None and not net.is_finite():
        return net, None, "not_finite"
    deviation_pct = _deviation(net, standard_unit_rate)
    if deviation_pct is not None and not deviation_pct.is_finite():
        # Пояс и подтяжки: сегодня недостижимо, если `net` уже проверен выше и
        # `standard_unit_rate` не NULL (миграция 0002 держит `standard_unit_rate
        # > 0`), но PostgreSQL считает `'NaN'::numeric > 0` ИСТИНОЙ (тот же
        # факт, что уже задокументирован в `services.additional_works` для
        # похожего CHECK) — то есть нефинитный норматив теоретически МОГ бы
        # пройти CHECK. Не полагаемся на это молча.
        return net, None, "not_finite"
    reason = (
        "unknown_vat_base" if net is None
        else ("no_standard" if standard_unit_rate is None else None)
    )
    return net, deviation_pct, reason


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
        #
        # Гейт квантования — ПО ПОЛЮ, не по строке (ре-ревью задачи 8, круг 3,
        # Правка 1: круг 2 поднимал ОДИН флаг на всю строку от одного лишь
        # ПОКАЗА норматива и квантовал ИМ ЖЕ факт, который никто не
        # пересчитывал — регресс тождества §2.4). Факт квантуется, ТОЛЬКО
        # если он сам получил статус `RESTATED`.
        display_unit_cost = restate_gross(r.unit_cost_total, r.vat_rate_base, effective_rate)
        unit_cost_value = display_unit_cost.amount
        if display_unit_cost.status is AmountStatus.RESTATED:
            unit_cost_value = quantize_money(unit_cost_value)

        display_total_cost = restate_gross(r.total_cost_total, r.vat_rate_base, effective_rate)
        total_cost_value = display_total_cost.amount
        if display_total_cost.status is AmountStatus.RESTATED:
            total_cost_value = quantize_money(total_cost_value)

        # Норматив (ре-ревью, круг 3, Правка 2, спека §2.5 строка 293):
        # НЕИЗВЕСТНАЯ база ЭТОЙ строки (`r.vat_rate_base is None`) — норматив
        # показывается как НЕТТО, не гасится: конвертировать не во что, а
        # гасить его так же, как при разногласии, стёрло бы разницу между «не
        # с чем сравнить» (§10, `deviation_reason="unknown_vat_base"`, уже
        # различённой `_net_deviation` выше) и «нормы вовсе нет» — тот же
        # прецедент, что уже стерегёт матрица (`_fold_cell`,
        # `test_matrix_cell_keeps_standard_unit_rate_without_vat_base`).
        # РАЗНОГЛАСИЕ (база строки ИЗВЕСТНА, но эффективная ставка сметы —
        # `None`, потому что заявленные ставки ДРУГИХ предложений разошлись)
        # — другой случай: единой ставки показа нет вовсе, и здесь норматив
        # ГАСИТСЯ (§5.1, не переделывается).
        if r.vat_rate_base is None:
            standard_value = r.standard_unit_rate
        else:
            standard_value = _standard_in_display_rate(r.standard_unit_rate, effective_rate)
            if standard_value is not None:
                # Норматив не несёт ветки тождества (это ВСЕГДА реальная
                # конвертация нетто->гросс, даже когда факт остался
                # нетронутым), поэтому квантуется, когда вообще посчитан —
                # безусловно относительно факта, а не «заодно с ним».
                standard_value = quantize_money(standard_value)

        key_rates.append(
            {
                "position_item_id": r.position_item_id,
                "catalog_position_id": r.catalog_position_id,
                "job_title": r.job_title_in_proposal,
                "catalog_job_title": r.standard_job_title,
                "unit_code": r.unit_code,
                "weight": r.weight,
                "unit_cost_total": unit_cost_value,
                "unit_cost_net": quantize_money(net),
                "vat_rate_base": r.vat_rate_base,
                "total_cost_total": total_cost_value,
                "standard_unit_rate": standard_value,
                # Точный Decimal; округление до 0.1 п.п. — только на слое
                # представления (§4). В JSON уедет строкой. Теперь считается от
                # НЕТТО (норматив — цена без НДС, спека пересчёта §1).
                "deviation_pct": deviation_pct,
                "deviation_reason": reason,
            }
        )
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

    **Состав «расценённых» намеренно задан VIEW-ом, и это осознанное решение,
    а не пропуск (спека правила цены, задача 4 плана, ревью-находка и решение
    оркестратора).** `positions_priced`/`with_standard`/`without_standard`/
    `priced_amount` считаются прямым запросом к `DEVIATION_INPUTS` — тем, что
    вернул `v_position_deviation_inputs`, без второго, собственного предиката
    поверх него. Это отличает читателя от `_cell_groups_cte`/`_cell_weights_
    cte` и `_priced_positions_select`/`_all_positions_select` (задачи 2 и 3
    плана правила цены): тем читателям нужны либо строки, которых В VIEW НЕТ
    (drill-down показывает невошедшие позиции), либо независимость от текста
    VIEW на будущее (задача 4 не должна была менять их результат). Здесь ни
    того ни другого: «расценённые позиции сметы» и есть ровно содержимое VIEW,
    и второй фильтр поверх уже отфильтрованных строк был бы МЁРТВОЙ ЗАЩИТОЙ —
    тем самым, что ревью этой фичи ловило трижды на других площадках
    (`docs/insights/verifying-guards.md`).

    Отсюда прямое следствие миграции 0016: до неё VIEW отдавал позицию с
    нулевой, отрицательной или нефинитной ценой (условие было только
    `unit_cost_total IS NOT NULL`), и такая позиция СЧИТАЛАСЬ расценённой —
    тот же дефект счётчика, ради починки которого затеяна вся фича, только
    на этой площадке, а не в `key_rates`/матрице. После миграции 0016 «расценена»
    здесь означает «есть пригодная цена» (конечная и больше нуля), и это
    делает имя `positions_priced` правдой, а не обещанием. Замер на входе
    «одна позиция, цена NaN/Infinity/-Infinity, вес 1»: `positions_priced` и
    `with_standard` были 1, стали 0; `priced_amount` был нефинитной строкой
    (`'NaN'`/`'Infinity'`/`'-Infinity'`, просочившейся в JSON), стал `None`
    (позиций для накопления не осталось вовсе); `over_standard` не менялся —
    и до, и после равен 0 (`test_analytics_api.py::TestPassportSurvivesNon
    FiniteCost`).

    **Граница: «утечка нефинитного в `priced_amount` закрыта» верно ТОЛЬКО для
    этого измеренного входа, не вообще** (ревью задачи 4, Правка 8). `priced_
    amount` суммирует `PositionItem.total_cost_total` (через `restate_gross`,
    выше), а VIEW фильтрует `unit_cost_total` — это РАЗНЫЕ хранимые колонки, и
    предикат цены на вторую не распространяется. На измеренном входе они
    совпадали случайно: `total_cost_total = unit_cost_total × weight`, и
    нефинитная цена делала нефинитным ОБА поля сразу. Отдельно замерено:
    позиция с ПРИГОДНОЙ ценой (`unit_cost_total = 100`, проходит VIEW) и
    независимо испорченным `total_cost_total = NaN` (достижимо — импорт не
    валидирует построчную арифметику) — эндпоинт по-прежнему отдаёт
    `priced_amount = 'NaN'`. Это ДОФИЧЕВЫЙ, отдельный дефект (утечка
    нефинитного значения в JSON через `restate_gross`, у которой ветка
    `NOT_FINITE` возвращает `amount=gross`, а не `None`) — эта задача его не
    чинит, он идёт в реестр долга.

    Обещание Global Constraints фичи «счётчики полноты итогов паспорта не
    меняют смысл» на эту функцию НЕ распространяется: оно про другой набор —
    `positions_rows_priced`/`rows_priced`/`own_rows_priced`/`rows_with_amount`/
    `rate_coverage` из `crud/project_passport.py`, все про конечный `total_
    cost_total` и этой миграцией не тронуты. Эндпоинт, который использует
    `_passport_totals` (`GET /api/v1/analytics/passport/{contract_id}`), — уже
    убранный из навигации «паспорт фазы 6» (`docs/reference/screens.md`, п. про
    `passport_top_n`); смена смысла счётчика реального пользователя сегодня не
    касается.

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
        # Пояс и подтяжки (дефект 1, ре-ревью Codex, PR #21): `_net_deviation`
        # уже не имеет права вернуть нефинитное `deviation_pct` (см. её
        # докстроку), но сравнение здесь — то самое место, где нефинитный
        # `Decimal` бросал `InvalidOperation`, и оно обязано быть устойчивым
        # НЕЗАВИСИМО от гарантии вызываемой функции, а не полагаться на неё
        # одну — на случай, если инвариант выше когда-нибудь ослабят молча.
        if deviation_pct is not None and deviation_pct.is_finite() and deviation_pct > 0:
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
    """Слагаемые ячеек матрицы, в разрезе базы НДС (спека пересчёта §2.4, §6;
    правило цены — спека правила цены §2.2, §2.5).

    Строка **входит** в ячейку тогда и только тогда, когда её цена и вес оба
    пригодны — `_price_ok`/`_weight_ok` (`money.price.is_price`/`is_weight`,
    задача 1 плана правила цены), а не голое `weight > 0`, как было раньше.
    Разница не косметическая: старое условие пропускало нулевую, отрицательную
    и нефинитную цену в средневзвешенную ставку молча (спека §1.1 — 16,5 %
    позиций стенда без цены доезжали как «цена = 0»); теперь такая строка не
    входит в свёртку вовсе, и её судьбу (какую причину показать в пустой
    ячейке, поднимать ли признак неполноты) решает сторона присутствия
    (`_excluded_positions_cte`) — она же остаётся ЕДИНСТВЕННЫМ источником
    этого решения и после того, как миграция 0016 (задача 4) сузила саму VIEW
    тем же предикатом: такая строка сегодня не попадает сюда вообще (`WHERE`
    ниже дублирует уже применённый VIEW-ом фильтр — инертно, см. докстроку
    `_price_ok`), и если бы причина считалась здесь же, она пропадала бы
    молча.

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
        .where(
            _price_ok(DEVIATION_INPUTS.c.unit_cost_total),
            _weight_ok(DEVIATION_INPUTS.c.weight),
            *scope_filters,
        )
        .group_by(
            DEVIATION_INPUTS.c.catalog_position_id,
            DEVIATION_INPUTS.c.contract_id,
            DEVIATION_INPUTS.c.vat_rate_base,
        )
        .cte("cell_groups")
    )


def _fold_cell(groups) -> dict:
    """Одна ячейка матрицы из групп по базе НДС — ветка «есть хотя бы одна
    входящая позиция» (спека правила цены §2.5, Правило 1; спека пересчёта
    НДС §2.4, §6). Ветку «входящих нет» строит `_cell_without_ingesting` —
    у неё другой источник данных (сторона присутствия, не группы VIEW), и
    смешивать их в одну функцию значило бы протащить VIEW туда, где миграция
    0016 (задача 4) его уже не оставила.

    Ровно одна ячейка на пару (работа, договор), даже если у нескольких её
    предложений — одна и та же база НДС (`test_matrix_yields_one_cell_per_
    position_and_contract`): группировка SQL их уже слила в одну строку.

    **Две оси ответа (спека §2.5).** `rate_reason` — почему нет ставки, `None`
    означает ровно «ставка есть». `deviation_reason` — почему нет отклонения
    ЯЧЕЙКИ; когда `rate_reason` не пуст, `deviation_reason` — всегда `no_rate`
    (сравнивать нечего вообще, а не «сравнили и норматива не нашли» — это
    другой факт, `no_standard`, и он законен только при посчитанной ставке).

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
                "rate_reason": "unknown_vat_base",
                "deviation_pct": None,
                "deviation_reason": "no_rate",
            }
        if not group.weight_total:
            # Недостижимо сегодня: `_cell_groups_cte` фильтрует входящие
            # `_price_ok`/`_weight_ok`, и группа не может существовать без
            # хотя бы одной такой строки — SUM(weight) группы поэтому не
            # бывает нулём/NULL. Оставлено защитой на случай будущей правки
            # фильтра, с ПРАВИЛЬНОЙ (не заимствованной у соседней ветки)
            # причиной — найдено ревью задачи 3.
            return {
                "rate": None,
                "amount": None,
                "standard_unit_rate": group.standard_unit_rate,
                "rate_reason": "no_weight",
                "deviation_pct": None,
                "deviation_reason": "no_rate",
            }
        net_cost += gross_to_net(group.weighted_cost, group.vat_rate_base)
        weight_total += group.weight_total
        standard = group.standard_unit_rate if standard is None else standard

    rate = net_cost / weight_total if weight_total else None

    # Дефект 1, третий экземпляр (ре-ревью Codex, PR #21, круг 3): `NaN`/
    # `Infinity` в `unit_cost_total` доезжает сюда открытым хвостом Ф4 (§5.6) и
    # тихо распространяется через SQL `SUM` (`weighted_cost`) и через
    # `gross_to_net`/деление — `rate` (а с ним и `amount`, та же величина
    # `net_cost`) становится нефинитным БЕЗ исключения. В отличие от
    # `_net_deviation` (паспорт/drill-down), здесь под угрозой не только
    # `deviation_pct`, а ДВА денежных поля ответа: аналитик увидел бы в ячейке
    # матрицы буквальное `"NaN"`, подписанное `deviation_reason=None`/
    # `"no_standard"` — то есть «нет норматива» вместо честного «величина не
    # число». Матрица — поверхность, которую UI реально рисует (в отличие от
    # паспорта фазы 6), так что утечка была бы видна аналитику напрямую.
    #
    # Нефинитная `rate` прячет ОБА поля (`rate`/`amount`) целиком — та же
    # логика, что уже применена к `unknown_vat_base`/`no_weight` выше: частичная
    # величина хуже отсутствующей. `standard_unit_rate` не гасится (та же
    # причина, что в докстроке функции) — норматив от НДС не зависит и остаётся
    # нетто по определению независимо от годности факта.
    if rate is not None and not rate.is_finite():
        return {
            "rate": None,
            "amount": None,
            "standard_unit_rate": standard,
            "rate_reason": "not_finite",
            "deviation_pct": None,
            "deviation_reason": "no_rate",
        }

    deviation_pct = _deviation(rate, standard)
    if deviation_pct is not None and not deviation_pct.is_finite():
        # Пояс и подтяжки, симметрично `_net_deviation`: `rate` здесь уже
        # доказанно финитен (проверка выше), поэтому это может случиться,
        # только если сам норматив окажется нефинитным — гипотетически
        # возможно, т.к. PostgreSQL считает `'NaN'::numeric > 0` ИСТИНОЙ и
        # CHECK `standard_unit_rate > 0` NaN не отсекает. `rate`/`amount`
        # остаются видимыми (они настоящие числа, ставка ЕСТЬ — `rate_reason`
        # пуст), гасится только отклонение — тот же выбор, что у
        # `_net_deviation`. Здесь `not_finite` — это ось `deviation_reason`
        # («нефинитно отклонение»), а не ось `rate_reason` («нефинитна
        # ставка») — тот же токен встречается в обеих осях с разным смыслом
        # (спека §2.5).
        return {
            "rate": quantize_money(rate),
            "amount": quantize_money(net_cost),
            "standard_unit_rate": standard,
            "rate_reason": None,
            "deviation_pct": None,
            "deviation_reason": "not_finite",
        }

    return {
        "rate": quantize_money(rate),
        "amount": quantize_money(net_cost),
        "standard_unit_rate": standard,
        "rate_reason": None,
        "deviation_pct": deviation_pct,
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


def _not_finite(column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """«Значение колонки — NaN/Infinity», параметризовано КОЛОНКОЙ.

    Единственная реализация правила в модуле. `_price_ok`/`_weight_ok` ниже и
    `_presence_row_flags` (задача 2 плана правила цены) пользуются этой же
    функцией над ЛЮБОЙ числовой колонкой — сегодня это доказано на
    `DEVIATION_INPUTS.c.unit_cost_total`, `DEVIATION_INPUTS.c.weight`,
    `PositionItem.unit_cost_total` и `_PRESENCE_WEIGHT` (COALESCE-выражение
    веса на стороне присутствия). Второй копии условия не заводим: правило
    цены обязано жить в одном экземпляре (спека §2.2), и на каждой новой
    площадке добавляется применение функции, а не переписанное с нуля
    перечисление трёх нефинитных значений.

    В `crud.project_passport` есть `_finite_amount(column)` — та же проверка,
    уже параметризованная колонкой. Не переиспользована МЕЖДУ модулями по тем
    же причинам, что у `_declared_rates`/`_standard_in_display_rate` (модули
    сознательно не тянут друг у друга приватные имена) — и добавляются два
    своих довода: полярность обратная (`_finite_amount` истинна на ГОДНОМ
    значении, `_not_finite` — на НЕГОДНОМ), и NULL-поведение разное.
    `_finite_amount` намеренно не обрабатывает `NULL` отдельно (`NULL <>
    число` сам даёт `NULL`, что ведёт себя как ложь ТОЛЬКО внутри
    `WHERE`/`CASE` — и там, где она применяется, этого достаточно), а
    `_not_finite` — внутренний блок `_price_ok`/`_weight_ok`, чья гарантия
    явного `FALSE` на пустом входе держится на отдельном условии
    `column.is_not(None)` СНАРУЖИ; протаскивать это допущение через границу
    модуля было бы менее прозрачно, чем три строки сравнения.

    PostgreSQL сравнивает `numeric NaN` с самим собой как РАВНОЕ (в отличие от
    IEEE 754 `float`, где `nan == nan` ложно), поэтому `column ==
    Decimal("NaN")` здесь рабочая проверка, а не всегда ложная.
    """
    return sa.or_(
        column == Decimal("NaN"),
        column == Decimal("Infinity"),
        column == Decimal("-Infinity"),
    )


def _price_ok(column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """SQL-сторона `money.price.is_price`: конечное значение больше нуля.

    Функция ОТ КОЛОНКИ, а не готовое выражение над `DEVIATION_INPUTS`: тем же
    предикатом пользуются и над `PositionItem.unit_cost_total` — колонкой,
    куда VIEW не достаёт вовсе: `PositionItem` несёт пустую цену как `NULL`,
    а `v_position_deviation_inputs` такую строку просто не показывает (строка
    отсутствует, а не несёт `NULL`) — что при тексте 0012 (`IS NOT NULL`), что
    при тексте 0016 (предикат цены). Правило одно, площадок применения
    несколько — вместо второй копии условия на каждой.

    `column.is_not(None)` — не стилистика, а необходимое условие: SQL
    трёхзначен, и без него на пустом входе `NOT(_not_finite(NULL))` и
    `NULL > 0` дали бы не `FALSE`, а `NULL` — предикат перестал бы отличаться
    от «неизвестно» ровно там, где обязан читаться как «не цена». Присутствие
    этого условия в конъюнкции гарантирует итоговый `FALSE` независимо от
    прочих операндов: в трёхзначной логике `FALSE AND NULL = FALSE`. Закрыто
    тестом `test_price_predicate_sql.py::TestPriceOkOverPositionItem::
    test_null_price_gives_false_not_null`.

    Наивное «больше нуля» непригодно само по себе: `'NaN'::numeric > 0` и
    `'Infinity'::numeric > 0` в PostgreSQL дают `TRUE` (спека §1.2, предъявлено
    `test_price_predicate_sql.py::TestNaivePredicateIsNotEnough` на хранимой
    колонке) — нефинитное исключается явно, тем же приёмом, что и
    `_not_finite`.

    Задача 2 плана правила цены добавила второе применение — `_cell_groups_
    cte`/`_cell_weights_cte` фильтруют им `DEVIATION_INPUTS.c.unit_cost_total`.
    На тот момент (до миграции 0016) VIEW отсеивал только `IS NOT NULL`, и
    ноль/нефинитное доезжали как цена (спека §1.1/§1.2) — применение здесь
    было НЕОБХОДИМЫМ и наблюдалось тестами напрямую.

    **После миграции 0016 все ЧЕТЫРЕ продакшен-применения этой функции к
    колонке VIEW (`_priced_positions_select`, `_cell_groups_cte`, `_cell_
    weights_cte` и условие соединения в `_all_positions_select` — задача 3)
    стали ИНЕРТНЫ по отношению к VIEW, и это ЗНАНИЕ, а не недосмотр** (найдено
    ревью задачи 4). Сам VIEW теперь несёт тот же предикат в своём `WHERE`,
    и `_price_ok(DEVIATION_INPUTS.c.unit_cost_total)` здесь — конъюнкция
    условия с самим собой: снятие всех четырёх применений разом сегодня НЕ
    роняет НИ ОДНОГО теста (замерено собственным прогоном `just test-backend-
    parallel` с мутацией всех четырёх мест разом на `sa.true()`: 2646 passed,
    0 failed, 6 skipped — весь backend-suite, а не выборка), включая тот тест
    задачи 3, что предъявлял этот же предикат в условии соединения `_all_
    positions_select` красным ДО миграции 0016 — сегодня он этого больше не
    видит. Условия оставлены не ради наблюдаемой сейчас защиты, а по решению
    3 плана фичи: состав каждого из этих ЧЕТЫРЁХ читателей — СВОЙ, а не заданный
    VIEW-ом, и не должен зависеть от того, что именно исключает текст VIEW
    сегодня — тот может измениться будущей миграцией (сузиться иначе, временно
    расшириться, дать исключение) независимо от того, следят ли за этим
    читатели. Это ровно противоположность `_passport_totals`
    (`crud/analytics.py`, см. её докстроку): ТАМ второй фильтр был бы мёртвой
    защитой, потому что состав ТОГО читателя ЗАДАН VIEW-ом намеренно; ЗДЕСЬ
    состав читателей — их собственный факт, VIEW лишь однажды совпал с ним.
    """
    return sa.and_(column.is_not(None), sa.not_(_not_finite(column)), column > 0)


def _weight_ok(column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """SQL-сторона `money.price.is_weight`: конечное значение больше нуля.

    Формула совпадает с `_price_ok` — «конечно и больше нуля» правило заведено
    одно, — но факт другой: у веса свой носитель (объём/количество позиции,
    спека §2.1), и это отдельная функция, а не переиспользование `_price_ok`
    под другим именем. Если правила когда-нибудь разойдутся, менять придётся
    только одно место, не разбираясь, какой смысл где имелся в виду.

    Площадки применения: `DEVIATION_INPUTS.c.weight`
    (`test_price_predicate_sql.py::TestWeightOkOverDeviationInputsView`, и
    теперь также `_cell_groups_cte`/`_cell_weights_cte` — задача 2) и
    `_PRESENCE_WEIGHT` — именованная копия того же `COALESCE(suggested_
    quantity, quantity)`, что несёт миграция 0012 текстом VIEW, нужная стороне
    присутствия, у которой своей колонки «вес» нет вовсе (см. докстроку
    `_PRESENCE_WEIGHT` ниже).
    """
    return sa.and_(column.is_not(None), sa.not_(_not_finite(column)), column > 0)


def _cell_weights_cte(scope_filters: list):
    """Нетто-вес КАЖДОЙ ячейки и признак её невычислимости (спека §2.6, исключение).

    Гранулярность — ячейка (работа × договор), та же, что у `_fold_cell`: вес
    строки обязан сходиться с суммой показанных ячеек, а показывается ячейка
    только целиком. Единица неполноты — ЯЧЕЙКА, а не строка VIEW: суммируй мы
    нетто по строкам VIEW напрямую, известная часть скрытой (частично неизвестной)
    ячейки всё равно попала бы в `row_amount`, и вес строки перестал бы сходиться
    с суммой показанных `cell.amount` (`test_row_amount_excludes_partially_
    unknown_cell`).

    **Входящие строки — `_price_ok`/`_weight_ok`, не голое `weight > 0`**
    (задача 2 плана правила цены, та же правка, что у `_cell_groups_cte`):
    нулевая, отрицательная и нефинитная цена сюда больше не доезжают.

    **`cell_not_finite` (нефинитная цена среди входящих) отсюда УБРАН.** До
    этой правки VIEW пропускал сюда любую цену, включая нефинитную (фильтр был
    `weight > 0`, не `_price_ok`), и `bool_or` по стоимости ловил её здесь же.
    Теперь входящие строки по определению `_price_ok` — конечны, — и признак
    стал бы мёртвым (снятие фильтра его не воскрешает, воскрешает саму
    неисправность; см. `docs/insights/verifying-guards.md`, слой 7: мёртвая
    проверка хуже отсутствующей). Нефинитная позиция ЕСТЬ, но она больше не
    входящая — её видит сторона присутствия (`_excluded_positions_cte`,
    `any_not_finite`), и признак неполноты строки собирается ИЗ ДВУХ
    источников в `get_matrix`: `cell_unknown` отсюда и `incomplete` оттуда, а
    не из одного `bool_or` здесь. Это прямое требование решения о носителе
    (отчёт задачи 2, §1.5 спеки): миграция 0016 (задача 4) с тех пор убрала
    такую строку из VIEW насовсем, и признак не зависит от неё уже сегодня —
    он был построен так заранее, чтобы сужение VIEW его не задело.

    `cell_unknown` остаётся здесь: неизвестная база НДС у входящей строки —
    факт со стороны VIEW, который задача 4 не трогает (COALESCE базы не
    зависит от предиката цены), и вторую его копию заводить незачем.
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
        .where(
            _price_ok(DEVIATION_INPUTS.c.unit_cost_total),
            _weight_ok(DEVIATION_INPUTS.c.weight),
            *scope_filters,
        )
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


# ---------------------------------------------------------------------------
#  Сторона присутствия (спека правила цены §2.4, §2.5, §2.6; отчёт задачи 2
#  плана правила цены — "Решение о носителе"). Читает `PositionItem` напрямую,
#  а не VIEW отклонений: миграция 0016 (задача 4) с тех пор сузила VIEW тем же
#  предикатом цены, и совокупность работ, состав исключённых позиций и признак
#  неполноты не зависят от этого сужения — они были построены НЕ через VIEW
#  заранее, поэтому день, когда исключённые позиции пропали из VIEW, их не
#  затронул.
# ---------------------------------------------------------------------------

def _presence_positions(latest):
    """JOIN «позиция → её договор через последнюю смету» — общая основа обеих
    сторон присутствия (`_presence_cte`, `_excluded_positions_cte`): обе
    обязаны видеть один и тот же состав договоров, а не два похожих JOIN-а,
    которые могут незаметно разойтись.
    """
    return (
        sa.join(
            PositionItem, CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id
        )
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(latest, latest.c.estimate_id == Lot.estimate_id)
        .join(Contract, Contract.id == latest.c.contract_id)
    )


def _presence_conditions(*, rate_class_id, date_from, date_to, latest) -> list:
    """Условия присутствия: раздел не считается работой (§6), каталожная
    строка обязана быть `kind='POSITION'`, плюс фильтры выборки
    (`column_scope_filters` — то же правило даты, что у колонок матрицы).

    Фильтр по `kind` здесь ЯВНЫЙ, а не унаследованный от VIEW: сторона
    присутствия читает `PositionItem` напрямую, и без этого условия строка
    каталога `TO_REVIEW`/`HEADER`/`TRASH` стала бы строкой матрицы — ровно то,
    что сегодня исключает предложение `cp.kind = 'POSITION'` внутри
    определения VIEW (миграция 0012). Проверено существующим тестом на смеси
    кодов (`test_rows_are_catalog_positions_only`).
    """
    return [
        PositionItem.is_chapter.is_(False),
        CatalogPosition.kind == CatalogKind.POSITION.value,
        *column_scope_filters(
            rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
        ),
    ]


def _presence_cte(*, rate_class_id, date_from, date_to, latest):
    """Присутствие «работа × договор» (спека §2.4): пара существует, если в
    последней смете договора есть хотя бы одна позиция (`is_chapter=false`,
    каталожная строка `kind='POSITION'`) на эту работу — **независимо от
    цены**. Носитель — `PositionItem` (решение о носителе, см. докстроку
    модуля выше): работа, у которой во всей выборке нет ни одной годной цены,
    больше не исчезает из матрицы молча (спека §1.4), а становится строкой с
    пустой ставкой.

    Одним запросом задаёт и совокупность СТРОК матрицы (проекция на
    `catalog_position_id`), и совокупность СУЩЕСТВОВАНИЯ ЯЧЕЕК (пара с
    `contract_id`) — это одно правило присутствия, а не два похожих.
    """
    return (
        sa.select(
            PositionItem.catalog_position_id.label("catalog_position_id"),
            Contract.id.label("contract_id"),
        )
        .select_from(_presence_positions(latest))
        .where(
            *_presence_conditions(
                rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
            )
        )
        .group_by(PositionItem.catalog_position_id, Contract.id)
        .cte("presence")
    )


#: Вес на стороне присутствия («Решение о весе», отчёт задачи 2) —
#: ИМЕНОВАННАЯ копия правила, которое несёт миграция 0012 текстом VIEW
#: (`COALESCE(pi.suggested_quantity, pi.quantity) AS weight`). Текст миграции
#: заморожен (§9 AGENTS.md, downgrade обязан восстанавливать его дословно) —
#: вторая площадка этого правила неизбежна, и Global Constraints плана
#: требуют, чтобы она была НАЗВАНА (не инлайновым `COALESCE` посреди запроса,
#: а отдельным выражением с говорящим именем) и стереглась ТЕСТОМ ПОВЕДЕНИЯ,
#: а не сверкой текста: сверка текста зелена и когда оба текста ОДИНАКОВО
#: неверны. Стережёт `test_presence_weight_agrees_with_view_weight` —
#: сравнивает это выражение с `DEVIATION_INPUTS.c.weight` НА ОДНОЙ И ТОЙ ЖЕ
#: строке позиции, а не тексты двух формул.
_PRESENCE_WEIGHT = sa.func.coalesce(PositionItem.suggested_quantity, PositionItem.quantity)


def _presence_row_flags(
    price: sa.ColumnElement, weight: sa.ColumnElement
) -> dict[str, sa.ColumnElement]:
    """Классификация ОДНОЙ позиции на стороне присутствия — четыре именованных
    условия, из которых `get_matrix` складывает причину пустой ячейки (спека
    §2.5, Правило 2) и признак неполноты (спека §2.6). Считается по колонкам
    `PositionItem`, не VIEW — см. докстроку `_excluded_positions_cte`.

    Категории НЕ взаимоисключающие по построению (одна позиция может
    формально подходить сразу под несколько — например, нефинитная цена при
    отрицательном весе). Разрешение конкуренции — дело ВЫЗЫВАЮЩЕГО кода
    (приоритет §2.5: `not_finite` > `no_weight` > `negative_only` >
    `no_price`), не этой функции: она лишь называет факты, порядок им не
    приписывает.

    `flag` — признак неполноты ОДНОЙ позиции (спека §2.6, шесть строк
    таблицы): нефинитная цена или нефинитный вес делают матричный вклад
    невычислимым — флаг ДА безусловно, независимо от второй величины; иначе
    флаг ДА тогда и только тогда, когда произведение цены на вес НЕНУЛЕВОЕ —
    нулевая либо пустая цена и нулевой либо пустой вес дают нулевое
    произведение, и флаг они не поднимают. `COALESCE(..., 0)` здесь заменяет
    пустоту на ноль НАМЕРЕННО, в отличие от `_price_ok`/`_weight_ok`, где
    пустота обязана читаться как «непригодно»: у веса на входе в SUM своя
    роль (вклад), у веса на входе в предикат пригодности — своя, и это две
    разные задачи одной и той же колонки.

    `negative_only` НЕ проверяет конечность цены отдельным операндом (ре-ревью,
    правка H, мутант M8-эквивалент: `'-Infinity'::numeric < 0` истинно, и
    операнд конечности здесь был бы НЕНАБЛЮДАЕМ — приоритет §2.5 ставит
    `not_finite` ВЫШЕ `negative_only`, и `_cell_without_ingesting` проверяет
    `any_not_finite` первым; строка с `-Infinity` в цене уже поднимает
    `price_nonfinite` для СЕБЯ ЖЕ, а значит и общий `any_not_finite` группы —
    до `any_negative_only` в такой группе дело просто не доходит НИ ПРИ КАКОМ
    входе. Оставлять недоказуемый операнд значило бы держать защиту, которая
    читается как живая, но предъявить её отдельным входом нельзя — решение:
    убрать его, а не маскировать привычной формой «на всякий случай».
    """
    price_nonfinite = sa.and_(price.is_not(None), _not_finite(price))
    weight_nonfinite = sa.and_(weight.is_not(None), _not_finite(weight))
    contribution_nonzero = sa.and_(
        sa.not_(price_nonfinite),
        sa.not_(weight_nonfinite),
        sa.func.coalesce(price, 0) * sa.func.coalesce(weight, 0) != 0,
    )
    return {
        "not_finite": sa.or_(price_nonfinite, weight_nonfinite),
        "no_weight": sa.and_(_price_ok(price), sa.not_(_weight_ok(weight))),
        "negative_only": sa.and_(price.is_not(None), price < 0),
        "flag": sa.or_(price_nonfinite, weight_nonfinite, contribution_nonzero),
    }


def _excluded_positions_cte(*, rate_class_id, date_from, date_to, latest):
    """Позиции, ИСКЛЮЧЁННЫЕ из ставки ячейки (не входящие — `_price_ok` и
    `_weight_ok` вместе НЕ выполнены), сведённые в разрезе ячейки (спека
    §2.5, §2.6).

    Единственный источник причины пустой ставки и признака неполноты для
    невходящих позиций: решение о носителе требует читать их отсюда, а не из
    `bool_or` по строкам VIEW — `_cell_weights_cte` эти строки больше не
    видит (не проходят `_price_ok`/`_weight_ok`, см. её докстроку).

    Присутствие (`_presence_cte`) гарантирует, что у ЛЮБОЙ пары (работа,
    договор) есть хотя бы одна позиция `is_chapter=false`; если её нет среди
    входящих (`cell_groups` пуст для этой пары), она обязана найтись здесь —
    пустых с обеих сторон сразу не бывает. `get_matrix` полагается на это при
    выборе ветки фолда ячейки.
    """
    price = PositionItem.unit_cost_total
    weight = _PRESENCE_WEIGHT
    ingesting = sa.and_(_price_ok(price), _weight_ok(weight))
    flags = _presence_row_flags(price, weight)
    return (
        sa.select(
            PositionItem.catalog_position_id.label("catalog_position_id"),
            Contract.id.label("contract_id"),
            sa.func.bool_or(flags["not_finite"]).label("any_not_finite"),
            sa.func.bool_or(flags["no_weight"]).label("any_no_weight"),
            sa.func.bool_or(flags["negative_only"]).label("any_negative_only"),
            sa.func.bool_or(flags["flag"]).label("incomplete"),
        )
        .select_from(_presence_positions(latest))
        .where(
            sa.not_(ingesting),
            *_presence_conditions(
                rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
            ),
        )
        .group_by(PositionItem.catalog_position_id, Contract.id)
        .cte("excluded_positions")
    )


def _presence_standards_cte(*, rate_class_id, date_from, date_to, latest):
    """Норматив по паре (работа, договор), НЕЗАВИСИМО от цены и не через
    VIEW: `_fold_cell` берёт норматив из `cell_groups`, а группа существует
    только для входящих строк — ячейке без единой входящей взять норматив
    неоткуда, хотя норматив от цены не зависит и гаснуть не обязан (спека
    §2.5, §2.7 — тот же принцип, что уже держит `unknown_vat_base`/
    `no_weight` внутри `_fold_cell`). После задачи 4 VIEW сузится тем же
    предикатом цены, и для ячейки без входящих строк там норматива тоже не
    найти — эта площадка обязана не зависеть от VIEW уже сейчас.

    Условие диапазона дат — та же пара сравнений, что использует
    `crud.rate_standards._standards_select` (`valid_from <= дата` и
    `valid_to IS NULL OR valid_to > дата`), а не `daterange(...) @> ...`
    текстом VIEW: разное написание одного и того же полуоткрытого интервала,
    и площадка присутствия не обязана копировать SQL-синтаксис VIEW, только
    его смысл. `EXCLUDE` на `rate_standards` держит не больше одной строки на
    (работа, класс, дата) — `MIN` здесь способ вынести единственное значение
    из `GROUP BY`, а не агрегация по-настоящему (тот же приём, что у
    `_cell_groups_cte`).
    """
    comparison_date = sa.func.coalesce(latest.c.data_prepared_on_date, Contract.signed_date)
    return (
        sa.select(
            PositionItem.catalog_position_id.label("catalog_position_id"),
            Contract.id.label("contract_id"),
            sa.func.min(RateStandard.standard_unit_rate).label("standard_unit_rate"),
        )
        .select_from(
            _presence_positions(latest).outerjoin(
                RateStandard,
                sa.and_(
                    RateStandard.catalog_position_id == PositionItem.catalog_position_id,
                    RateStandard.rate_class_id == Contract.rate_class_id,
                    RateStandard.valid_from <= comparison_date,
                    sa.or_(
                        RateStandard.valid_to.is_(None),
                        RateStandard.valid_to > comparison_date,
                    ),
                ),
            )
        )
        .where(
            *_presence_conditions(
                rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
            )
        )
        .group_by(PositionItem.catalog_position_id, Contract.id)
        .cte("presence_standards")
    )


def _cell_without_ingesting(excluded_row, standard_unit_rate) -> dict:
    """Ячейка, у которой нет ни одной входящей позиции (спека §2.5, Правило 2).

    Приоритет причины СВЕРХУ ВНИЗ: `not_finite`, `no_weight`, `negative_only`,
    `no_price` — одно значение, а не массив (у ячейки одна подпись на
    экране), и порядок — правило спеки, а не случайность реализации.
    `no_price` — фолбэк на случай, когда ни одна из трёх более сильных
    категорий не сработала; присутствие гарантирует, что `excluded_row`
    непуст (хотя бы одна позиция в нём есть), поэтому фолбэк всегда означает
    «только нули и пустые», а не «данных нет вовсе».

    `standard_unit_rate` передаётся СНАРУЖИ (`_presence_standards_cte`), а не
    вычисляется здесь: норматив от цены не зависит и не гаснет вместе со
    ставкой (парный тест `TestMatrixCellFoldSurvivesNonFiniteCost`, тот же
    принцип, что у `unknown_vat_base`/`no_weight` внутри `_fold_cell`).
    """
    if excluded_row.any_not_finite:
        reason = "not_finite"
    elif excluded_row.any_no_weight:
        reason = "no_weight"
    elif excluded_row.any_negative_only:
        reason = "negative_only"
    else:
        reason = "no_price"
    return {
        "rate": None,
        "amount": None,
        "standard_unit_rate": standard_unit_rate,
        "rate_reason": reason,
        "deviation_pct": None,
        "deviation_reason": "no_rate",
    }


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

    Три запроса: колонки выборки, число строк, страница строк вместе с ячейками —
    как и до задачи 2 плана правила цены (§1.4 спеки, раздел «Замер плана» отчёта
    задачи: совокупность присутствия и причины исключённых позиций сложены В ТЕ ЖЕ
    два запроса — `row_base`/`total` и финальный `rows` — а не вынесены в третий и
    четвёртый; третий проход по данным (долг 13) этой задачей не заведён).

    **Совокупность строк — присутствие (`_presence_cte`), не VIEW отклонений**
    (спека §2.4, решение о носителе): работа, у которой во всей выборке нет ни
    одной годной цены, теперь становится строкой с пустой ставкой, а не исчезает
    молча. Ставка ячейки по-прежнему считается из VIEW (`_cell_groups_cte`) —
    правило неизменно, меняется лишь то, что происходит, когда входящих строк
    для ячейки НЕТ: причину и признак неполноты в этом случае называет
    `_excluded_positions_cte`, а не отсутствие ячейки.
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

    latest = latest_estimates()
    presence = _presence_cte(
        rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
    )
    excluded = _excluded_positions_cte(
        rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
    )
    presence_standards = _presence_standards_cte(
        rate_class_id=rate_class_id, date_from=date_from, date_to=date_to, latest=latest
    )

    row_totals = (
        sa.select(
            presence.c.catalog_position_id.label("catalog_position_id"),
            # В вес входят ТОЛЬКО вычислимые ячейки — те, что видит аналитик
            # (спека §2.6, исключение: `SUM` игнорирует `NULL`, и без явного
            # `CASE` известная часть скрытой ячейки молча попала бы в вес).
            # `COALESCE(cell_unknown, false)` учитывает и ячейки БЕЗ ИНГЕСТИРУЮЩЕЙ
            # строки вовсе (`cell_weights` для них пуст — `LEFT JOIN` даёт `NULL`):
            # такая ячейка не несёт суммы и не обязана нести признак «неизвестна
            # база», СУMM игнорирует её `NULL` сама.
            sa.func.sum(
                sa.case(
                    (
                        sa.func.coalesce(cell_weights.c.cell_unknown, False).is_(False),
                        cell_weights.c.cell_net,
                    )
                )
            ).label("row_amount"),
            # Признак неполноты строки — из ДВУХ источников (решение о носителе,
            # отчёт задачи 2): `cell_unknown` — неизвестная база НДС у входящей
            # строки (сторона VIEW, миграция 0016/задача 4 её не касается);
            # `excluded.incomplete` — исключённая позиция с ненулевым либо
            # невычислимым вкладом (сторона присутствия, спека §2.6) —
            # единственный источник и после того, как задача 4 убрала такие
            # строки из VIEW насовсем.
            sa.func.bool_or(
                sa.or_(
                    sa.func.coalesce(cell_weights.c.cell_unknown, False),
                    sa.func.coalesce(excluded.c.incomplete, False),
                )
            ).label("row_amount_incomplete"),
        )
        .select_from(
            presence.outerjoin(
                cell_weights,
                sa.and_(
                    cell_weights.c.catalog_position_id == presence.c.catalog_position_id,
                    cell_weights.c.contract_id == presence.c.contract_id,
                ),
            ).outerjoin(
                excluded,
                sa.and_(
                    excluded.c.catalog_position_id == presence.c.catalog_position_id,
                    excluded.c.contract_id == presence.c.contract_id,
                ),
            )
        )
        .group_by(presence.c.catalog_position_id)
        .cte("row_totals")
    )

    # Строки матрицы — присутствие (`_presence_cte`), которое само уже держит
    # условие `kind='POSITION'` (`_presence_conditions`) — второй его копии здесь
    # не заводим. Join к каталогу нужен за названием и единицей, а не за фильтром.
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
        # бы как «работы на ноль рублей», а не «сумма неизвестна»). Это же место
        # ставит в конец теперь и работу БЕЗ единой годной цены (`row_amount`
        # для неё тоже `NULL`) — сортировка не менялась НИ ОДНОЙ строкой (§2.4).
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

    # Ячейки страницы — присутствие (не только `cell_groups`): работа страницы ×
    # каждый договор, где она присутствует, даже без единой входящей позиции.
    cell_pairs = (
        sa.select(
            page_rows.c.catalog_position_id.label("catalog_position_id"),
            presence.c.contract_id.label("contract_id"),
        )
        .select_from(
            page_rows.join(
                presence, presence.c.catalog_position_id == page_rows.c.catalog_position_id
            )
        )
        .subquery("cell_pairs")
    )

    rows = db.execute(
        sa.select(
            page_rows.c.catalog_position_id,
            page_rows.c.standard_job_title,
            page_rows.c.unit_code,
            page_rows.c.row_amount,
            page_rows.c.row_amount_incomplete,
            cell_pairs.c.contract_id,
            cell_groups.c.vat_rate_base,
            cell_groups.c.weighted_cost,
            cell_groups.c.weight_total,
            cell_groups.c.standard_unit_rate,
            excluded.c.any_not_finite,
            excluded.c.any_no_weight,
            excluded.c.any_negative_only,
            presence_standards.c.standard_unit_rate.label("presence_standard_unit_rate"),
        )
        .select_from(
            page_rows.join(
                cell_pairs, cell_pairs.c.catalog_position_id == page_rows.c.catalog_position_id
            )
            # LEFT JOIN — входящих групп у ячейки может не быть вовсе (§2.5
            # Правило 2); `weighted_cost IS NULL` в результате отличает такую
            # ячейку от настоящей группы (`_shape_matrix_rows`).
            .outerjoin(
                cell_groups,
                sa.and_(
                    cell_groups.c.catalog_position_id == cell_pairs.c.catalog_position_id,
                    cell_groups.c.contract_id == cell_pairs.c.contract_id,
                ),
            )
            .outerjoin(
                excluded,
                sa.and_(
                    excluded.c.catalog_position_id == cell_pairs.c.catalog_position_id,
                    excluded.c.contract_id == cell_pairs.c.contract_id,
                ),
            )
            .outerjoin(
                presence_standards,
                sa.and_(
                    presence_standards.c.catalog_position_id == cell_pairs.c.catalog_position_id,
                    presence_standards.c.contract_id == cell_pairs.c.contract_id,
                ),
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
    сворачивает их. Одного прохода недостаточно: группы одной ячейки не обязаны
    идти в результате подряд.

    **Два пути свёртки ячейки** (спека §2.5): если среди строк ячейки есть хотя
    бы одна ИНГЕСТИРУЮЩАЯ (`_fold_cell`, различитель — `weighted_cost is not
    None`, LEFT JOIN с `cell_groups` иначе оставил бы её `NULL`), ставка
    считается по ним; если нет — причину называет `_cell_without_ingesting` по
    агрегатам присутствия (`excluded.any_*`), которые LEFT JOIN с `excluded`
    несёт на ТОЙ ЖЕ строке результата. Присутствие (`_presence_cte`)
    гарантирует, что для любой ячейки страницы сработает ровно одна из этих
    двух веток — пустых с обеих сторон не бывает.
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
        cell_rows = groups[(catalog_position_id, contract_id)]
        ingesting = [r for r in cell_rows if r.weighted_cost is not None]
        if ingesting:
            cell = _fold_cell(ingesting)
        else:
            cell = _cell_without_ingesting(cell_rows[0], cell_rows[0].presence_standard_unit_rate)
        shaped[catalog_position_id]["cells"].append({"contract_id": contract_id, **cell})
    return [shaped[cp] for cp in order]


def _row_exclusion_reason(price: Decimal | None, weight: Decimal | None) -> str:
    """Причина невхождения ОДНОЙ строки drill-down в ставку ячейки (задача 3
    плана правила цены, спека §2.8).

    **`not_finite` обязана проверяться ПЕРВОЙ — это реальный, наблюдаемый
    приоритет** (правка ревью, круг 1): без него цена `-Infinity` ушла бы в
    `negative` (`Decimal("-Infinity") < 0` не бросает и не различает
    бесконечность от обычного отрицательного числа), а отрицательная цена при
    НЕФИНИТНОМ весе ушла бы в `negative`, даже не заметив, что сам вклад
    невычислим (`test_matrix_cell_drilldown_excluded_reason_not_finite_wins_
    over_negative_price`, `..._with_nonfinite_weight`).

    **Порядок между `no_weight` и `negative` НИЖЕ — не приоритет, а
    ненаблюдаемое следствие того, что на ОДНОЙ строке (в отличие от агрегата
    `_presence_row_flags`, где РАЗНЫЕ строки одной ячейки могут дать разные
    флаги одновременно) эти два условия взаимно исключают друг друга по
    значению `price`:** `no_weight` требует `is_price(price)` истинным (цена
    конечна и `> 0`), `negative` требует `price < 0` — оба разом с одним и тем
    же `price` не выполняются никогда, и переставленный порядок не меняет ни
    одного ответа (подтверждено снятием — отчёт задачи, круг 1, правка 3).
    Порядок оставлен таким же, как у `_cell_without_ingesting`, ради
    единообразия чтения, а не потому что он что-то решает.

    Фолбэк `no_price` всегда достижим: позиция уже исключена
    (`not (is_price(price) and is_weight(weight))`), и если её не поймала ни
    одна из трёх веток выше, цена пуста или ноль.
    """
    price_nonfinite = price is not None and not price.is_finite()
    weight_nonfinite = weight is not None and not weight.is_finite()
    if price_nonfinite or weight_nonfinite:
        return "not_finite"
    # Ниже порядок НЕНАБЛЮДАЕМ (см. докстроку) — оставлен для единообразия с
    # `_cell_without_ingesting`, а не потому что одна ветка важнее другой.
    if is_price(price) and not is_weight(weight):
        return "no_weight"
    if price is not None and price < 0:
        return "negative"
    return "no_price"


def _cell_item(r) -> dict:
    """Строка drill-down: валовое из файла, нетто из ячейки, база между ними и
    признак вхождения в ставку (задача 3 плана правила цены, спека §2.8).

    `unit_cost_total` НЕ трогается — это исходные деньги файла, и спека пересчёта
    §2.4 обещает их посимвольное совпадение. `unit_cost_net` добавляется рядом:
    без него человек складывал бы валовые, а ячейка показывала бы нетто
    (`test_matrix_cell_drilldown_survives_the_migration`).

    Носитель — `_all_positions_select`: строка может быть НЕВОШЕДШЕЙ (её цена
    или вес не проходят `is_price`/`is_weight`). Невошедшая строка несёт деньги
    файла ДОСЛОВНО и причину невхождения (`excluded_reason`), но ОТКЛОНЕНИЕ для
    неё не вычисляется вовсе (решение «чего не считать для невошедшей строки»):
    норматив и база НДС ей не нужны ни для чего, и вызывать ради неё
    `_net_deviation` значило бы приписать позиции факт, которого система не
    утверждает. `deviation_pct`/`deviation_reason` невошедшей строки поэтому
    пусты ОБА — и это не перегрузка смысла пустоты (`docs/insights/
    one-value-two-states.md`): различитель — `included`, читаемое ПЕРВЫМ.
    `deviation_reason` невошедшей строки никогда не `"no_rate"` — этот код
    придуман для ЯЧЕЙКИ (`_cell_without_ingesting`), а не для позиции: у
    позиционных поверхностей `deviation_reason` сохраняет сегодняшний перечень
    значений (Global Constraints плана, ревизия §4 спеки §2.7).
    """
    included = is_price(r.unit_cost_total) and is_weight(r.weight)
    if included:
        net, deviation_pct, reason = _net_deviation(
            r.unit_cost_total, r.vat_rate_base, r.standard_unit_rate
        )
        excluded_reason = None
    else:
        net = None
        deviation_pct = None
        reason = None
        excluded_reason = _row_exclusion_reason(r.unit_cost_total, r.weight)
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
        "included": included,
        "excluded_reason": excluded_reason,
    }


def get_matrix_cell(db: Session, *, contract_id: int, catalog_position_id: int) -> dict:
    """Drill-down по ячейке (§6, задача 3 плана правила цены — спека §2.8):
    ВСЕ позиции работы в последней смете договора, а не только вошедшие в
    ставку.

    До этой задачи показывались только строки, участвовавшие в расчёте
    (`weight > 0` поверх VIEW). Теперь носитель — `_all_positions_select`:
    ячейка `rate_reason = "no_price"` (и любая другая пустая ставка) обязана
    открыть НЕПУСТОЙ список строк с признаком невхождения и его причиной —
    иначе утверждение экрана «работа есть, цены нет» нечем было бы проверить
    (решение о носителе, отчёт задачи 2 плана правила цены).
    """
    estimate = get_latest_estimate(db, contract_id)
    if estimate is None:
        raise DomainError(404, f"У договора {contract_id} нет ни одной сметы.")

    rows = db.execute(
        _all_positions_select(estimate.id)
        .where(PositionItem.catalog_position_id == catalog_position_id)
        .order_by(
            PositionItem.total_cost_total.desc().nulls_last(),
            PositionItem.id,
        )
    ).all()

    return {
        "contract_id": contract_id,
        "catalog_position_id": catalog_position_id,
        "estimate_id": estimate.id,
        "amendment_no": estimate.amendment_no,
        "items": [_cell_item(r) for r in rows],
    }
