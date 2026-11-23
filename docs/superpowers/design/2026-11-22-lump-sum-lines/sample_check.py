"""Сверка ответа кода с разметкой выборки (спека строк-сумм §9 п. 14). Только чтение.

Берёт 37 строк выборки (`tasks/lump-sum-sample/key.json` — id строк сметы, `sample.xlsx` — ответ
пользователя «Ваш ответ» и комментарий) и считает по каждой `LumpCheck` тем же `lump_check`, что
загрузчики сравнения: обычные ставки `typical_rates`, поправки `load_lump_overrides`, база
`coalesce(estimates.vat_rate_base_override, proposals.vat_rate)`, единица каталожной позиции,
применимость — строка-работа с семьёй и вариантом, не `SYSTEM`, не архив.

Ожидание спеки (пороги 5/50): ни одна строка, размеченная ставкой, не исключена (кроме строки 16 —
комплект по подтверждению пользователя); оба «М/к кровли» — комплекты; пропущены только навигация
×28 и два «Водный объект».

Запуск (из `backend/`, база — из `backend/.env`, кодом ветки после миграции 0029):
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-22-lump-sum-lines/sample_check.py ../tasks/lump-sum-sample ../tasks/lump-sum-run/sample_check.json
Вывод — коммерческие данные (названия, цены): только в `tasks/`. Транзакция READ ONLY.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import sqlalchemy as sa  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from crud.lump_sum import load_lump_overrides, typical_rates  # noqa: E402
from crud.our_price_load import _WEIGHT  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    CatalogContext,
    CatalogPosition,
    ContextMember,
    Estimate,
    Lot,
    PositionItem,
    Proposal,
)
from services.lump_sum import lump_check  # noqa: E402

RATE_EXCEPTION_NO = 16  # «Светильники, согласно ДП» — размечена ставкой, пользователь подтвердил комплект


def main() -> None:
    sample_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    key = json.loads((sample_dir / "key.json").read_text(encoding="utf-8"))
    ws = load_workbook(sample_dir / "sample.xlsx").active
    header = [c.value for c in ws[1]]
    col_no, col_title = header.index("№"), header.index("Наименование строки")
    col_ans, col_comment = header.index("Ваш ответ"), header.index("Комментарий")
    marks = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[col_no] is None:
            continue
        marks[int(row[col_no])] = {
            "title": row[col_title],
            "answer": (row[col_ans] or "").strip().lower(),
            "comment": row[col_comment],
        }

    db = SessionLocal()
    db.execute(sa.text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    try:
        ids = [k["id"] for k in key]
        rows = {
            r.id: r
            for r in db.execute(
                sa.select(
                    PositionItem.id,
                    PositionItem.unit_cost_total,
                    _WEIGHT.label("weight"),
                    PositionItem.is_chapter,
                    CatalogPosition.unit_id,
                    CatalogContext.work_family_id,
                    CatalogContext.work_variant_id,
                    CatalogContext.semantic_kind,
                    CatalogContext.archived_at,
                    sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate).label("vat_base"),
                    Estimate.contract_id,
                    Estimate.offer_id,
                )
                .select_from(PositionItem)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .join(Estimate, Estimate.id == Lot.estimate_id)
                .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
                .outerjoin(ContextMember, ContextMember.position_item_id == PositionItem.id)
                .outerjoin(CatalogContext, CatalogContext.id == ContextMember.context_id)
                .where(PositionItem.id.in_(ids))
            ).all()
        }
        rates = typical_rates(db)
        overrides = load_lump_overrides(db, ids)
        result = []
        for k in key:
            r = rows.get(k["id"])
            mark = marks.get(int(k["no"]), {})
            check = None
            reason = None
            if r is None:
                reason = "row_missing"
            elif (
                r.is_chapter
                or r.work_family_id is None
                or r.work_variant_id is None
                or r.semantic_kind == "SYSTEM"
                or r.archived_at is not None
                or (r.contract_id is None and r.offer_id is None)
            ):
                reason = "not_applicable"
            else:
                check = lump_check(
                    family_id=r.work_family_id,
                    unit_id=r.unit_id,
                    unit_price=r.unit_cost_total,
                    weight=r.weight,
                    vat_base=r.vat_base,
                    override=overrides.get(r.id),
                    rates=rates,
                )
                if check is None:
                    reason = "not_applicable_price_weight_base_unit"
            result.append(
                {
                    "no": k["no"],
                    "id": k["id"],
                    "stratum": k.get("stratum"),
                    "title": mark.get("title"),
                    "answer": mark.get("answer"),
                    "comment": mark.get("comment"),
                    "rule": None if check is None else check.rule,
                    "is_lump": None if check is None else check.is_lump,
                    "override": None if check is None or check.override is None else check.override.verdict,
                    "ratio": None
                    if check is None or check.typical is None
                    else format(check.net / check.typical.net, ".1f"),
                    "reason": reason,
                }
            )
    finally:
        db.rollback()
        db.close()

    def lump(item):
        return item["is_lump"] is True

    rate_marked_excluded = [
        x["no"] for x in result if x["answer"] == "ставка" and lump(x) and int(x["no"]) != RATE_EXCEPTION_NO
    ]
    roof = [x["no"] for x in result if (x["title"] or "").lower().startswith("м/к кровли")]
    roof_not_lump = [n for n in roof if not lump(next(x for x in result if x["no"] == n))]
    missed = [
        {"no": x["no"], "title": x["title"], "ratio": x["ratio"]}
        for x in result
        if x["answer"] in ("сумма", "комплект") and not lump(x)
    ]
    summary = {
        "rows": len(result),
        "lump": sum(1 for x in result if lump(x)),
        "not_applicable": [x["no"] for x in result if x["reason"]],
        "rate_marked_but_excluded": rate_marked_excluded,
        "roof_rows": roof,
        "roof_not_lump": roof_not_lump,
        "marked_lump_but_rate": missed,
        "row16_is_lump": lump(next(x for x in result if int(x["no"]) == RATE_EXCEPTION_NO)),
    }
    out.write_text(json.dumps({"summary": summary, "rows": result}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"rows={summary['rows']} lump={summary['lump']} not_applicable={len(summary['not_applicable'])} "
        f"rate_marked_excluded={len(rate_marked_excluded)} roof={len(roof)} roof_not_lump={len(roof_not_lump)} "
        f"marked_lump_but_rate={len(missed)} row16_lump={summary['row16_is_lump']}"
    )


if __name__ == "__main__":
    main()
