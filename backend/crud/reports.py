"""Данные для выгрузок §7.6: свод по договору и отчёт «для банка».

Макет отчёта «для банка» **согласован с пользователем** 2026-08-04 (§6.1 брифинга,
`AGENTS.md` §7.6 требует именно согласования, а не выдумывания):

| Что | Решение |
|---|---|
| Разрез | класс → работа |
| Колонки | работа, единица, объём, ставка, норматив, отклонение %, отклонение в деньгах |
| Итоги | по каждому классу **и** общий |
| Шапка | реквизиты выборки + блок подписей |

Два отчёта — **два файла** (решение §6.6): у них разный охват и разные параметры,
свод берёт один договор, а «для банка» — выборку из многих.

**Отклонение в итогах — средневзвешенное по объёму, не среднее арифметическое
процентов.** Разница не косметическая: работа на 12 млн с отклонением +5 % и работа
на 40 тыс. с +80 % дают среднее арифметическое +42,5 %, тогда как фактическая
переплата — около +5 %. Банку показывают вторую цифру. Поэтому итог считается как
`(факт − норматив) / норматив` по суммам, а не усреднением процентов.

**Отчёт «для банка» — нетто-ось (задача 4 пересчёта НДС, спека §2.5).** Выборка
охватывает много договоров с разными целевыми ставками НДС, поэтому общей оси,
кроме нетто, у неё нет: норматив — цена без НДС по определению (ревизия `AGENTS.md`
§4), и сравнивать его с валовым фактом означало бы сравнивать разные единицы
измерения. Факт приводится к нетто через `money.vat.gross_to_net` по БАЗОВОЙ
ставке НДС конкретной позиции (`vat_rate_base` из `DEVIATION_INPUTS`); свод по
договору (отчёт «а») — однодоговорная поверхность, и задача 8 переводит его на
ставку ПОКАЗА (`money.vat.effective_display_rate`, спека §5.1): факт и норматив
показываются в ОДНОЙ и той же ставке (цель, иначе перекрытая база, иначе
единогласная заявленная ставка предложений; `None` при разногласии — тогда
норматив гасится, а факт остаётся в СВОИХ, исходных ставках построчно).

**Счётчики исключённого — разбиение на ЧЕТЫРЕ класса, а не пересечение** (задача 4,
приоритет строго сверху вниз, и он не декоративный):

1. без объёма — блокирующая причина: норматив без объёма не помог бы, поэтому
   позиция без объёма И без базы НДС считается здесь один раз, а не в следующем
   классе;
2. с объёмом, но без базы НДС — нетто не выведено, сравнивать не с чем;
3. с объёмом и базой, но без норматива;
4. сравнимые (в строках отчёта).

Каждая расценённая позиция выборки попадает ровно в один класс. В итогах
печатается счётчик сравнимых позиций и три счётчика исключённого, а в сноске —
независимо посчитанное «Всего расценённых позиций»: сумма четырёх напечатанных
чисел равна ему, и это настоящий инвариант, а не тавтология — общий счёт берётся
отдельным `count(*)` по VIEW, не суммой напечатанного. Строка отчёта агрегирует
работу (несколько позиций → одна строка), поэтому по числу строк равенство не
проверить — разбиение печатается счётчиками именно для того, чтобы сходиться
**по файлу**.

**Строки отчёта «для банка» — только работы, у которых норматив есть.** Так прямо
попросил пользователь: «позиции без норматива в расчёт отклонения не входят и
показываются отдельным счётчиком». Иначе они разбавили бы средневзвешенное
отклонение вниз, и отчёт занизил бы переплату — то есть соврал бы в пользу
подрядчика.

**Итоги считаются в Python, а не в SQL** — в отличие от матрицы (решение §6.3). Это
не противоречие: там SQL выигрывал потому, что Python пришлось бы перекачивать
140 тысяч строк ради 1200 ячеек, а здесь строки и есть содержимое файла, они
транспортируются в любом случае. Суммировать уже полученное дешевле, чем добавлять
второй запрос.

**Округление — только на границе, и только там, где ничего больше не суммируется**
(§7.6, `global-constraints.md`). Слагаемые (`comparable_amount`,
`comparable_standard_amount`) остаются НЕОКРУГЛЁННЫМИ до самого конца функции:
`_totals_of` и промежуточные строки суммируют точные величины, и только когда все
суммы (по классу и по выборке) уже посчитаны, `bank_comparison` округляет деньги
для показа — иначе `Σ round(x) ≠ round(Σ x)` тихо разъехалось бы с числом, которое
банк получит, сложив колонку на калькуляторе.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.analytics import (
    DEVIATION_INPUTS,
    column_scope_filters,
    get_latest_estimate,
    latest_estimates,
    scope_filters,
)
from crud.common import DomainError, iso
from models import (
    CatalogPosition,
    Contract,
    Contractor,
    Lot,
    ObjectModel,
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

#: Ноль как Decimal — чтобы суммирование не начиналось с int и не давало float.
ZERO = Decimal(0)

#: Позиция расценена, но взвесить её нечем: оба количества пусты либо объём ≤ 0.
#: Дополнение к фильтру `weight > 0` — то, что он отбрасывает. Блокирующая причина
#: (класс 1 из четырёх, задача 4): срабатывает раньше проверки базы НДС и норматива.
_NO_VOLUME = sa.or_(DEVIATION_INPUTS.c.weight.is_(None), DEVIATION_INPUTS.c.weight <= 0)


def _weighted(amount: Decimal | None, volume: Decimal | None) -> Decimal | None:
    """Средневзвешенная величина; `None`, если веса нет (§6: ячейка пустая)."""
    if amount is None or volume is None or volume == 0:
        return None
    return amount / volume


def _amount_sort_key(amount: Decimal | None) -> tuple[int, Decimal]:
    """Ключ сортировки строк отчёта по убыванию суммы, устойчивый к `NaN`/`Infinity`.

    Правка 3 (ре-ревью финала ветки): открытый хвост Ф4 (§5.6) пропускает
    нефинитную стоимость при импорте, и она доезжает до `amount`/
    `comparable_amount`. Раньше порядок строк задавал SQL `ORDER BY`, которому
    `NaN` безразличен; эта ветка перенесла свёртку в Python (§2.6), а
    `Decimal('NaN') < x` в контексте с трапами бросает `InvalidOperation` —
    сравнение внутри `list.sort` роняло бы отчёт 500-й на первом же файле с
    такой строкой.

    Нефинитная сумма — не ноль и не «самая большая»: она уходит в КОНЕЦ, тем
    же приёмом, что «вес неизвестен» у строки матрицы (`NULLS LAST`, спека
    §2.6) — недостоверное число не должно всплывать наверх списка. Первый
    элемент кортежа разводит финитные и нефинитные суммы в две группы, чтобы
    `Decimal`-сравнение между ними (второй элемент) никогда не происходило;
    внутри группы нефинитных чисел второй элемент — общий `ZERO`, тай-брейк
    полностью ложится на `catalog_position_id`, добавляемый вызывающим кодом.
    """
    value = amount if amount is not None else ZERO
    if not value.is_finite():
        return (1, ZERO)
    return (0, -value)


def _deviation_pct(fact: Decimal | None, standard: Decimal | None) -> Decimal | None:
    """Отклонение по СУММАМ, а не усреднение процентов (см. модульную документацию)."""
    if fact is None or standard is None or standard == 0:
        return None
    return (fact / standard - 1) * 100


# ---------------------------------------------------------------------------
#  (а) Свод расценок по договору с отклонениями
# ---------------------------------------------------------------------------

def _declared_rates(db: Session, estimate_id: int) -> list[Decimal | None]:
    """Заявленные ставки НДС предложений сметы — СЫРОЙ список (задача 8, спека
    §5.1): `effective_display_rate` сам сворачивает его в единогласную ставку
    или `None` при разногласии/незнании/отсутствии предложений, поэтому
    сворачивать его здесь ЕЩЁ РАЗ незачем."""
    return list(
        db.execute(
            sa.select(Proposal.vat_rate)
            .select_from(Proposal)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimate_id)
        ).scalars().all()
    )


def _standard_in_display_rate(standard: Decimal | None, rate: Decimal | None) -> Decimal | None:
    """Норматив — цена без НДС; на одно-договорной поверхности он показывается в
    ЭФФЕКТИВНОЙ ставке поверхности, той же, в которой показан факт (задача 8,
    спека §5.1).

    `rate is None` (разногласие заявленных ставок предложений, оговорённая
    граница §5.1) гасит норматив целиком: показать «в какой-то из» ставок
    нельзя. Отклонение от приведения не меняется: приведение ОБЕИХ сторон к
    одной ставке отношения не меняет.
    """
    if standard is None or rate is None:
        return None
    return net_to_gross(standard, rate)


def contract_summary(db: Session, contract_id: int) -> dict:
    """Свод по договору: все расценённые работы последней сметы с отклонениями.

    В отличие от отчёта «для банка», работы **без** норматива здесь остаются: это
    свод по договору, а не сравнение с нормативами, и человек должен видеть весь
    предмет торга. У таких строк отклонение — `None`, что на листе печатается как
    «нет норматива» (§10 требует отличать это от нуля).

    **Однодоговорная поверхность в СТАВКЕ ПОКАЗА (задача 8, спека §5.1).** Факт и
    норматив показываются в ОДНОЙ и той же ставке — `effective_display_rate`
    (цель, иначе перекрытая база, иначе единогласная заявленная ставка
    предложений; `None` при разногласии). Факт приводится К КАЖДОЙ группе
    работа×база СВОЕЙ базой (`_fold_summary_work`, тот же приём, что у
    `_fold_bank_work`), норматив (уже нетто) — одной конвертацией `net_to_gross`
    в конце: у него нет собственной базы НДС, разносить его по группам незачем.
    """
    contract_row = db.execute(
        sa.select(Contract, ObjectModel.title, Contractor.title, RateClass.title)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
        .where(Contract.id == contract_id)
    ).first()
    if contract_row is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    contract, object_title, contractor_title, rate_class_title = contract_row

    estimate = get_latest_estimate(db, contract_id)
    header = {
        "contract_number": contract.contract_number,
        "contract_title": contract.title,
        "object_title": object_title,
        "contractor_title": contractor_title,
        "rate_class_title": rate_class_title,
        "signer": contract.signer,
        "signed_date": iso(contract.signed_date),
        "total_amount": contract.total_amount,
        "estimate_amendment_no": estimate.amendment_no if estimate else None,
        "estimate_date": iso(estimate.data_prepared_on_date) if estimate else None,
        # Подпись листа (задача 8, шаг 6): в какой ставке показаны суммы этого
        # свода — «с НДС N %» либо «без НДС» при ставке 0, либо не подписывается
        # вовсе при разногласии (единой ставки показа нет). Ключ есть на ОБОИХ
        # путях функции (правило «форма ответа одна и та же», см. `crud.
        # project_passport`), даже когда сметы ещё нет вовсе.
        "vat_display_rate": None,
        # Ревью задачи 8 (Правка 6): при разногласии заявленных ставок норматив
        # гасится (граница §5.1), но `amount` строки ВСЁ РАВНО складывает
        # валовые из РАЗНЫХ ставок построчно — лист обязан назвать это явно,
        # а не молчать о единицах измерения (спека §2.5, строка 280).
        "vat_display_note": None,
    }
    if estimate is None:
        return {"header": header, "rows": [], "totals": _empty_report_totals()}

    declared_rates = _declared_rates(db, estimate.id)
    effective_rate = effective_display_rate(
        estimate.vat_rate_target, estimate.vat_rate_base_override, declared_rates
    )
    header["vat_display_rate"] = effective_rate
    if effective_rate is None:
        # Правка 2 (ре-ревью финала ветки): `effective_rate is None` смешивает ДВЕ
        # разные причины, и подпись листа обязана назвать ту, что случилась на
        # самом деле, а не одну на двоих (§10, спека §2.5). Различитель —
        # ЗАЯВЛЕННЫЕ ставки предложений, а не `any_unknown_base` внутри
        # `_fold_summary_work` (та задаётся ПО РАБОТЕ и не видна здесь):
        #   хотя бы одна ставка `NULL` → база неизвестна хотя бы у одного
        #     предложения, норматив показывается СЫРЫМ нетто (`_fold_summary_work`,
        #     ветка `any_unknown_base`, спека §2.5 строка 293) — отклонение не
        #     вычисляется, а не «не показан»;
        #   все ставки известны, но не совпадают → настоящее разногласие (§5.1),
        #     и вот тогда норматив гасится целиком — здесь текст прежний.
        if any(rate is None for rate in declared_rates):
            header["vat_display_note"] = (
                "База НДС неизвестна хотя бы у одного предложения сметы: "
                "норматив показан как нетто без пересчёта в единую ставку, "
                "отклонение не вычислено — это ДРУГАЯ причина, чем «нет "
                "норматива» (AGENTS.md §4)."
            )
        else:
            header["vat_display_note"] = (
                "Единой ставки НДС нет (предложения заявили разные ставки): "
                "суммы складывают строки в ИХ ИСХОДНЫХ ставках, норматив не "
                "показан."
            )

    group_rows = db.execute(
        _work_aggregate_select()
        .where(DEVIATION_INPUTS.c.estimate_id == estimate.id, DEVIATION_INPUTS.c.weight > 0)
        .group_by(
            DEVIATION_INPUTS.c.catalog_position_id,
            CatalogPosition.standard_job_title,
            UnitOfMeasure.code,
            DEVIATION_INPUTS.c.vat_rate_base,
        )
    ).all()

    rows, fact_restated_any, standard_shown_any = _fold_summary_rows(group_rows, effective_rate)
    # Итоги — из НЕОКРУГЛЁННЫХ строк (см. модульную документацию про границу
    # округления): `_totals_of` читает `amount`/`comparable_amount`/
    # `comparable_standard_amount`, и они обязаны остаться точными ДО того, как
    # `_quantize_summary_row` округлит поля строк ниже — иначе получилось бы
    # `Σ round(x)` вместо `round(Σ x)`. Служебные ключи `_fact_restated`/
    # `_standard_shown`, приложенные `_fold_summary_rows`, `_totals_of` не
    # трогает (читает только именованные денежные поля).
    totals = _totals_of(rows)
    # Что отбросил фильтр `weight > 0` — счётчиком, не молчанием. Свод показывает
    # предмет торга целиком, и позиция с ценой, но без объёма, обязана быть хотя бы
    # упомянута: в строку ей нельзя (взвешивать нечем, §6), но исчезнуть без следа
    # ей тоже нельзя. Находка собственного ревью фазы.
    totals["positions_without_volume"] = db.execute(
        sa.select(sa.func.count())
        .select_from(DEVIATION_INPUTS)
        .where(DEVIATION_INPUTS.c.estimate_id == estimate.id, _NO_VOLUME)
    ).scalar_one()
    # Общий счёт — НЕЗАВИСИМЫМ count(*) по VIEW, без фильтра объёма: равенство
    # «сравнимые + без норматива + без объёма = всего» становится проверяемым
    # инвариантом файла, а не суммой напечатанных чисел (замечание ревью).
    totals["positions_priced"] = db.execute(
        sa.select(sa.func.count())
        .select_from(DEVIATION_INPUTS)
        .where(DEVIATION_INPUTS.c.estimate_id == estimate.id)
    ).scalar_one()

    # Округление — ПОСЛЕДНИЙ шаг, когда totals уже посчитаны из точных строк, и
    # ПОЛЕВЫМ гейтом (ре-ревью задачи 8, круг 3, Правка 1 — круг 2 квантовал
    # ЦЕЛУЮ строку одним флагом, поднятым одним лишь показом норматива, и
    # ломал тождество §2.4 на факте, который никто не пересчитывал): факт
    # (`amount`/`rate`) квантуется, только если РЕАЛЬНО пересчитан
    # (`_fact_restated`); норматив (`standard_unit_rate`) — как только вообще
    # посчитан (`_standard_shown`, у него нет ветки тождества).
    for row in rows:
        _quantize_summary_row(row)
    _quantize_summary_totals(totals, fact_restated_any=fact_restated_any, standard_shown_any=standard_shown_any)
    return {"header": header, "rows": rows, "totals": totals}


def _work_aggregate_select():
    """Слагаемые группы свода по договору: работа × база НДС (задача 8, спека §5.1).

    Группировка ДОПОЛНИТЕЛЬНО идёт по `vat_rate_base` — тем же приёмом, что у
    отчёта «для банка» (`_bank_position_groups_select`) и у паспорта проекта
    (`crud.project_passport._direct_totals`): множитель приведения к ставке
    показа постоянен внутри группы одной базы, поэтому `restate_gross` можно
    вызвать РАЗ на группу (`_fold_summary_work`), а не на каждую позицию — при
    том, что базы внутри ОДНОЙ работы вполне могут различаться (несколько
    предложений на одном договоре). Общего определения «сравнимо» у двух
    отчётов НЕТ: здесь оно по-прежнему только про норматив — эта поверхность
    однодоговорная, и целевая ставка гасит норматив ЦЕЛИКОМ уже там, где
    заявленные ставки предложений разошлись (`effective_display_rate`), без
    отдельного счётчика «без базы», который есть только у отчёта «для банка».

    `FILTER (WHERE rate_standard_id IS NOT NULL)` отделяет сравнимые строки от
    остальных **внутри одного проходa**: иначе понадобился бы второй запрос, а его
    результат пришлось бы сшивать с первым по ключу.

    `fact_amount_total` (по всем строкам группы) нужен своду по договору — он
    показывает весь предмет торга; `fact_amount` (только по сравнимым) нужен
    отклонению, чтобы оно считалось от той же совокупности, что норматив.
    """
    comparable = DEVIATION_INPUTS.c.rate_standard_id.isnot(None)
    weighted_fact = DEVIATION_INPUTS.c.unit_cost_total * DEVIATION_INPUTS.c.weight
    weighted_standard = DEVIATION_INPUTS.c.standard_unit_rate * DEVIATION_INPUTS.c.weight
    return (
        sa.select(
            DEVIATION_INPUTS.c.catalog_position_id.label("catalog_position_id"),
            CatalogPosition.standard_job_title.label("job_title"),
            UnitOfMeasure.code.label("unit_code"),
            DEVIATION_INPUTS.c.vat_rate_base.label("vat_rate_base"),
            sa.func.sum(weighted_fact).label("fact_amount_total"),
            sa.func.sum(DEVIATION_INPUTS.c.weight).label("volume_total"),
            sa.func.sum(weighted_fact).filter(comparable).label("fact_amount"),
            sa.func.sum(DEVIATION_INPUTS.c.weight).filter(comparable).label("volume"),
            sa.func.sum(weighted_standard).filter(comparable).label("standard_amount"),
            sa.func.count().label("positions"),
            sa.func.count().filter(~comparable).label("positions_without_standard"),
        )
        .select_from(DEVIATION_INPUTS)
        .join(CatalogPosition, CatalogPosition.id == DEVIATION_INPUTS.c.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == DEVIATION_INPUTS.c.unit_id)
    )


def _fold_summary_work(
    job_title: str, unit_code: str | None, groups, effective_rate
) -> tuple[dict, bool, bool]:
    """Одна строка свода по договору из групп работа×база НДС (задача 8, спека §5.1).

    Факт приводится к ставке показа ПО КАЖДОЙ группе — её база постоянна внутри
    группы (тот же приём, что у `_fold_bank_work`); при `effective_rate is None`
    (нет ни цели, ни перекрытой базы, ни единогласия предложений) `restate_gross`
    для каждой группы уходит в ветку тождества (эффективная ставка равна СВОЕЙ
    базе группы), и факт остаётся построчно в своих исходных ставках — той же
    формой, что и до задачи 8.

    Норматив (уже нетто) НЕ разносится по группам: у него нет собственной базы
    НДС, конвертировать его частями незачем — он копится СЫРЫМ (`standard_net_
    total`).

    **НЕИЗВЕСТНОСТЬ базы отличена от РАЗНОГЛАСИЯ ставок** (ре-ревью задачи 8,
    круг 4, Правка 2 — тот же дефект, что чинился на паспорте Ф6 кругом 3,
    просто на этой поверхности; спека §2.5, строка 293, дословно: «норматив
    при неизвестной базе показывается как нетто; не вычисляется только
    отклонение»). Признак — `vat_rate_base` САМИХ ГРУПП этой работы, а не
    `effective_rate is None` (та даёт `None` в ОБОИХ случаях и сама их не
    различает): если у ХОТЯ БЫ ОДНОЙ группы база не заявлена, норматив
    показывается СЫРЫМ нетто без попытки конвертации (конвертировать не во
    что), а отклонение НЕ ВЫЧИСЛЯЕТСЯ (факт этой группы тоже остался
    непересчитанным — сравнивать валовое с нетто было бы сравнением разных
    единиц). Если базы ВСЕХ групп известны, но `effective_rate` всё равно
    `None` — это НАСТОЯЩЕЕ разногласие заявленных ставок предложений (§5.1),
    и вот тогда норматив гасится целиком.

    Величины здесь НЕОКРУГЛЕНЫ (см. модульную документацию про границу
    округления): вызывающий код (`contract_summary`) квантует их САМ, ПОСЛЕ
    `_totals_of`, ПОЛЕВЫМ гейтом (см. второй/третий элементы возврата).

    **Гейт квантования — ПО ПОЛЮ, не по строке** (ре-ревью задачи 8, круг 3,
    Правка 1: круг 2 держал ОДИН флаг на всю строку, поднятый одним лишь
    показом норматива, и квантовал ИМ ЖЕ факт, который никто не пересчитывал
    — регресс тождества §2.4 на `amount`/`rate` при любой работе с
    нормативом и без цели). Возвращаются ДВА независимых признака:
    `fact_restated` — хоть одна группа факта получила статус `RESTATED`
    (гейт для `amount`/`rate`); `standard_shown` — норматив РЕАЛЬНО приведён
    к ставке показа (гейт для `standard_unit_rate` — у нормы нет ветки
    тождества при настоящей конвертации, но в ветке «база неизвестна» нормы
    конвертации вообще не было, и квантовать нечего). `deviation_money`
    квантуется, если сработал ХОТЯ БЫ ОДИН из двух гейтов (обе его стороны
    могли внести нецелые хвосты).
    """
    fact_total: Decimal | None = None
    volume_total = ZERO
    fact_comparable: Decimal | None = None
    volume_comparable = ZERO
    standard_net_total = ZERO
    positions_total = 0
    positions_without_standard_raw = 0
    fact_restated = False

    for group in groups:
        positions_total += group.positions
        positions_without_standard_raw += group.positions_without_standard

        restated_total = restate_gross(group.fact_amount_total, group.vat_rate_base, effective_rate)
        if restated_total.status is AmountStatus.RESTATED:
            fact_restated = True
        if restated_total.amount is not None:
            fact_total = (
                restated_total.amount if fact_total is None else fact_total + restated_total.amount
            )
        if group.volume_total is not None:
            volume_total += group.volume_total

        if group.fact_amount is not None:
            restated_comparable = restate_gross(
                group.fact_amount, group.vat_rate_base, effective_rate
            )
            if restated_comparable.status is AmountStatus.RESTATED:
                fact_restated = True
            fact_comparable = (
                restated_comparable.amount
                if fact_comparable is None
                else fact_comparable + restated_comparable.amount
            )
            volume_comparable += group.volume or ZERO
            standard_net_total += group.standard_amount or ZERO

    any_unknown_base = any(group.vat_rate_base is None for group in groups)
    # Правка 2 (ре-ревью финала ветки, `comparable_positions` реально сравнимых
    # позиций): при `any_unknown_base` отклонение гасится для ВСЕЙ работы (ветки
    # ниже, `comparable_amount`/`comparable_standard_amount` уходят в `None`
    # целиком) — значит НИ ОДНА позиция этой работы не попадает в расчёт
    # отклонения, независимо от того, найден ли у неё норматив. Приоритет тот
    # же, что у отчёта «для банка» (`_fold_bank_work`): неизвестная база
    # перевешивает отсутствие норматива, поэтому позиции уходят в СВОЙ счётчик
    # базы целиком, а не остаются вдобавок в «без норматива» — иначе
    # `_totals_of` вычла бы их из `comparable_positions` дважды.
    if any_unknown_base:
        positions_without_standard = 0
        positions_without_vat_base = positions_total
    else:
        positions_without_standard = positions_without_standard_raw
        positions_without_vat_base = 0
    if fact_comparable is None:
        standard_amount = None
        standard_shown = False
    elif any_unknown_base:
        # База ХОТЯ БЫ ОДНОЙ группы не заявлена: норматив показывается СЫРЫМ
        # нетто, без попытки конвертации (спека §2.5, строка 293) — не
        # квантуется, конвертации не было (та же логика, что у факта в
        # ветке тождества).
        standard_amount = standard_net_total
        standard_shown = False
    else:
        # Базы ВСЕХ групп известны — если `effective_rate` всё же `None`,
        # это настоящее разногласие заявленных ставок (§5.1), и норматив
        # гасится (`_standard_in_display_rate` вернёт `None`).
        standard_amount = _standard_in_display_rate(standard_net_total, effective_rate)
        standard_shown = standard_amount is not None
    deviation_pct = None if any_unknown_base else _deviation_pct(fact_comparable, standard_amount)
    deviation_money = (
        None if any_unknown_base or fact_comparable is None or standard_amount is None
        else fact_comparable - standard_amount
    )
    row = {
        "job_title": job_title,
        "unit_code": unit_code,
        "volume": volume_total,
        "rate": _weighted(fact_total, volume_total),
        "standard_unit_rate": _weighted(standard_amount, volume_comparable),
        "amount": fact_total,
        "deviation_pct": deviation_pct,
        "deviation_money": deviation_money,
        # Сравнимая часть — по ней считаются итоги, и она может быть меньше
        # строки. При неизвестной базе (`any_unknown_base`) — `None`, не
        # сырые валовое/нетто: строка не участвует в суммах ИТОГОВ (`_totals_
        # of`), иначе они смешали бы единицы этой строки с приведёнными
        # суммами других работ отчёта.
        "comparable_amount": None if any_unknown_base else fact_comparable,
        "comparable_standard_amount": None if any_unknown_base else standard_amount,
        "positions": positions_total,
        "positions_without_standard": positions_without_standard,
        # Не печатается отдельной строкой на своде (задача 4 отчёта «для банка»
        # его не трогала — у свода своя форма макета, §6.1 согласован
        # отдельно): нужен только `_totals_of`, чтобы `comparable_positions` не
        # засчитывал позиции, чьё отклонение погашено неизвестной базой НДС.
        "positions_without_vat_base": positions_without_vat_base,
    }
    return row, fact_restated, standard_shown


def _fold_summary_rows(group_rows, effective_rate) -> tuple[list[dict], bool, bool]:
    """Строки SQL (работа×база) -> строки свода (одна на работу), задача 8.

    Тот же двухпроходный приём, что у `_fold_bank_rows`: группы одной работы не
    обязаны идти в результате подряд (разных баз может быть несколько),
    поэтому сначала собираем их по ключу, потом сворачиваем.

    Второй/третий элементы возврата — `fact_restated_any`/`standard_shown_any`,
    ИТОГОВЫЕ (по всему своду) версии полевых гейтов `_fold_summary_work`: они
    гейтуют квантование `totals` (см. `contract_summary`) — САМИ строки
    квантуются `contract_summary` по СВОИМ, построчным флагам, приложенным к
    каждой строке служебными ключами `_fact_restated`/`_standard_shown`
    (снимаются перед возвратом наружу).
    """
    order: list[int] = []
    meta: dict[int, tuple[str, str | None]] = {}
    groups_by_work: dict[int, list] = {}
    for r in group_rows:
        key = r.catalog_position_id
        if key not in meta:
            meta[key] = (r.job_title, r.unit_code)
            order.append(key)
            groups_by_work[key] = []
        groups_by_work[key].append(r)

    rows = []
    fact_restated_any = False
    standard_shown_any = False
    for key in order:
        job_title, unit_code = meta[key]
        row, fact_restated, standard_shown = _fold_summary_work(
            job_title, unit_code, groups_by_work[key], effective_rate
        )
        row["catalog_position_id"] = key
        row["_fact_restated"] = fact_restated
        row["_standard_shown"] = standard_shown
        rows.append(row)
        fact_restated_any = fact_restated_any or fact_restated
        standard_shown_any = standard_shown_any or standard_shown

    # Крупные работы сверху — тот же порядок, что раньше давал `ORDER BY
    # fact_amount_total DESC` в SQL (перешёл в Python: строки теперь
    # сворачиваются здесь, а не приходят готовыми из SQL). Тай-брейк по
    # `catalog_position_id` — та же причина, что у `_fold_bank_rows`: без него
    # порядок работ с РАВНОЙ суммой ничем не определён.
    rows.sort(key=lambda r: (*_amount_sort_key(r["amount"]), r["catalog_position_id"]))
    return rows, fact_restated_any, standard_shown_any


def _quantize_summary_row(row: dict) -> dict:
    """Округление ГРАНИЦЫ одной строки свода — полевым гейтом (ре-ревью задачи
    8, круг 3, Правка 1): `amount`/`rate` квантуются, только если факт этой
    СТРОКИ реально пересчитан (`row["_fact_restated"]`); `standard_unit_rate`
    — как только вообще посчитан (`row["_standard_shown"]`, у нормы нет
    ветки тождества); `deviation_money` — если сработал ХОТЯ БЫ ОДИН из двух
    гейтов. Служебные ключи `_fact_restated`/`_standard_shown` снимаются —
    это внутреннее состояние свёртки, не часть контракта ответа.
    """
    fact_restated = row.pop("_fact_restated")
    standard_shown = row.pop("_standard_shown")
    if fact_restated:
        row["amount"] = quantize_money(row["amount"])
        row["rate"] = quantize_money(row["rate"])
    if standard_shown:
        row["standard_unit_rate"] = quantize_money(row["standard_unit_rate"])
    if row["deviation_money"] is not None and (fact_restated or standard_shown):
        row["deviation_money"] = quantize_money(row["deviation_money"])
    return row


def _quantize_summary_totals(totals: dict, *, fact_restated_any: bool, standard_shown_any: bool) -> dict:
    """То же самое полевое округление для готовых ИТОГОВ свода (см.
    `_quantize_summary_row`) — те же два гейта, свёрнутые по ИЛИ через все
    строки (`_fold_summary_rows`)."""
    if fact_restated_any:
        totals["amount"] = quantize_money(totals["amount"])
    if standard_shown_any:
        totals["standard_amount"] = quantize_money(totals["standard_amount"])
    if totals["deviation_money"] is not None and (fact_restated_any or standard_shown_any):
        totals["deviation_money"] = quantize_money(totals["deviation_money"])
    return totals


# ---------------------------------------------------------------------------
#  (б) Сравнение с нормативами «для банка»: класс → работа
# ---------------------------------------------------------------------------

def _bank_position_groups_select():
    """Слагаемые группы отчёта «для банка»: работа × база НДС (задача 4, спека §2.4).

    Группировка ДОПОЛНИТЕЛЬНО идёт по `vat_rate_base` — тем же приёмом, что у
    матрицы (`crud/analytics.py::_cell_groups_cte`): множитель пересчёта в нетто
    постоянен внутри группы одной базы, поэтому `gross_to_net` можно вызвать РАЗ на
    группу (`_fold_bank_work`), а не на каждую позицию — при том, что базы внутри
    ОДНОЙ работы одного класса вполне могут различаться (выборка «для банка» берёт
    много договоров/предложений, у каждого своя ставка), и без этой группировки
    один вызов `gross_to_net` на всю сумму работы был бы арифметически неверен.
    Норматив уже нетто по определению (ревизия `AGENTS.md` §4) и в пересчёте не
    нуждается.

    `positions_without_standard` считается ВНУТРИ группы известной базы. Группа с
    `vat_rate_base IS NULL` в этот счётчик не попадает вовсе — по приоритету §7.6
    неизвестная база блокирует раньше, чем отсутствие норматива, и это разбирает
    `_fold_bank_work`, а не эта функция: здесь только сырые слагаемые.
    """
    comparable = DEVIATION_INPUTS.c.rate_standard_id.isnot(None)
    weighted_fact = DEVIATION_INPUTS.c.unit_cost_total * DEVIATION_INPUTS.c.weight
    weighted_standard = DEVIATION_INPUTS.c.standard_unit_rate * DEVIATION_INPUTS.c.weight
    return (
        sa.select(
            DEVIATION_INPUTS.c.rate_class_id.label("rate_class_id"),
            DEVIATION_INPUTS.c.catalog_position_id.label("catalog_position_id"),
            CatalogPosition.standard_job_title.label("job_title"),
            UnitOfMeasure.code.label("unit_code"),
            DEVIATION_INPUTS.c.vat_rate_base.label("vat_rate_base"),
            sa.func.sum(weighted_fact).filter(comparable).label("weighted_fact_gross"),
            sa.func.sum(DEVIATION_INPUTS.c.weight).filter(comparable).label("weight_comparable"),
            sa.func.sum(weighted_standard).filter(comparable).label("weighted_standard"),
            sa.func.count().label("positions"),
            sa.func.count().filter(~comparable).label("positions_without_standard"),
        )
        .select_from(DEVIATION_INPUTS)
        .join(CatalogPosition, CatalogPosition.id == DEVIATION_INPUTS.c.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == DEVIATION_INPUTS.c.unit_id)
    )


def _fold_bank_work(job_title: str, unit_code: str | None, groups) -> dict:
    """Одна строка отчёта «для банка» из групп работа×база НДС (задача 4).

    Группа с известной базой приводится к нетто ОДНИМ вызовом `gross_to_net` (её
    гросс-сумма уже свёрнута SQL-ом — множитель пересчёта постоянен внутри группы);
    группа с `vat_rate_base IS NULL` целиком уходит в счётчик «без базы»,
    НЕЗАВИСИМО от того, есть ли в ней норматив: приоритет §7.6 ставит неизвестную
    базу выше отсутствия норматива.

    Величины здесь НЕОКРУГЛЕНЫ (см. модульную документацию про границу округления):
    `comparable_amount`/`comparable_standard_amount` идут дальше в `_totals_of` как
    есть, а показ (`amount`, `rate`, `standard_unit_rate`, `deviation_money`)
    округляет вызывающий (`bank_comparison`) в самом конце, когда все суммы уже
    посчитаны.
    """
    net_fact_total = ZERO
    weight_total = ZERO
    standard_weighted_total = ZERO
    positions_total = 0
    positions_without_standard = 0
    positions_without_vat_base = 0
    has_comparable = False

    for group in groups:
        positions_total += group.positions
        if group.vat_rate_base is None:
            positions_without_vat_base += group.positions
            continue
        positions_without_standard += group.positions_without_standard
        if group.weighted_fact_gross is not None:
            has_comparable = True
            net_fact_total += gross_to_net(group.weighted_fact_gross, group.vat_rate_base)
            weight_total += group.weight_comparable
            standard_weighted_total += group.weighted_standard

    volume = weight_total if has_comparable else None
    amount = net_fact_total if has_comparable else None
    standard_amount = standard_weighted_total if has_comparable else None
    deviation_money = (
        None if amount is None or standard_amount is None else amount - standard_amount
    )
    return {
        "job_title": job_title,
        "unit_code": unit_code,
        "volume": volume,
        "rate": _weighted(amount, volume),
        "standard_unit_rate": _weighted(standard_amount, volume),
        "amount": amount,
        "deviation_pct": _deviation_pct(amount, standard_amount),
        "deviation_money": deviation_money,
        # Сравнимая часть — по ней считаются итоги (см. `_totals_of`); совпадает с
        # `amount`/`standard_amount` здесь, потому что видимая строка отчёта «для
        # банка» и есть только сравнимая часть работы (§7.6).
        "comparable_amount": amount,
        "comparable_standard_amount": standard_amount,
        "positions": positions_total,
        "positions_without_standard": positions_without_standard,
        "positions_without_vat_base": positions_without_vat_base,
    }


def _fold_bank_rows(group_rows) -> dict[int, list[dict]]:
    """Строки SQL (работа×база) → строки отчёта (одна на класс×работу), по классам.

    Два прохода — тот же приём, что у матрицы (`crud/analytics.py::
    _shape_matrix_rows`): группы одной работы не обязаны идти в результате подряд
    (разных баз может быть несколько), поэтому сначала собираем их по ключу, потом
    сворачиваем.
    """
    order: list[tuple[int, int]] = []
    meta: dict[tuple[int, int], tuple[str, str | None]] = {}
    groups_by_work: dict[tuple[int, int], list] = {}
    for r in group_rows:
        key = (r.rate_class_id, r.catalog_position_id)
        if key not in meta:
            meta[key] = (r.job_title, r.unit_code)
            order.append(key)
            groups_by_work[key] = []
        groups_by_work[key].append(r)

    rows_by_class: dict[int, list[dict]] = {}
    for key in order:
        class_id, catalog_position_id = key
        job_title, unit_code = meta[key]
        row = _fold_bank_work(job_title, unit_code, groups_by_work[key])
        row["catalog_position_id"] = catalog_position_id
        rows_by_class.setdefault(class_id, []).append(row)

    for bucket in rows_by_class.values():
        # Крупные работы сверху — тот же порядок, что раньше давал `ORDER BY
        # fact_amount DESC` в SQL. Строки без сравнимой части (`comparable_amount
        # is None`) в видимый список всё равно не попадут (см. `kept` ниже),
        # поэтому их место в этой сортировке не важно.
        #
        # Тай-брейк по `catalog_position_id` — та же причина, что у матрицы
        # (`crud/analytics.py`, сортировка `row_amount.desc().nullslast()` +
        # `catalog_position_id.asc()`): без него порядок работ с РАВНОЙ суммой
        # ничем не определён, и два прогона на одних данных могли бы отдать
        # разные файлы. Найдено ревью задачи 4 — до правки список сортировался
        # только по сумме.
        bucket.sort(
            key=lambda r: (*_amount_sort_key(r["comparable_amount"]), r["catalog_position_id"])
        )
    return rows_by_class


def _quantize_row_for_display(row: dict) -> dict:
    """Округление ГРАНИЦЫ для одной строки отчёта — вызывается один раз, когда
    строка уже отслужила свою роль слагаемого в `_totals_of` (задача 4, §7.6)."""
    row["rate"] = quantize_money(row["rate"])
    row["standard_unit_rate"] = quantize_money(row["standard_unit_rate"])
    row["amount"] = quantize_money(row["amount"])
    row["deviation_money"] = quantize_money(row["deviation_money"])
    return row


def _quantize_totals_for_display(totals: dict) -> dict:
    """То же самое для готовых итогов (по классу и по выборке)."""
    totals["amount"] = quantize_money(totals["amount"])
    totals["standard_amount"] = quantize_money(totals["standard_amount"])
    totals["deviation_money"] = quantize_money(totals["deviation_money"])
    return totals


def bank_comparison(
    db: Session,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    rate_class_id: int | None = None,
) -> dict:
    """Отчёт «для банка» по согласованному макету: секции по классам, внутри — работы.

    Выборка та же, что у матрицы (последние сметы договоров плюс фильтры класса и
    периода), поэтому отчёт и экран показывают одно и то же — иначе расхождение
    цифр между экраном и файлом пришлось бы объяснять банку.
    """
    latest = latest_estimates()
    scope = scope_filters(
        rate_class_id=rate_class_id, date_from=date_from, date_to=date_to
    )

    group_rows = db.execute(
        _bank_position_groups_select()
        .join(latest, latest.c.estimate_id == DEVIATION_INPUTS.c.estimate_id)
        .where(DEVIATION_INPUTS.c.weight > 0, *scope)
        .group_by(
            DEVIATION_INPUTS.c.rate_class_id,
            DEVIATION_INPUTS.c.catalog_position_id,
            CatalogPosition.standard_job_title,
            UnitOfMeasure.code,
            DEVIATION_INPUTS.c.vat_rate_base,
        )
    ).all()
    rows_by_class = _fold_bank_rows(group_rows)

    # Классы выборки — из ДОГОВОРОВ с последней сметой, а не из строк, прошедших
    # `weight > 0`. Иначе класс, все расценённые позиции которого без объёма,
    # исчезал из отчёта молча: шапка говорила «Классов: N», а секции не было —
    # нарушение согласованного макета «итоги по каждому классу». Замечание
    # внешнего ревью, подтверждено тестом до правки.
    latest_for_classes = latest_estimates()
    scope_classes = db.execute(
        sa.select(RateClass.id, RateClass.title)
        .select_from(
            sa.join(latest_for_classes, Contract, Contract.id == latest_for_classes.c.contract_id)
            .join(RateClass, RateClass.id == Contract.rate_class_id)
        )
        .where(
            *column_scope_filters(
                rate_class_id=rate_class_id,
                date_from=date_from,
                date_to=date_to,
                latest=latest_for_classes,
            )
        )
        .group_by(RateClass.id, RateClass.title)
        .order_by(RateClass.title.asc())
    ).all()

    # Счётчик «без объёма» — ПО КЛАССАМ, по той же причине: общий скаляр не мог
    # сказать, какому классу принадлежат отброшенные позиции.
    latest_for_volume = latest_estimates()
    no_volume_by_class = dict(
        db.execute(
            sa.select(DEVIATION_INPUTS.c.rate_class_id, sa.func.count())
            .select_from(
                DEVIATION_INPUTS.join(
                    latest_for_volume, latest_for_volume.c.estimate_id == DEVIATION_INPUTS.c.estimate_id
                )
            )
            .where(_NO_VOLUME, *scope)
            .group_by(DEVIATION_INPUTS.c.rate_class_id)
        ).all()
    )

    latest_for_total = latest_estimates()

    sections: list[dict] = []
    for class_id, class_title in scope_classes:
        bucket = rows_by_class.get(class_id, [])
        # Строки без базы НДС и строки без норматива в отчёт не попадают, но их
        # счётчики обязаны дожить до итогов: этого требует согласованный макет
        # (§6.1), расширенный до четырёх классов задачей 4.
        excluded_no_standard = sum(r["positions_without_standard"] for r in bucket)
        excluded_vat_base = sum(r["positions_without_vat_base"] for r in bucket)
        kept = [r for r in bucket if r["volume"] is not None]
        totals = _totals_of(kept)
        totals["positions_without_standard"] = excluded_no_standard
        totals["positions_without_vat_base"] = excluded_vat_base
        totals["positions_without_volume"] = no_volume_by_class.get(class_id, 0)
        sections.append(
            {
                "rate_class_id": class_id,
                "rate_class_title": class_title,
                "rows": kept,
                "totals": totals,
            }
        )

    grand_totals = _totals_of([r for s in sections for r in s["rows"]]) | {
        "positions_without_standard": sum(
            s["totals"]["positions_without_standard"] for s in sections
        ),
        "positions_without_vat_base": sum(
            s["totals"]["positions_without_vat_base"] for s in sections
        ),
        "positions_without_volume": sum(
            s["totals"]["positions_without_volume"] for s in sections
        ),
        # Независимый общий счёт (см. свод): проверяемость разбиения по файлу.
        # Считает ВСЕ четыре класса разом — он не фильтрует ни по объёму, ни по
        # базе НДС, ни по нормативу, поэтому равенство с суммой четырёх напечатанных
        # счётчиков остаётся настоящим инвариантом, а не тавтологией.
        "positions_priced": db.execute(
            sa.select(sa.func.count()).select_from(
                DEVIATION_INPUTS.join(
                    latest_for_total,
                    latest_for_total.c.estimate_id == DEVIATION_INPUTS.c.estimate_id,
                )
            ).where(*scope)
        ).scalar_one(),
    }

    # Округление — САМЫЙ ПОСЛЕДНИЙ шаг, когда каждая сумма (по классу и по
    # выборке) уже посчитана из точных слагаемых (см. модульную документацию).
    for section in sections:
        for row in section["rows"]:
            _quantize_row_for_display(row)
        _quantize_totals_for_display(section["totals"])
    _quantize_totals_for_display(grand_totals)

    return {
        "header": _bank_header(db, latest, date_from, date_to, rate_class_id),
        "sections": sections,
        "totals": grand_totals,
    }


def _bank_header(
    db: Session,
    latest,
    date_from: dt.date | None,
    date_to: dt.date | None,
    rate_class_id: int | None,
) -> dict:
    """Состав выборки для шапки: сколько договоров, объектов и классов в отчёте."""
    row = db.execute(
        sa.select(
            sa.func.count(sa.distinct(Contract.id)).label("contracts"),
            sa.func.count(sa.distinct(Contract.object_id)).label("objects"),
            sa.func.count(sa.distinct(Contract.rate_class_id)).label("classes"),
        )
        .select_from(sa.join(latest, Contract, Contract.id == latest.c.contract_id))
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
        "date_from": iso(date_from),
        "date_to": iso(date_to),
        "contracts": row.contracts,
        "objects": row.objects,
        "classes": row.classes,
    }


# ---------------------------------------------------------------------------
#  Итоги
# ---------------------------------------------------------------------------

def _empty_report_totals() -> dict:
    """Итоги, когда сравнивать нечего.

    **Деньги здесь `None`, а не ноль** — найдено прогоном стенда. У класса, где ни у
    одной работы нет норматива, ноль в колонке «Отклонение, ₽» читается как «сошлось
    с нормативом», то есть утверждает прямо противоположное действительности. Это та
    же ошибка, которую §10 запрещает на уровне отдельной позиции, только в итоге —
    и в отчёте, который уходит в банк, она опаснее всего.

    `deviation_money` и `deviation_pct` обязаны быть пустыми **вместе**: разошлись бы
    они, и строка утверждала бы «отклонение 0 ₽ при неизвестном проценте».
    """
    return {
        "volume": None,
        "amount": None,
        "standard_amount": None,
        "deviation_money": None,
        "deviation_pct": None,
        "works": 0,
        "positions": 0,
        "comparable_positions": 0,
        "positions_without_standard": 0,
        "positions_without_volume": 0,
        "positions_priced": 0,
        "_positions_blocked_by_vat_base": 0,
    }


def _totals_of(rows: list[dict]) -> dict:
    """Итог по набору строк: деньги суммой, отклонение — по суммам.

    Отклонение НЕ усредняется из процентов строк (см. модульную документацию): здесь
    оно считается как `(факт − норматив) / норматив` по сравнимым суммам, то есть
    оказывается взвешенным по объёму автоматически.

    `comparable_positions` вычитает ОБА счётчика исключённого, которые может нести
    строка: `positions_without_standard` и `positions_without_vat_base` — оба
    поля теперь несут ОБЕ строки (Правка 2, ре-ревью финала ветки: у свода тоже
    бывает работа, чьё отклонение погашено неизвестной базой НДС, а не
    отсутствием норматива). `.get(..., 0)` защищает только от совсем старых
    строк без этого ключа — сейчас таких нет, но функция не должна падать,
    если он вдруг отсутствует.

    `_positions_blocked_by_vat_base` — служебный агрегат (ключ с подчёркиванием,
    НЕ печатается: `_write_excluded_counters` в `services/excel_reports.py`
    гейтуется по имени `positions_without_vat_base`, а не по этому). Он нужен
    только `_write_totals`, чтобы отличить у ИТОГОВОЙ строки причину пустого
    отклонения — «нет норматива» от «неизвестна база НДС» (Правка 2, пункт 3):
    без него обе причины схлопывались бы в одну подпись `NO_STANDARD`.
    """
    if not rows:
        return _empty_report_totals()

    amount = sum((r["amount"] for r in rows if r["amount"] is not None), ZERO)
    comparable = sum((r["comparable_amount"] for r in rows if r["comparable_amount"] is not None), ZERO)
    standard = sum(
        (r["comparable_standard_amount"] for r in rows if r["comparable_standard_amount"] is not None),
        ZERO,
    )
    # Сравнимой базы нет — значит нет ни процента, ни рублей отклонения. Ноль в
    # рублях при пустом проценте означал бы «сошлось» (см. `_empty_report_totals`).
    comparable_exists = standard != 0
    return {
        "volume": None,  # объёмы работ в разных единицах — суммировать их нельзя
        # Позиции, реально вошедшие в расчёт отклонения. Считается по строкам, а не
        # отдельным запросом: у отброшенных работ (без единого норматива либо без
        # известной базы НДС) сравнимых позиций нет по построению, поэтому сумма по
        # переданным строкам полна.
        "comparable_positions": sum(
            r["positions"] - r["positions_without_standard"] - r.get("positions_without_vat_base", 0)
            for r in rows
        ),
        "amount": amount,
        "standard_amount": standard if comparable_exists else None,
        "deviation_money": (comparable - standard) if comparable_exists else None,
        "deviation_pct": _deviation_pct(comparable, standard),
        "works": len(rows),
        "positions": sum(r["positions"] for r in rows),
        "positions_without_standard": sum(r["positions_without_standard"] for r in rows),
        "_positions_blocked_by_vat_base": sum(r.get("positions_without_vat_base", 0) for r in rows),
    }
