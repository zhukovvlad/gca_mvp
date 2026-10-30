"""Строки файла подрядчику и шапка листа (спека 4б §2.8).

Чистый расчёт без базы: из строк сметы КП и оценки ядра строит строки файла;
из них собираются и предпросмотр, и книга xlsx — предпросмотр равен файлу по
построению. Цель, источник и случай комментария берутся из `services.kp_comment`,
здесь они только раскладываются по столбцам. Деньги — `Decimal`; «Наша расценка»
и цена за единицу не округляются (округляет формат ячейки), округление до копеек
стоит только в сравнении отклонения — по тем же значениям, что и в случае
комментария.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
from typing import TYPE_CHECKING, Literal

from money.inflation import YearMonth, parse_target_month, year_exponents
from money.price import is_price, is_weight
from money.vat import gross_to_net, net_to_gross
from parser.summary_block import ARITHMETIC_PRECISION
from responses import safe_filename_part
from services.excel import PRICE_LEVEL_NOMINAL, safe_str
from services.kp_comment import (
    CommentInputs,
    InflationLabel,
    SourceContract,
    pick_source,
    rub_in_base,
    target_net,
    target_sum_net,
    template_text,
    vat_words,
)
from services.our_price import HistoryIndex, LineResult, LineState, OfferEvaluation

if TYPE_CHECKING:
    from crud.comparison import InflationPlan

#: Промежуточных округлений нет; ловушки включены, чтобы деление на ноль не прошло молча.
_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)
_ONE_DIGIT = Decimal("0.1")
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class KpSourceLine:
    """Строка сметы КП в порядке файла."""

    position_item_id: int
    is_chapter: bool
    #: Система, а не работа (`KpLine.is_system`); решает форму цели и комментария.
    is_system: bool
    #: `item_number_in_proposal`.
    number: str | None
    title: str
    #: Текст единицы: из `estimate_raw_data`, иначе имя единицы, иначе пусто.
    unit_text: str
    #: Вес строки.
    quantity: Decimal | None
    #: Как в КП: валовая цена в базе НДС КП.
    unit_price: Decimal | None
    total: Decimal | None


@dataclass(frozen=True)
class FileSection:
    number: str | None
    title: str


@dataclass(frozen=True)
class FileLine:
    position_item_id: int
    number: str | None
    title: str
    unit: str
    quantity: Decimal | None
    kp_unit_price: Decimal | None
    kp_total: Decimal | None
    #: «Наша расценка за ед.» в базе НДС КП.
    ours: Decimal | None
    #: Один знак после запятой.
    deviation_pct: Decimal | None
    source: str | None
    inflation: str | None
    comment: str | None
    comment_kind: Literal["template", "custom"] | None
    state: str
    #: Договор, чья цена названа в столбце «Приведение по ИПЦ». Это не ячейка:
    #: нужен шапке, чтобы перечислить годы коэффициентов; предпросмотр
    #: собирает строки по явному списку ключей и его не отдаёт.
    inflation_source: SourceContract | None = None


@dataclass(frozen=True)
class FileHeader:
    tender: str
    object: str
    round: str
    contractor: str
    #: `ДД.ММ.ГГГГ`.
    exported_on: str
    vat_label: str
    source_label: str
    level_label: str
    in_progress_lines: int
    #: Год, коэффициент, источник, признак прогноза, дата правки; `None` — без ряда.
    inflation_years: list[dict] | None
    #: Имя ряда и целевой месяц `ГГГГ-ММ` для блока приведения; `None` — без ряда.
    inflation_series: str | None
    inflation_target_month: str | None
    filename: str


@dataclass(frozen=True)
class FileContext:
    """Что строкам и шапке нужно от сцены."""

    #: База НДС КП; КП с неизвестной базой отсекается раньше.
    kp_vat_base: Decimal
    history: HistoryIndex
    tender_class_id: int
    tender_area: Decimal | None
    #: Подрядчик участника.
    tender_contractor_id: int
    #: Имя ряда и целевой месяц; `None` — без ряда.
    inflation: InflationLabel | None
    inflation_plan: InflationPlan | None
    #: Ценовой период сметы договора по `contract_id`.
    contract_periods: Mapping[int, YearMonth]
    tender_title: str
    object_title: str
    stage_number: int
    stage_date: date | None
    contractor_title: str
    exported_on: date


# --- вход комментария ---------------------------------------------------------------------


def _net(value: Decimal | None, base: Decimal) -> Decimal | None:
    if value is None or not value.is_finite():
        return None
    return gross_to_net(value, base)


def line_comment_inputs(
    line: KpSourceLine,
    result: LineResult,
    *,
    history: HistoryIndex,
    tender_class_id: int,
    tender_contractor_id: int,
    tender_area: Decimal | None,
    kp_vat_base: Decimal,
    inflation: InflationLabel | None,
    agreed_net: Decimal | None,
) -> CommentInputs:
    """Вход комментария строки: единственное место, где он собирается.

    Файл, строки разбора и панель зовут эту функцию, поэтому случай комментария
    везде один. Система или работа — по строке сметы.
    """
    return CommentInputs(
        result=result,
        is_system=line.is_system,
        unit_text=line.unit_text,
        kp_unit_net=_net(line.unit_price, kp_vat_base),
        kp_total_net=_net(line.total, kp_vat_base),
        quantity=line.quantity,
        agreed_net=agreed_net,
        level=result.rung.level if result.rung is not None else None,
        source=pick_source(
            result,
            history,
            tender_class_id=tender_class_id,
            tender_contractor_id=tender_contractor_id,
        ),
        tender_contractor_id=tender_contractor_id,
        tender_area=tender_area,
        inflation=inflation,
        vat_base=kp_vat_base,
    )


# --- отклонение ---------------------------------------------------------------------------


def _percent_over(kp: Decimal | None, target: Decimal | None) -> Decimal | None:
    """`(КП / цель - 1) * 100` по исходным нетто-значениям; округляется готовый процент
    (один знак, половина вверх). Копеечное округление цен здесь не участвует: оно живёт только
    в выборе случая комментария (спека 4б §2.8)."""
    if not is_price(kp) or not is_price(target):
        return None
    with localcontext(_CONTEXT):
        percent = ((kp / target - 1) * _HUNDRED).quantize(_ONE_DIGIT, rounding=ROUND_HALF_UP)
    return abs(percent) if percent.is_zero() else percent


def deviation_pct(inp: CommentInputs) -> Decimal | None:
    """Отклонение от того числа, что стоит в «Нашей расценке» (спека 4б §2.8, таблица)."""
    if inp.agreed_net is not None:
        return _percent_over(inp.kp_unit_net, inp.agreed_net)
    if inp.is_system:
        return _percent_over(inp.kp_total_net, target_sum_net(inp))
    return _percent_over(inp.kp_unit_net, target_net(inp))


# --- тексты столбцов ----------------------------------------------------------------------


def _source_text(inp: CommentInputs) -> str:
    """«Источник» строки без договорённости; у строки есть сравнение, значит, есть договор."""
    src = inp.source
    head = f"{src.contract.object_title}, договор № {src.contract.number}, {src.contract.year} г.: "
    nominal = rub_in_base(inp, src.rate.nominal)
    vat = vat_words(inp.vat_base)
    if not inp.is_system:
        average = "в среднем " if src.rate.lines >= 2 else ""
        return f"{head}{average}{nominal} ₽ за {inp.unit_text} {vat}"
    text = f"{head}{nominal} ₽ на м² объекта {vat}"
    if not is_weight(inp.quantity):
        with localcontext(_CONTEXT):
            nominal_sum = src.rate.nominal * inp.tender_area * inp.result.share
        text += f" — для вашего объекта {rub_in_base(inp, nominal_sum)} ₽ за весь объём"
    return text


def _inflation_text(inp: CommentInputs, label: InflationLabel) -> str:
    if inp.is_system and not is_weight(inp.quantity):
        amount = f"{rub_in_base(inp, target_sum_net(inp))} ₽ за весь объём"
    else:
        amount = f"{rub_in_base(inp, target_net(inp))} ₽ за {inp.unit_text}"
    return f"{label.series_name}, к {label.month}: {amount} {vat_words(inp.vat_base)}"


def _file_line(
    src: KpSourceLine,
    result: LineResult,
    *,
    comments: Mapping[int, str],
    agreements: Mapping[int, tuple[Decimal, str]],
    context: FileContext,
) -> FileLine:
    agreed = agreements.get(src.position_item_id)
    inp = line_comment_inputs(
        src,
        result,
        history=context.history,
        tender_class_id=context.tender_class_id,
        tender_contractor_id=context.tender_contractor_id,
        tender_area=context.tender_area,
        kp_vat_base=context.kp_vat_base,
        inflation=context.inflation,
        agreed_net=agreed[0] if agreed is not None else None,
    )
    target = target_net(inp)

    source: str | None = None
    inflation: str | None = None
    inflation_source: SourceContract | None = None
    if agreed is not None:
        source = f"Договорились {agreed[1]}"
    elif result.state is LineState.comparable:
        source = _source_text(inp)
        if context.inflation is not None and inp.source.rate.net != inp.source.rate.nominal:
            inflation = _inflation_text(inp, context.inflation)
            inflation_source = inp.source

    custom = comments.get(src.position_item_id)
    if custom is not None:
        comment: str | None = custom or None
        comment_kind: Literal["template", "custom"] | None = "custom"
    else:
        comment = template_text(inp)
        comment_kind = "template" if comment is not None else None

    return FileLine(
        position_item_id=src.position_item_id,
        number=safe_str(src.number),
        title=safe_str(src.title),
        unit=safe_str(src.unit_text),
        quantity=src.quantity,
        kp_unit_price=src.unit_price,
        kp_total=src.total,
        ours=net_to_gross(target, context.kp_vat_base) if target is not None else None,
        deviation_pct=deviation_pct(inp),
        source=safe_str(source),
        inflation=safe_str(inflation),
        comment=safe_str(comment),
        comment_kind=comment_kind,
        state=result.state.value,
        inflation_source=inflation_source,
    )


def build_file_rows(
    source_lines: Sequence[KpSourceLine],
    evaluation: OfferEvaluation,
    *,
    comments: Mapping[int, str],
    agreements: Mapping[int, tuple[Decimal, str]],
    context: FileContext,
) -> list[FileSection | FileLine]:
    """Строки файла в порядке входа; `agreements` — нетто и уже напечатанная дата `ДД.ММ.ГГГГ`."""
    rows: list[FileSection | FileLine] = []
    for src in source_lines:
        if src.is_chapter:
            rows.append(FileSection(number=safe_str(src.number), title=safe_str(src.title)))
        else:
            rows.append(
                _file_line(
                    src,
                    evaluation.lines[src.position_item_id],
                    comments=comments,
                    agreements=agreements,
                    context=context,
                )
            )
    return rows


# --- шапка --------------------------------------------------------------------------------


def inflation_years_for(
    sources: Iterable[SourceContract],
    plan: InflationPlan,
    periods: Mapping[int, YearMonth],
) -> list[dict]:
    """Годы коэффициентов договоров-источников файла, по возрастанию года.

    Набор лет — объединение годов `year_exponents` периода сметы каждого договора
    к целевому месяцу плана; факты лет — из `plan.used_years`, того же прочтения
    ряда, которым посчитаны множители.
    """
    target = parse_target_month(plan.target_month)
    needed: set[int] = set()
    for source in sources:
        needed.update(year_exponents(periods[source.contract.contract_id], target))
    return [dict(fact) for fact in sorted(plan.used_years, key=lambda f: f["year"]) if fact["year"] in needed]


def _vat_label(base: Decimal) -> str:
    if base == 0:
        return "Цены КП и наши расценки — без НДС (база НДС этого КП — 0 %)"
    return f"Цены КП и наши расценки — {vat_words(base)} (база НДС этого КП)"


def _source_label(base: Decimal) -> str:
    if base == 0:
        return "Цены договоров — без НДС (база НДС этого КП — 0 %)"
    return f"Цены договоров пересчитаны в базу НДС этого КП — {vat_words(base)}"


def build_header(context: FileContext, rows: Sequence[FileSection | FileLine]) -> FileHeader:
    lines = [row for row in rows if isinstance(row, FileLine)]
    plan = context.inflation_plan
    years = None
    if plan is not None:
        sources = [row.inflation_source for row in lines if row.inflation_source is not None]
        years = inflation_years_for(sources, plan, context.contract_periods)

    if context.inflation is None:
        level_label = PRICE_LEVEL_NOMINAL
    else:
        level_label = (
            f"приведено по ИПЦ: {context.inflation.series_name}, к {context.inflation.month}; КП не приводится"
        )

    stage = f"Этап {context.stage_number}"
    if context.stage_date is not None:
        stage += f" от {context.stage_date:%d.%m.%Y}"
    contractor = safe_filename_part(context.contractor_title, fallback="подрядчик")
    return FileHeader(
        tender=context.tender_title,
        object=context.object_title,
        round=stage,
        contractor=context.contractor_title,
        exported_on=f"{context.exported_on:%d.%m.%Y}",
        vat_label=_vat_label(context.kp_vat_base),
        source_label=_source_label(context.kp_vat_base),
        level_label=level_label,
        in_progress_lines=sum(1 for row in lines if row.state == LineState.in_progress.value),
        inflation_years=years,
        inflation_series=plan.series_name if plan is not None else None,
        inflation_target_month=plan.target_month if plan is not None else None,
        filename=f"КП {contractor} этап {context.stage_number} — наши расценки {context.exported_on:%Y-%m-%d}.xlsx",
    )
