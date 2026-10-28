"""Файл подрядчику: предпросмотр, выгрузка с хранением, список и выдача отправленных
(спека 4б §2.8-§2.11, `AGENTS.md` §8).

Предпросмотр и книга строятся из одних и тех же строк (`build_file`): предпросмотр равен
файлу по построению. Выгрузка — единственная запись этого модуля: строки, книга,
`storage.save`, строка `kp_sent_files`, коммит; любой отказ после сохранения файла откатывает
транзакцию и удаляет файл из хранилища — файл остаётся только у созданной записи. Предпросмотр,
список и выдача ничего не пишут.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud import our_price as crud_our_price
from crud.common import DomainError, iso
from models import KpSentFile, Offer, TenderRound, User
from services.excel_kp_file import build_kp_file, file_columns
from services.kp_file import FileHeader, FileLine, FileSection, build_file_rows, build_header
from services.maintenance import purge_files_best_effort
from storage import Storage


def _assemble(
    db: Session, tender_id: int, round_id: int, offer_id: int, *, inflation_series_id: int | None
) -> tuple[FileHeader, list[FileSection | FileLine], Decimal]:
    """Шапка, строки файла и база НДС КП."""
    inputs = crud_our_price.build_file_inputs(
        db,
        tender_id,
        round_id,
        offer_id,
        inflation_series_id=inflation_series_id,
        exported_on=dt.date.today(),
    )
    rows = build_file_rows(
        inputs.source_lines,
        inputs.evaluation,
        comments=inputs.comments,
        agreements=inputs.agreements,
        context=inputs.context,
    )
    return build_header(inputs.context, rows), rows, inputs.context.kp_vat_base


def build_file(
    db: Session, tender_id: int, round_id: int, offer_id: int, *, inflation_series_id: int | None
) -> tuple[FileHeader, list[FileSection | FileLine]]:
    """Шапка и строки файла участника; их читают и предпросмотр, и выгрузка.

    Raises:
        DomainError 404: тендера, этапа или оффера этого этапа нет, у оффера нет сметы КП.
        DomainError 422: `unknown_vat_base` либо отказы приведения по ИПЦ, как у разбора.
    """
    header, rows, _base = _assemble(db, tender_id, round_id, offer_id, inflation_series_id=inflation_series_id)
    return header, rows


def _finite(value: Decimal | None) -> Decimal | None:
    """Числа КП в базе без CHECK: `NaN` и `Infinity` книга пишет пустой ячейкой, предпросмотр
    обязан отдать то же самое."""
    return value if value is not None and value.is_finite() else None


def _line_view(line: FileLine) -> dict:
    return {
        "kind": "line",
        "position_item_id": line.position_item_id,
        "number": line.number,
        "title": line.title,
        "unit": line.unit,
        "quantity": _finite(line.quantity),
        "kp_unit_price": _finite(line.kp_unit_price),
        "kp_total": _finite(line.kp_total),
        "ours": line.ours,
        "deviation_pct": line.deviation_pct,
        "source": line.source,
        "inflation": line.inflation,
        "comment": line.comment,
        "comment_kind": line.comment_kind,
        "state": line.state,
    }


def preview_view(header: FileHeader, rows: Sequence[FileSection | FileLine]) -> dict:
    """Предпросмотр: значения в том виде, в каком лягут в ячейки (спека 4б §2.11). Поля строки
    названы явным перечнем: служебное поле строки файла в ответ не попадает."""
    return {
        "header": {
            "tender": header.tender,
            "object": header.object,
            "round": header.round,
            "contractor": header.contractor,
            "vat_label": header.vat_label,
            "source_label": header.source_label,
            "level_label": header.level_label,
            "in_progress_lines": header.in_progress_lines,
            "inflation_years": header.inflation_years,
            "filename": header.filename,
            "exported_on": header.exported_on,
            "inflation_series": header.inflation_series,
            "inflation_target_month": header.inflation_target_month,
        },
        "columns": file_columns(header.inflation_years is not None),
        "rows": [
            {"kind": "section", "number": row.number, "title": row.title}
            if isinstance(row, FileSection)
            else _line_view(row)
            for row in rows
        ],
    }


def export_file(
    db: Session,
    storage: Storage,
    tender_id: int,
    round_id: int,
    offer_id: int,
    *,
    inflation_series_id: int | None,
    user_id: int,
) -> tuple[bytes, str]:
    """Книга файла подрядчику и запись об отправленном файле; возвращает байты и имя файла.

    Транзакцию ведёт сама функция: файл в хранилище и запись должны появиться или исчезнуть
    вместе. Отказы до сохранения файла (404, `unknown_vat_base`, отказы ИПЦ) не оставляют ни файла,
    ни записи.
    """
    header, rows, vat_base = _assemble(db, tender_id, round_id, offer_id, inflation_series_id=inflation_series_id)
    content = build_kp_file(header, rows)
    key = storage.save(content)
    try:
        db.add(
            KpSentFile(
                offer_id=offer_id,
                file_key=key,
                filename=header.filename,
                file_sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                vat_rate=vat_base,
                level_label=header.level_label,
                created_by=user_id,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        purge_files_best_effort(storage, [key], context="Файл подрядчику")
        raise
    return content, header.filename


def _check_offer(db: Session, tender_id: int, round_id: int, offer_id: int) -> None:
    """Оффер должен принадлежать этапу этого тендера. Сметы у него может не быть: замена раунда
    уносит смету, а отправленные файлы остаются.

    Raises:
        DomainError 404: цепочка тендер, этап, оффер не сходится.
    """
    found = db.execute(
        sa.select(Offer.id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Offer.id == offer_id, TenderRound.id == round_id, TenderRound.tender_id == tender_id)
    ).scalar_one_or_none()
    if found is None:
        raise DomainError(404, f"Оффер {offer_id} не найден в этапе {round_id} тендера {tender_id}.")


def list_sent_files(db: Session, tender_id: int, round_id: int, offer_id: int) -> list[dict]:
    """Отправленные файлы оффера, новые сверху (спека 4б §2.11).

    Raises:
        DomainError 404: оффера этого этапа нет.
    """
    _check_offer(db, tender_id, round_id, offer_id)
    rows = db.execute(
        sa.select(
            KpSentFile.id,
            KpSentFile.filename,
            KpSentFile.created_at,
            KpSentFile.created_by,
            User.email,
            KpSentFile.vat_rate,
            KpSentFile.level_label,
            KpSentFile.size_bytes,
        )
        .join(User, User.id == KpSentFile.created_by)
        .where(KpSentFile.offer_id == offer_id)
        .order_by(KpSentFile.created_at.desc(), KpSentFile.id.desc())
    ).all()
    return [
        {
            "id": file_id,
            "filename": filename,
            "created_at": iso(created_at),
            "created_by": {"id": user_id, "email": email},
            "vat_rate": vat_rate,
            "level_label": level_label,
            "size_bytes": size_bytes,
        }
        for file_id, filename, created_at, user_id, email, vat_rate, level_label, size_bytes in rows
    ]


def sent_file(db: Session, tender_id: int, round_id: int, offer_id: int, file_id: int) -> KpSentFile:
    """Запись отправленного файла этого оффера.

    Raises:
        DomainError 404: оффера этого этапа или файла у этого оффера нет.
    """
    _check_offer(db, tender_id, round_id, offer_id)
    record = db.execute(
        sa.select(KpSentFile).where(KpSentFile.id == file_id, KpSentFile.offer_id == offer_id)
    ).scalar_one_or_none()
    if record is None:
        raise DomainError(404, f"Файл {file_id} не найден у оффера {offer_id}.")
    return record
