"""Приёмка: сходимость раскрытия по всем узлам обеих трасс стенда (DoD 2,
задача 7 плана фичи «попозиционное раскрытие»).

Не CI-тест — ходит в живой `gca_dev`, только читает. Для КАЖДОГО тендера
стенда и его трассы АНТТЕК (этапы 1-4):

  1. строится свод (`build_stage_summary`) на предложениях трассы;
  2. рекурсивно (включая детей) собираются все строки свода с
     `has_drilldown_rows == true`;
  3. для каждой такой строки вызывается `build_position_drilldown` и
     проверяются три вещи (спека §2.13): `reason is None`, каждая колонка
     `converged is True`, и `article_amount` колонки разложения равен
     `cells[i].amount` той же колонки строки свода (`None` с обеих сторон —
     ноль; случай "0.00" против `null` — та же проверка, не отдельная ветка,
     см. `money_to_decimal`).

Ожидание — ПОКОЛОНОЧНО зафиксированные числа узлов (`EXPECTED`, вывод из
блока замеров спеки — 113 и 189 в корпусе, минус узлы, скрытые правилом
§2.14; см. комментарий у `EXPECTED`), а не просто «список непуст». Гейт без
нижней границы пропускает вырожденный прогон: если `has_drilldown_rows`
однажды перестанет находить строки (регрессия ключа, сломанный обход
`children`), `checked` и `converged` станут 0 РОВНО ОДИНАКОВО, и старая
проверка `converged != checked` промолчала бы, отчитавшись `RESULT: OK`,
хотя не проверила ничего. Поэтому сверяется ещё и САМО число узлов.
Расхождение с `EXPECTED` — как и расхождение по сходимости — печатается,
и скрипт завершается ненулевым кодом; числа не подгоняются под скрипт.

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
from sqlalchemy.orm import Session  # noqa: E402

from crud.common import DomainError  # noqa: E402
from crud.position_drilldown import build_position_drilldown  # noqa: E402
from crud.stage_summary import build_stage_summary  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import Contractor, Offer, OfferPackage, TenderRound  # noqa: E402

#: Стенд: тендер 2 "Генподряд" (159-ТУ) и тендер 3 "Cityzen Tr. 1 UB8b"
#: (449-ТУ) — id проверены запросом к `gca_dev`, не выдуманы. Трасса на
#: обоих — участник АНТТЕК, все четыре этапа.
TENDER_IDS = (2, 3)
#: ООО «АНТТЕК» — ИНН, а не название: в терминале печатать кириллицу нельзя,
#: а по ИНН участник ищется однозначно (contractors.inn уникален).
CONTRACTOR_INN = "7701380579"
#: Ожидаемое число узлов с `has_drilldown_rows == true`, ПОКОЛОНОЧНО, не
#: просто "не ноль" (задача 7, второй круг ревью).
#:
#: Блок замеров спеки (§2.13) даёт 113 и 189 — это счёт по КОРПУСУ: сколько
#: категорий классификатора имеют непустое поддерево строк позиций/допработ,
#: посчитанный прямым SQL по `proposal_id`, В ОБХОД эндпоинта свода
#: (воспроизведено вызовом `corpus_stats()` из
#: `scripts/gen_position_drilldown_inline.py` — даёт те же 113 и 189).
#: Числа ниже — 108 и 166 — счёт по ДРУГОЙ, более узкой популяции: строки,
#: которые РЕАЛЬНО появляются в дереве свода (`build_stage_summary`) и несут
#: `has_drilldown_rows = true`. Разница — РОВНО 5 и 23 узла, и это не
#: неопределённость, а один конкретный, до конца проверенный механизм:
#: `services/stage_summary.py::_row` (правило §2.14) убирает из дерева ЛЮБОЙ
#: вложенный узел (`parent_id is not None`), у которого валовая сумма
#: поддерева равна `None`/0 в КАЖДОЙ из четырёх колонок — даже если в
#: поддереве реально лежат строки позиций/допработ (они просто гасят друг
#: друга или изначально нулевые). Такой узел никогда не станет строкой
#: свода, значит `has_drilldown_rows` для него не вычисляется вовсе — в
#: интерфейсе нет ни строки, ни шеврона, чтобы вызвать по нему разложение.
#: Диффом множеств id подтверждено: ВСЕ 5 (тендер 2) и ВСЕ 23 (тендер 3)
#: узла из разницы имеют нулевую валовую сумму поддерева на всех 4 этапах,
#: необъяснённого остатка нет (задача 7, отчёт).
#:
#: Если это число когда-нибудь изменится — это НЕ повод перебазировать
#: константу вслед за новым замером. Это сигнал ИССЛЕДОВАТЬ: либо
#: `has_drilldown_rows`/обход дерева сломались (тогда число упадёт, часто до
#: 0 — ровно тот вырожденный случай, ради которого константа здесь и стоит),
#: либо правило §2.14 в `_row` изменилось, либо в БД стенда появились новые
#: данные по трассе АНТТЕК. Расследовать нужно КАЖДЫЙ раз, а не только когда
#: число ушло в 0.
EXPECTED = {2: 108, 3: 166}
#: Тот самый корпусный счёт (113/189) — для печати рядом с `EXPECTED`, чтобы
#: расхождение с блоком замеров спеки было видно по значению, а не только по
#: комментарию выше.
CORPUS_MEASURED = {2: 113, 3: 189}


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


def check_node(tender_id: int, offer_ids: list[int], columns: list[dict],
                row: dict, db: Session, mismatches: list[Mismatch]) -> tuple[bool, int]:
    """Одна строка свода с `has_drilldown_rows` -> `(сошлась ли по всем трём
    проверкам §2.13, сколько пар "null против нуля" в ней встретилось)`; при
    расхождении дописывает `mismatches`.

    Вызов `build_position_drilldown` обёрнут: по построению (id взяты из уже
    провалидированного дерева свода того же тендера и той же выборки) он не
    должен отказывать, но если бы отказал — `DomainError.detail` человеческий
    текст, часто кириллица, а её нельзя пускать в стектрейс этого терминала
    (задача 7, второй круг ревью). Отказ печатается ASCII-полями исключения
    (`status_code`, `code`), без `detail`, и учитывается как расхождение, а не
    падение всего прогона — так дальше проверяются оставшиеся узлы.
    """
    wc_id = row["work_category_id"]
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
    cell_by_stage = {col["stage_no"]: cell for col, cell in zip(columns, row["cells"], strict=True)}

    if set(conv_by_stage) != set(cell_by_stage):
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


def check_tender(db: Session, tender_id: int, mismatches: list[Mismatch]) -> tuple[int, int, int]:
    offer_ids = offers_of(db, tender_id, CONTRACTOR_INN)
    if len(offer_ids) != 4:
        raise SystemExit(
            f"tender {tender_id}: expected 4 ANTTEK offers (stages 1-4), "
            f"got {len(offer_ids)}: {offer_ids}"
        )

    summary = build_stage_summary(db, tender_id, offer_ids)
    columns = summary["columns"]
    nodes = collect_drilldown_rows(summary["rows"])

    checked = 0
    converged = 0
    null_zero_pairs = 0
    for row in nodes:
        checked += 1
        node_ok, node_null_zero = check_node(tender_id, offer_ids, columns, row, db, mismatches)
        if node_ok:
            converged += 1
        null_zero_pairs += node_null_zero

    return checked, converged, null_zero_pairs


def main() -> int:
    db = SessionLocal()
    try:
        overall_ok = True
        mismatches: list[Mismatch] = []
        for tender_id in TENDER_IDS:
            checked, converged, null_zero_pairs = check_tender(db, tender_id, mismatches)
            expected = EXPECTED.get(tender_id)
            corpus = CORPUS_MEASURED.get(tender_id)
            print(f"tender {tender_id}: nodes checked={checked} converged={converged}/{checked} "
                  f"expected={expected} corpus_measured={corpus} null_zero_pairs={null_zero_pairs}")
            if converged != checked:
                overall_ok = False
            # Нижняя граница гейта (задача 7, второй круг ревью): сравнение
            # ТОЛЬКО `converged != checked` пропускает вырожденный прогон —
            # если обход дерева сломался и не нашёл ни одной строки,
            # `checked == converged == 0`, и старая проверка молчала бы,
            # отчитавшись успехом, хотя не проверила ровно ничего.
            if checked != expected:
                overall_ok = False
                print(f"  MISMATCH: tender {tender_id} checked={checked} != expected={expected}")

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
