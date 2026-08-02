"""Тесты фазы B: персистенция результата парсинга (S0-2)."""
from decimal import Decimal

import pytest

from crud.documents import create_document
from crud.materials import get_or_create_material_class
from models import Invoice
from pdf_parser import ParsedInvoice, ParsedItem, ParseOutcome
from processing import persist_parse_result


def test_get_or_create_material_class_no_commit_flushes_only(db_session, factories):
    """commit=False оставляет транзакцию открытой (откат вызывающего убирает класс)."""
    from models import MaterialClass

    mc = get_or_create_material_class(db_session, name="В40", material_type="concrete", commit=False)
    assert mc.id is not None  # flush присвоил id
    db_session.rollback()
    # После отката класс не должен сохраниться
    assert db_session.query(MaterialClass).filter(MaterialClass.name == "В40").first() is None


def _outcome(cost="0.002"):
    """ParseOutcome с одной СФ и одной материальной позицией — helper для тестов фазы B."""
    from datetime import date
    return ParseOutcome(
        doc_type="invoice",
        cost_usd=Decimal(cost),
        paid_calls=1,
        invoices=[ParsedInvoice(
            number="СФ-500", date=date(2026, 5, 1),
            supplier_name="ООО Тест", supplier_inn="1111111111",
            vat_rate=20, confidence=0.9,
            items=[ParsedItem(
                raw_name="Бетон В40", item_type="material", material_class="В40",
                material_type="concrete", calc_role="base", quantity=5.0, unit="м3",
                unit_price=9000.0, amount=45000.0, vat_amount=7500.0,
            )],
        )],
    )


def test_persist_creates_invoices_and_sets_parsed(db_session, factories):
    """Фаза B создаёт СФ, ставит parsed, накапливает стоимость и счётчик."""
    project = factories.ProjectFactory.create()
    doc = create_document(db_session, project.id, "x.pdf", "k/x.pdf")
    doc.status = "processing"
    db_session.commit()

    persist_parse_result(db_session, doc.id, _outcome())

    db_session.expire_all()
    from models import Document
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"
    assert saved.doc_type == "invoice"
    assert saved.parse_cost_usd == Decimal("0.002")
    assert saved.parse_count == 1
    assert db_session.query(Invoice).filter(Invoice.document_id == doc.id).count() == 1


def test_persist_replaces_old_invoices(db_session, factories):
    """Фаза B удаляет старые СФ и вставляет новые (parse-then-swap) в одной транзакции."""
    doc = factories.DocumentFactory.create(status="processing")
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")

    persist_parse_result(db_session, doc.id, _outcome())

    db_session.expire_all()
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id).all()]
    assert numbers == ["СФ-500"]


def test_persist_phase_b_error_rolls_back_and_carries_cost(db_session, factories, monkeypatch):
    """Сбой фазы B → rollback (старые СФ целы) + TransientError с учётом стоимости (AC-S0-11)."""
    import processing

    doc = factories.DocumentFactory.create(status="processing")
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")
    db_session.commit()

    def boom(*a, **k):
        """Ломает вставку позиции внутри фазы B — эмулирует детерминированный сбой БД."""
        raise RuntimeError("db exploded mid-phase-B")
    monkeypatch.setattr(processing, "normalize_item", boom)

    with pytest.raises(processing.TransientError) as exc:
        persist_parse_result(db_session, doc.id, _outcome(cost="0.003"))
    assert exc.value.cost_usd == Decimal("0.003")   # стоимость фазы A сохранена в ошибке
    assert exc.value.paid_calls == 1
    # Санитизация (FIX A): сырой exc не должен просочиться в user-facing сообщение.
    assert "db exploded" not in exc.value.message
    assert "SQL" not in exc.value.message

    db_session.expire_all()
    # rollback внутри persist откатил удаление старой СФ — данные не потеряны (AC-S0-1/11).
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id).all()]
    assert numbers == ["СФ-OLD"]
