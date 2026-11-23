"""Поиск справочных строк договоров из панели строки КП (спека справочных аналогов §2.12, §2.20).

Модуль только читает. Поиск не строит сцену этапа: он грузит одну строку КП, договоры истории и
историю работ только семьи строки КП и связанных с ней семей, считает лестницу самой строки КП,
блоки А и Г и пометки выбросов теми же функциями, что панель, — поэтому пометка и пересчёт строки
договора в выдаче равны тем, что у неё в блоках панели.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError
from crud.lump_sum import typical_rates
from crud.not_analog import load_not_analog_marks
from crud.our_price import reference_lump_view, reference_row_view, search_context
from crud.our_price_inflation import plan_history_inflation, round_target_month
from crud.our_price_load import (
    KpLineRow,
    _zone_required_families,
    load_analog_details,
    load_history,
    load_kp_lines,
    load_work_history,
)
from crud.reference_lines import ReferenceLine, load_link_values, reference_lines_from_rows, reference_lines_query
from models import CatalogPosition, PositionItem
from parser.sanitize_text import NormalizationUnavailableError, normalize_job_title_with_lemmatization
from services.analog_ladder import WorkHistory
from services.context_routing import chapter_steps
from services.family_links import links_of_families
from services.our_price import HistoryContract, kp_work_of, work_ladder_of
from services.reference_blocks import (
    RefRow,
    conversion_for,
    fold_group_rows,
    link_groups,
    link_param_id,
    link_units,
    link_value_pairs,
    opens_zone_block,
    reference_marks,
    zone_groups,
)
from services.reference_math import price_in_kp_unit, units_equal

#: Предел выдачи — строк после свёртки (спека §2.12).
SEARCH_LIMIT = 50
CODE_SEARCH_QUERY_INVALID = "search_query_invalid"
CODE_REFERENCE_NOT_APPLICABLE = "reference_not_applicable"

_QUERY_MIN = 2
_QUERY_MAX = 100
_WORD_MIN = 2
_WORDS_MAX = 6
_ESCAPE = "\\"


def _invalid_query() -> DomainError:
    return DomainError(
        422,
        f"Запрос: от {_QUERY_MIN} до {_QUERY_MAX} знаков, от 1 до {_WORDS_MAX} слов не короче {_WORD_MIN} знаков.",
        code=CODE_SEARCH_QUERY_INVALID,
    )


def parse_query(q: str) -> tuple[str, ...]:
    """Слова запроса: текст после обрезки — 2..100 знаков, слова по пробелам, короче двух знаков
    отбрасываются, остаётся 1..6 слов.

    Raises:
        DomainError 422: `search_query_invalid`.
    """
    text = (q or "").strip()
    if not _QUERY_MIN <= len(text) <= _QUERY_MAX:
        raise _invalid_query()
    words = tuple(word for word in text.split() if len(word) >= _WORD_MIN)
    if not 1 <= len(words) <= _WORDS_MAX:
        raise _invalid_query()
    return words


def _escape_ilike(text: str) -> str:
    """Экранирует `%`, `_` и сам экранирующий символ перед подстановкой в `ILIKE`; сам символ — первым."""
    return text.replace(_ESCAPE, _ESCAPE * 2).replace("%", _ESCAPE + "%").replace("_", _ESCAPE + "_")


def _lemma(word: str) -> str | None:
    """Лемма слова; лемматизатор недоступен — `None` (ищем только по тексту)."""
    try:
        return normalize_job_title_with_lemmatization(word)
    except NormalizationUnavailableError:
        return None


def _word_conditions(words: Sequence[str]) -> list[sa.ColumnElement[bool]]:
    """По условию на слово: текст в наименовании строки ИЛИ лемма в нормализованном наименовании её
    каталожной позиции; строка подходит, когда проходит каждое."""
    conditions = []
    for word in words:
        alternatives = [PositionItem.job_title_in_proposal.ilike(f"%{_escape_ilike(word)}%", escape=_ESCAPE)]
        lemma = _lemma(word)
        if lemma:
            alternatives.append(
                CatalogPosition.normalized_job_title.ilike(f"%{_escape_ilike(lemma)}%", escape=_ESCAPE)
            )
        conditions.append(sa.or_(*alternatives))
    return conditions


def _find_row(
    db: Session, estimate_id: int, offer_id: int, pid: int, zone_required: frozenset[int]
) -> KpLineRow:
    found = load_kp_lines(db, [estimate_id], only_position_item_id=pid, zone_required=zone_required)[estimate_id]
    if not found:
        raise DomainError(404, f"Строка {pid} не найдена в КП оффера {offer_id}.")
    (row,) = found
    if row.line.is_system:
        raise DomainError(
            422,
            "Справочный поиск доступен у строки-работы; у системы справочных строк нет.",
            code=CODE_REFERENCE_NOT_APPLICABLE,
        )
    if row.line.is_lump:  # состояние `lump_sum`: лестницы нет, справочной цели не будет (спека строк-сумм §6 п. 1)
        raise DomainError(
            422,
            "Строка КП похожа на комплект: справочного поиска у неё нет.",
            code=CODE_REFERENCE_NOT_APPLICABLE,
        )
    return row


def _lump_key(line: ReferenceLine) -> tuple[bool, int | None] | None:
    """Составляющая ключа свёртки поиска: итог правила и, у строки с поправкой, её id — такая
    строка остаётся отдельной, чтобы снять поправку можно было с неё самой."""
    if line.lump is None:
        return None
    return line.lump.is_lump, None if line.lump.override is None else line.position_item_id


def search_reference(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int,
    pid: int,
    *,
    q: str,
    units: Literal["all", "kp"],
    inflation_series_id: int | None,
) -> dict:
    """Выдача поиска `{rows, total_lines, truncated}` по строке КП `pid` оффера `offer_id`.

    Строка выдачи — строка блока А панели (`reference_row_view`) с зоной или `null`, плюс
    `conversion_kind`: `none` (единицы равны), `link` (пересчёт по связи, `conversion` несёт `N`,
    основание и связь) или `manual_needed` (пересчёт нужен, `N` вводит человек). Порядок: единица
    строки КП, её зона, близость места, цена в своей единице, `position_item_id`; строки после
    свёртки, не больше `SEARCH_LIMIT`; `total_lines` — строк до свёртки.

    Raises:
        DomainError 404: тендера, этапа, оффера, сметы КП или строки этого КП нет, нет ряда ИПЦ.
        DomainError 422: `search_query_invalid`, `reference_not_applicable` (система), отказы
            приведения по ИПЦ, как у панели.
    """
    words = parse_query(q)
    context = search_context(db, tender_id, round_id, offer_id)
    head, participant = context.head, context.participant
    zone_required = _zone_required_families(db)
    row = _find_row(db, context.estimate_id, offer_id, pid, zone_required)
    kp_family = None if row.line.key is None else row.line.key.family_id

    contracts, _history_lines = load_history(db, exclude_object_id=head.object_id)
    target_month = round_target_month(db, head.round_id)
    factors = None
    if inflation_series_id is not None:
        plan = plan_history_inflation(
            db, [c.estimate_id for c in contracts], series_id=inflation_series_id, target_month=target_month
        )
        factors = plan.factor_by_estimate

    states = tuple(links_of_families(db, [] if kp_family is None else [kp_family]))
    family_ids = {family for state in states if state.active for family in (state.link.family_lo_id, state.link.family_hi_id)}
    if kp_family is not None:
        family_ids.add(kp_family)
    work_lines, families = load_work_history(
        db, estimate_of={c.contract_id: c.estimate_id for c in contracts}, family_ids=family_ids
    )
    contract_of = {c.contract_id: c for c in contracts}
    history = WorkHistory(work_lines, families, contract_of, factors=factors, zone_required=zone_required)

    lines = _find_lines(db, contracts, words, row, units, factors)

    not_analog = {key: frozenset(marks) for key, marks in load_not_analog_marks(db, head.tender_id).items()}
    excluded = not_analog.get(row.line.form_key, frozenset())
    kp_net, ladder = work_ladder_of(
        row.line,
        kp_vat_base=participant.base,
        work_history=history,
        tender_class_id=head.rate_class_id,
        tender_contractor_id=participant.contractor_id,
        target_month=target_month,
        not_analog=not_analog,
    )
    kp = None if ladder is None or kp_net is None else kp_work_of(row.line, kp_net, excluded=excluded)
    blocks_open = kp is not None and ladder is not None and ladder.rung_no is None

    pairs: set[tuple[int, int]] = set()
    for line in lines.values():
        param_id = link_param_id(states, line.family_id, kp_family)
        if param_id is not None:
            pairs.add((line.position_item_id, param_id))
    if blocks_open:
        assert kp is not None
        pairs |= link_value_pairs(kp, history, states)
    values = load_link_values(db, pairs)
    link_unit_symbols = link_units(states)

    zones = ()
    links = ()
    if kp is not None and ladder is not None:
        if opens_zone_block(ladder):
            zones = zone_groups(
                kp,
                history,
                tender_class_id=head.rate_class_id,
                tender_contractor_id=participant.contractor_id,
                target_month=target_month,
            )
        if blocks_open and any(state.active for state in states):
            links = link_groups(
                kp,
                row.unit_id,
                history,
                states,
                link_unit_symbols,
                values,
                tender_class_id=head.rate_class_id,
                target_month=target_month,
            )
    marks = reference_marks(ladder, zones, links)

    built: list[tuple[ReferenceLine, RefRow, str]] = []
    for line in lines.values():
        conversion, reason, needed = conversion_for(
            contract_unit_id=line.unit_id,
            kp_unit_id=row.unit_id,
            contract_family_id=line.family_id,
            kp_family_id=kp_family,
            links=states,
            units=link_unit_symbols,
            values=values,
            position_item_id=line.position_item_id,
        )
        if conversion is not None:
            kp_unit_net: Decimal | None = price_in_kp_unit(line.net, conversion.factor)
            kind = "link"
        else:
            kp_unit_net = None if needed else line.net
            kind = "manual_needed" if needed else "none"
        param_id = link_param_id(states, line.family_id, kp_family)
        text = None if param_id is None else values.get((line.position_item_id, param_id))
        ref = RefRow(
            position_item_id=line.position_item_id,
            contract_id=line.contract_id,
            zone=line.zone,
            unit_id=line.unit_id,
            nominal=line.nominal,
            net=line.net,
            conversion=conversion,
            no_conversion_reason=reason,
            kp_unit_net=kp_unit_net,
            outlier_ratio=marks.get(line.position_item_id),
            param_value=None if text is None else text.text,
            family_id=line.family_id,
        )
        built.append((line, ref, kind))

    def order(item: tuple[ReferenceLine, RefRow, str]) -> tuple:
        line, _ref, _kind = item
        same_unit = units_equal(line.unit_id, row.unit_id)
        same_zone = line.zone is not None and line.zone == row.line.zone
        return (
            not same_unit,
            not same_zone,
            -len(row.line.chain_titles & line.chain_titles),
            line.net,
            line.position_item_id,
        )

    built.sort(key=order)
    kind_of = {ref.position_item_id: kind for _line, ref, kind in built}
    titles = {line.position_item_id: line.title for line, _ref, _kind in built}
    # строка с поправкой не сворачивается ни с кем: меню поправки работает с представителем
    decision = {line.position_item_id: _lump_key(line) for line, _ref, _kind in built}
    folded = fold_group_rows([ref for _line, ref, _kind in built], titles, decision)
    shown = folded[:SEARCH_LIMIT]

    line_of = {line.position_item_id: line for line, _ref, _kind in built}
    unit_labels = typical_rates(db).unit_labels
    details = load_analog_details(db, [pid_ for _row, ids in shown for pid_ in ids])
    chapter_ids = {details[pid_][4] for _row, ids in shown for pid_ in ids} - {None}
    steps = chapter_steps(db, chapter_ids)
    rows = []
    for ref, ids in shown:
        view = reference_row_view(
            ref,
            ids,
            contract=contract_of[ref.contract_id],
            participant=participant,
            kp_unit_net=kp_net,
            kp_unit_id=row.unit_id,
            details=details,
            steps=steps,
            lump=reference_lump_view(line_of[ref.position_item_id], participant.base, unit_labels),
        )
        view["conversion_kind"] = kind_of[ref.position_item_id]
        rows.append(view)
    return {"rows": rows, "total_lines": len(built), "truncated": len(folded) > SEARCH_LIMIT}


def _find_lines(
    db: Session,
    contracts: Sequence[HistoryContract],
    words: Sequence[str],
    row: KpLineRow,
    units: Literal["all", "kp"],
    factors: dict[int, Decimal] | None,
) -> dict[int, ReferenceLine]:
    """Справочные строки (§2.3) договоров истории, в которых находится каждое слово запроса."""
    if not contracts:
        return {}
    statement = reference_lines_query(contracts).where(*_word_conditions(words))
    if units == "kp":
        statement = statement.where(
            sa.false() if row.unit_id is None else CatalogPosition.unit_id == row.unit_id
        )
    return reference_lines_from_rows(db, db.execute(statement).all(), factors)

