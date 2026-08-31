"""Приёмка: сходимость раскрытия по всем узлам обеих трасс стенда (DoD 2,
задача 7 плана фичи «попозиционное раскрытие»; ревизия — находка 5 внешнего
ревью PR #35).

Не CI-тест — ходит в живой `gca_dev`, только читает. Для КАЖДОГО тендера
стенда и его трассы АНТТЕК (этапы 1-4):

  1. считается КОРПУСНАЯ популяция — множество id категорий с НЕПУСТЫМ
     поддеревом строк (позиций и/или допработ) в выбранных сметах
     (`nonempty_subtree_category_ids`). Это ДОСЛОВНО то, что просит DoD 2
     спеки: «Прогон по всем узлам с непустым поддеревом обеих трасс стенда
     (113 и 189, блок замеров): сходимость в каждой колонке» — БЕЗ оглядки на
     то, показывает ли свод строку/шеврон для узла;
  2. строится свод (`build_stage_summary`) на тех же предложениях — только
     чтобы для строк, которые он ПОКАЗЫВАЕТ (`has_drilldown_rows == true`),
     сверить `article_amount` разложения с `cells[i].amount` строки свода
     (третья проверка §2.13, доступная только там, где есть с чем сверять);
  3. для КАЖДОГО узла корпусной популяции вызывается `build_position_drilldown`
     и проверяются (§2.13): `reason is None`, каждая колонка `converged is
     True`; для узлов, у которых есть строка свода, — ДОПОЛНИТЕЛЬНО
     `article_amount` колонки разложения равен `cells[i].amount` той же
     колонки строки свода.

До ревизии (находка 5) скрипт ходил ТОЛЬКО по строкам свода с
`has_drilldown_rows` — 108 и 166 узлов. Это меньше корпусной популяции: свод
ПРЯЧЕТ (правило §2.14, `services/stage_summary.py::_row`) любой вложенный
узел, чья валовая сумма поддерева равна `None`/0 в КАЖДОЙ из четырёх колонок,
даже если в поддереве реально лежат строки позиций/допработ (они просто
гасят друг друга или изначально нулевые). Такой узел никогда не станет
строкой свода — ни строки, ни шеврона, чтобы вызвать по нему разложение из
интерфейса — но ЭНДПОИНТ адресуется `work_category_id` напрямую и его можно
и нужно проверить. Разница — РОВНО 5 узлов (тендер 2) и 23 узла (тендер 3);
диффом множеств id подтверждено, что все они имеют нулевую валовую сумму
поддерева на всех 4 этапах, необъяснённого остатка нет (задача 7, отчёт).

Обе популяции печатаются и пинуются числом (`EXPECTED_CORPUS`,
`EXPECTED_UI_REACHABLE`), а не просто «список непуст» — по той же причине,
что и раньше: гейт без нижней границы пропускает вырожденный прогон (сломанный
обход классификатора или дерева свода даёт 0 и молчаливое `RESULT: OK`).
Расхождение с любым из двух пиннингов — как и расхождение по сходимости —
печатается, и скрипт завершается ненулевым кодом; числа не подгоняются под
скрипт.

Запуск (из `backend/`):

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/check_drilldown_convergence.py

Вывод — только ASCII (числа, id, True/False): наименования статей и работ на
экран не идут, чтобы не упереться в мангл кириллицы этого терминала.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

# Скрипт запускается из `backend/` как `python scripts/...`, а это кладёт в
# sys.path каталог скрипта, а не корень пакета — тот же приём, что в
# `gen_position_drilldown_inline.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import Session, aliased  # noqa: E402

from crud.common import DomainError  # noqa: E402
from crud.position_drilldown import build_position_drilldown  # noqa: E402
from crud.stage_summary import build_stage_summary  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    Contractor,
    Estimate,
    EstimateAdditionalWork,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    TenderRound,
    WorkCategory,
)

#: Стенд: тендер 2 "Генподряд" (159-ТУ) и тендер 3 "Cityzen Tr. 1 UB8b"
#: (449-ТУ) — id проверены запросом к `gca_dev`, не выдуманы. Трасса на
#: обоих — участник АНТТЕК, все четыре этапа.
TENDER_IDS = (2, 3)
#: ООО «АНТТЕК» — ИНН, а не название: в терминале печатать кириллицу нельзя,
#: а по ИНН участник ищется однозначно (contractors.inn уникален).
CONTRACTOR_INN = "7701380579"

#: Корпусная популяция (DoD 2, блок замеров спеки §2.13) — ПОКОЛОНОЧНО, не
#: просто «не ноль». Число узлов классификатора с непустым поддеревом строк
#: (позиций и/или допработ) в выбранных сметах трассы — считается
#: `nonempty_subtree_category_ids`, независимо от дерева свода/правила §2.14.
EXPECTED_CORPUS = {2: 113, 3: 189}
#: Подмножество корпуса, которое реально ДОХОДИТ до интерфейса — строки свода
#: с `has_drilldown_rows == true` (то, что раньше было единственной проверяемой
#: популяцией). Печатается и пинуется ОТДЕЛЬНО: различие реально и осмысленно
#: (см. докстроку модуля) и не должно стираться общим числом.
EXPECTED_UI_REACHABLE = {2: 108, 3: 166}


def offers_of(db: Session, tender_id: int, inn: str) -> list[int]:
    """Offer id участника в тендере по этапам, по возрастанию stage_no.

    По образцу `offers_of` из `tests/integration/test_stage_summary_api.py`.
    """
    return list(
        db.execute(
            sa.select(Offer.id)
            .join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender_id, Contractor.inn == inn)
            .order_by(TenderRound.stage_no)
        ).scalars().all()
    )


def estimates_of(db: Session, offer_ids: list[int]) -> list[int]:
    """Estimate id по тем же offer_ids — единственная привязка нужна для
    корпусного замера (`nonempty_subtree_category_ids`); порядок здесь не
    важен, это множество смет, не колонки ответа."""
    return list(
        db.execute(sa.select(Estimate.id).where(Estimate.offer_id.in_(offer_ids))).scalars().all()
    )


def nonempty_subtree_category_ids(db: Session, estimate_ids: list[int]) -> set[int]:
    """Множество id категорий с НЕПУСТЫМ поддеревом строк (позиций и/или
    допработ) среди заданных смет — тот же корпусный счёт (113/189), что даёт
    `corpus_stats()` из `gen_position_drilldown_inline.py`, но посчитанный
    напрямую, БЕЗ обхода дерева свода.

    Категория входит в результат, если ОНА САМА или любой из её потомков несёт
    хотя бы одну строку — позицию, чья ГЛАВА привязана к этой категории
    (`work_category_id` главы), либо допработу с таким же `work_category_id` —
    в одной из выбранных смет. В дереве классификатора «поддерево X непусто»
    эквивалентно «X — предок-или-сама одной из ЗАНЯТЫХ категорий», поэтому
    результат строится как объединение цепочек предков от каждой занятой
    категории до корня, а не обходом `subtree_ids` по каждому кандидату
    отдельно (то же самое множество, дешевле считать).

    Определение НЕ зависит от дерева свода: узел с нулевой валовой суммой во
    всех колонках сюда всё равно попадает, если в его поддереве есть хотя бы
    одна строка (правило §2.14 прячет такой узел от свода, но не от
    эндпоинта, который адресуется `work_category_id` напрямую — находка 5
    внешнего ревью PR #35)."""
    chapter = aliased(PositionItem)
    occupied: set[int] = set(db.execute(
        sa.select(sa.distinct(chapter.work_category_id))
        .select_from(PositionItem)
        .join(chapter, sa.and_(chapter.id == PositionItem.chapter_item_id,
                               chapter.proposal_id == PositionItem.proposal_id))
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids), PositionItem.is_chapter.is_(False),
               chapter.work_category_id.isnot(None))
    ).scalars().all())
    occupied |= set(db.execute(
        sa.select(sa.distinct(EstimateAdditionalWork.work_category_id))
        .select_from(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids),
               EstimateAdditionalWork.work_category_id.isnot(None))
    ).scalars().all())

    parent_of: dict[int, int | None] = dict(
        db.execute(sa.select(WorkCategory.id, WorkCategory.parent_id)).all()
    )
    result: set[int] = set()
    for cid in occupied:
        current: int | None = cid
        while current is not None and current not in result:
            result.add(current)
            current = parent_of.get(current)
    return result


def money_to_decimal(value: str | None) -> Decimal:
    """`None` и `"0.00"` — одна и та же сумма для сходимости (§2.13 брифа
    задачи): обе стороны сравнения проходят через эту же функцию, так что
    случай "0.00 против null" не проваливается сквозь голое `==` строк —
    он ЯВНО схлопывается в равные `Decimal`, а не проверяется отдельной
    веткой."""
    return Decimal(value) if value is not None else Decimal(0)


def collect_drilldown_rows(rows: list[dict]) -> list[dict]:
    """Рекурсивно (с детьми) — все строки свода с `has_drilldown_rows`."""
    out: list[dict] = []
    for row in rows:
        if row["has_drilldown_rows"]:
            out.append(row)
        out.extend(collect_drilldown_rows(row["children"]))
    return out


@dataclass
class Mismatch:
    tender_id: int
    work_category_id: int
    detail: str


def check_node(tender_id: int, offer_ids: list[int], wc_id: int, columns: list[dict],
                row: dict | None, db: Session, mismatches: list[Mismatch]) -> tuple[bool, int]:
    """Один узел корпусной популяции -> `(сошлась ли по применимым проверкам
    §2.13, сколько пар "null против нуля" в ней встретилось)`; при
    расхождении дописывает `mismatches`.

    `row` — строка свода (с `cells`), если узел ПОКАЗАН сводом
    (`has_drilldown_rows`); `None` — если узел есть только в корпусе (правило
    §2.14 спрятало его от дерева). В обоих случаях проверяются `reason is
    None` и `converged is True` в каждой колонке; сверка `article_amount` со
    строкой свода возможна ТОЛЬКО когда `row` есть — сравнивать её не с чем
    для спрятанных узлов, это не смягчение проверки, а честное сужение того,
    что вообще можно сверить без второго независимого источника.

    Вызов `build_position_drilldown` обёрнут: по построению (id взяты из
    справочника категорий той же выборки того же тендера) он не должен
    отказывать, но если бы отказал — `DomainError.detail` человеческий текст,
    часто кириллица, а её нельзя пускать в стектрейс этого терминала (задача
    7, второй круг ревью). Отказ печатается ASCII-полями исключения
    (`status_code`, `code`), без `detail`, и учитывается как расхождение, а не
    падение всего прогона — так дальше проверяются оставшиеся узлы.
    """
    try:
        drill = build_position_drilldown(db, tender_id, wc_id, offer_ids)
    except DomainError as exc:
        mismatches.append(Mismatch(
            tender_id, wc_id,
            f"build_position_drilldown raised DomainError status_code={exc.status_code} code={exc.code!r}",
        ))
        return False, 0
    except Exception as exc:
        mismatches.append(Mismatch(
            tender_id, wc_id,
            f"build_position_drilldown raised {type(exc).__name__}",
        ))
        return False, 0

    ok = True
    null_zero_pairs = 0

    if drill["reason"] is not None:
        ok = False
        mismatches.append(Mismatch(tender_id, wc_id, f"top-level reason={drill['reason']!r}"))

    conv_by_stage = {c["stage_no"]: c for c in drill["convergence"]}
    cell_by_stage = (
        {col["stage_no"]: cell for col, cell in zip(columns, row["cells"], strict=True)}
        if row is not None else None
    )

    if cell_by_stage is not None and set(conv_by_stage) != set(cell_by_stage):
        ok = False
        mismatches.append(Mismatch(
            tender_id, wc_id,
            f"stage sets differ: convergence={sorted(conv_by_stage)} cells={sorted(cell_by_stage)}",
        ))
        return ok, null_zero_pairs

    for stage_no, conv in conv_by_stage.items():
        if conv["converged"] is not True:
            ok = False
            mismatches.append(Mismatch(
                tender_id, wc_id,
                f"stage {stage_no}: converged={conv['converged']!r} reason={conv['reason']!r}",
            ))
        if cell_by_stage is None:
            continue   # узел спрятан сводом — сверить article_amount не с чем
        drill_raw, cell_raw = conv["article_amount"], cell_by_stage[stage_no]["amount"]
        drill_amount = money_to_decimal(drill_raw)
        cell_amount = money_to_decimal(cell_raw)
        # Задача 7, второй круг ревью: сама сходимость `None`/"0.00" уже
        # гарантирована тем, что обе стороны идут через `money_to_decimal` —
        # но это единственное место, где видно, СКОЛЬКО раз этот случай
        # вообще встретился на реальных данных, а не только в теории функции.
        if (drill_raw is None) != (cell_raw is None) and drill_amount == cell_amount == 0:
            null_zero_pairs += 1
        if drill_amount != cell_amount:
            ok = False
            mismatches.append(Mismatch(
                tender_id, wc_id,
                f"stage {stage_no}: article_amount={drill_raw!r} != cell.amount={cell_raw!r}",
            ))
    return ok, null_zero_pairs


def check_tender(db: Session, tender_id: int, mismatches: list[Mismatch]) -> tuple[int, int, int, int]:
    offer_ids = offers_of(db, tender_id, CONTRACTOR_INN)
    if len(offer_ids) != 4:
        raise SystemExit(
            f"tender {tender_id}: expected 4 ANTTEK offers (stages 1-4), "
            f"got {len(offer_ids)}: {offer_ids}"
        )
    estimate_ids = estimates_of(db, offer_ids)

    summary = build_stage_summary(db, tender_id, offer_ids)
    columns = summary["columns"]
    reachable_rows = {row["work_category_id"]: row for row in collect_drilldown_rows(summary["rows"])}
    corpus_ids = nonempty_subtree_category_ids(db, estimate_ids)

    checked = 0
    reachable_checked = 0
    converged = 0
    null_zero_pairs = 0
    for wc_id in sorted(corpus_ids):
        checked += 1
        row = reachable_rows.get(wc_id)
        if row is not None:
            reachable_checked += 1
        node_ok, node_null_zero = check_node(tender_id, offer_ids, wc_id, columns, row, db, mismatches)
        if node_ok:
            converged += 1
        null_zero_pairs += node_null_zero

    return checked, reachable_checked, converged, null_zero_pairs


def main() -> int:
    db = SessionLocal()
    try:
        overall_ok = True
        mismatches: list[Mismatch] = []
        for tender_id in TENDER_IDS:
            checked, reachable_checked, converged, null_zero_pairs = check_tender(db, tender_id, mismatches)
            expected_corpus = EXPECTED_CORPUS.get(tender_id)
            expected_reachable = EXPECTED_UI_REACHABLE.get(tender_id)
            print(f"tender {tender_id}: nodes checked={checked} ui_reachable={reachable_checked} "
                  f"converged={converged}/{checked} expected_corpus={expected_corpus} "
                  f"expected_ui_reachable={expected_reachable} null_zero_pairs={null_zero_pairs}")
            if converged != checked:
                overall_ok = False
            # Нижняя граница гейта (задача 7, второй круг ревью; сохранена
            # находкой 5): сравнение ТОЛЬКО `converged != checked` пропускает
            # вырожденный прогон — если обход классификатора/дерева сломался и
            # не нашёл ни одной строки, `checked == converged == 0`, и старая
            # проверка молчала бы, отчитавшись успехом, хотя не проверила
            # ровно ничего. Пинуются ОБЕ популяции — корпус и то, что из него
            # реально доходит до интерфейса, — раздельно: смешение стёрло бы
            # именно ту разницу, которую находка 5 просила не заметать.
            if checked != expected_corpus:
                overall_ok = False
                print(f"  MISMATCH: tender {tender_id} checked={checked} != expected_corpus={expected_corpus}")
            if reachable_checked != expected_reachable:
                overall_ok = False
                print(f"  MISMATCH: tender {tender_id} ui_reachable={reachable_checked} "
                      f"!= expected_ui_reachable={expected_reachable}")

        if mismatches:
            print(f"mismatches: {len(mismatches)}")
            for m in mismatches:
                print(f"  tender={m.tender_id} work_category_id={m.work_category_id} {m.detail}")

        if not overall_ok:
            print("RESULT: FAIL")
            return 1
        print("RESULT: OK")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
