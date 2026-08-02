"""Integration tests for routers/invoices.py — upload, reparse, update, delete."""
import pytest


def test_upload_enqueues_via_background_tasks(client, factories, in_memory_s3,
                                              sample_pdf_bytes, monkeypatch):
    """Upload → 202/processing/invoices=[]/duplicate=false; process_document поставлен
    именно в BackgroundTasks и НЕ исполнен к моменту ответа (AC-S1-1)."""
    import io

    from fastapi import BackgroundTasks

    import processing

    calls: list[dict] = []

    def spy_add_task(self, func, *args, **kwargs):
        """Фиксирует постановку в фон, НЕ исполняя таску (документ останется processing)."""
        calls.append({"func": func, "args": args, "kwargs": kwargs})
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    project = factories.ProjectFactory.create()
    files = {"file": ("a.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    r = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files)

    assert r.status_code == 202
    d = r.json()
    assert d["status"] == "processing"
    assert d["invoices"] == []
    assert d["duplicate"] is False
    # Поставлена ИМЕННО фоновая таска с правильной функцией и аргументами:
    assert len(calls) == 1
    assert calls[0]["func"] is processing.process_document
    assert calls[0]["args"][0] == d["id"]
    assert calls[0]["kwargs"]["mode"] == "parse"
    assert calls[0]["kwargs"]["pdf_bytes"] is not None
    # Таска не исполнялась → документ в БД остался processing (ответ не ждал обработку):
    g = client.get(f"/api/invoices/documents/{d['id']}")
    assert g.json()["status"] == "processing"


def test_reparse_enqueues_via_background_tasks(client, factories, in_memory_s3, monkeypatch):
    """Reparse → 202 + processing; таска в BackgroundTasks, не исполнена (AC-S1-1)."""
    from fastapi import BackgroundTasks

    import processing

    calls: list[dict] = []

    def spy_add_task(self, func, *args, **kwargs):
        """Спай постановки в фон без исполнения."""
        calls.append({"func": func, "args": args, "kwargs": kwargs})
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    doc = factories.DocumentFactory.create(s3_key="k/r.pdf", status="parsed")
    in_memory_s3["k/r.pdf"] = b"%PDF"
    r = client.post(f"/api/invoices/documents/{doc.id}/reparse")
    assert r.status_code == 202
    assert r.json()["status"] == "processing"
    assert len(calls) == 1
    assert calls[0]["func"] is processing.process_document
    assert calls[0]["args"][0] == doc.id
    assert calls[0]["kwargs"]["mode"] == "parse"


def test_deskew_reparse_enqueues_via_background_tasks(client, factories, in_memory_s3, monkeypatch):
    """Deskew-reparse → 202 + processing; таска в BackgroundTasks с mode="deskew",
    не исполнена (AC-S1-1, зеркало test_reparse_enqueues_via_background_tasks)."""
    from fastapi import BackgroundTasks

    import processing

    calls: list[dict] = []

    def spy_add_task(self, func, *args, **kwargs):
        """Спай постановки в фон без исполнения."""
        calls.append({"func": func, "args": args, "kwargs": kwargs})
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    doc = factories.DocumentFactory.create(s3_key="k/d.pdf", status="parsed")
    in_memory_s3["k/d.pdf"] = b"%PDF"
    r = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert r.status_code == 202
    assert r.json()["status"] == "processing"
    assert len(calls) == 1
    assert calls[0]["func"] is processing.process_document
    assert calls[0]["args"][0] == doc.id
    assert calls[0]["kwargs"]["mode"] == "deskew"
    # Таска не исполнялась → документ в БД остался processing:
    g = client.get(f"/api/invoices/documents/{doc.id}")
    assert g.json()["status"] == "processing"


def test_upload_rejects_non_pdf(client, factories):
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("doc.txt", b"not a pdf", "text/plain")},
        data={"project_id": project.id},
    )
    assert response.status_code == 400


def test_upload_creates_document_with_invoices(client, factories, sample_pdf_bytes, mock_openrouter):
    """Upload → 202 сразу; терминальный результат парсинга проверяем через GET (S1: обработка в фоне)."""
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 202
    doc_id = response.json()["id"]
    body = client.get(f"/api/invoices/documents/{doc_id}").json()
    assert body["status"] == "parsed"
    assert body["doc_type"] == "invoice"
    assert body["invoice_count"] == 1
    assert body["invoices"][0]["number"] == "СФ-101"
    assert len(body["invoices"][0]["items"]) == 1


def test_upload_unparseable_marks_doc_type_unknown(
    client, factories, sample_pdf_bytes, mock_openrouter,
):
    """202 сразу; терминальный doc_type=unknown/status=error виден через GET (S1)."""
    mock_openrouter.use_scenario("unparseable")
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 202
    doc_id = response.json()["id"]
    body = client.get(f"/api/invoices/documents/{doc_id}").json()
    # При doc_type=unknown бэкенд проставляет ОБА поля: doc_type=unknown и status=error.
    assert body["doc_type"] == "unknown"
    assert body["status"] == "error"
    assert body["invoice_count"] == 0


def test_upload_invalid_json_marks_error(
    client, factories, sample_pdf_bytes, mock_openrouter,
):
    """202 сразу; терминальный error виден через GET (S1)."""
    mock_openrouter.use_scenario("invalid_json")
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 202
    doc_id = response.json()["id"]
    body = client.get(f"/api/invoices/documents/{doc_id}").json()
    assert body["status"] == "error"
    # Невалидный JSON не должен оставлять полу-сохранённых СФ в БД.
    assert body["invoice_count"] == 0


def test_upload_truncated_response_saves_no_invoices(
    client, factories, sample_pdf_bytes, mock_openrouter,
):
    """finish_reason=length → parse returns error, no Invoice rows created.

    202 сразу; терминальный error виден через GET (S1)."""
    mock_openrouter.use_scenario("truncated_length")
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 202
    doc_id = response.json()["id"]
    body = client.get(f"/api/invoices/documents/{doc_id}").json()
    assert body["status"] == "error"
    assert body["invoice_count"] == 0


def test_upload_duplicate_returns_200_with_flag(client, factories, in_memory_s3, mock_openrouter, sample_pdf_bytes):
    """Повторная загрузка того же файла → 200 duplicate:true, S3 не растёт, документ не создан (Q6)."""
    import io

    files = {"file": ("a.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    project = factories.ProjectFactory.create()
    r1 = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files)
    assert r1.status_code in (200, 202)
    s3_size_after_first = len(in_memory_s3)

    files2 = {"file": ("copy.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    r2 = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files2)
    assert r2.status_code == 200
    d = r2.json()
    assert d["duplicate"] is True
    assert d["id"] == r1.json()["id"]           # тот же документ
    assert len(in_memory_s3) == s3_size_after_first  # S3 не вырос


def test_upload_duplicate_while_original_processing(client, factories, in_memory_s3, sample_pdf_bytes, db_session):
    """Дубль, пока оригинал в processing → 200 duplicate:true со статусом processing (спека §2)."""
    import hashlib
    import io

    project = factories.ProjectFactory.create()
    file_hash = hashlib.sha256(sample_pdf_bytes).hexdigest()
    factories.DocumentFactory.create(project=project, status="processing", file_hash=file_hash)
    db_session.commit()

    files = {"file": ("b.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    r = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files)
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["status"] == "processing"


def test_upload_incomplete_totals_saves_no_invoices(
    client, factories, sample_pdf_bytes, mock_openrouter,
):
    """Item sum ≠ printed «Всего к оплате» → parse returns error, no Invoice rows created.

    202 сразу; терминальный error виден через GET (S1)."""
    mock_openrouter.use_scenario("incomplete_totals")
    project = factories.ProjectFactory.create()
    response = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert response.status_code == 202
    doc_id = response.json()["id"]
    body = client.get(f"/api/invoices/documents/{doc_id}").json()
    assert body["status"] == "error"
    assert body["invoice_count"] == 0


def test_get_document_404(client):
    response = client.get("/api/invoices/documents/9999")
    assert response.status_code == 404


def test_serialized_document_exposes_last_error(client, factories):
    """API отдаёт last_error для документов в статусе error (S0-7)."""
    doc = factories.DocumentFactory.create(status="error", last_error="vision down")
    d = client.get(f"/api/invoices/documents/{doc.id}").json()
    assert d["last_error"] == "vision down"


def test_list_documents_filtered_by_project(client, factories):
    p1 = factories.ProjectFactory.create()
    p2 = factories.ProjectFactory.create()
    factories.DocumentFactory.create(project=p1)
    factories.DocumentFactory.create(project=p2)

    response = client.get(f"/api/invoices/documents?project_id={p1.id}")
    assert response.status_code == 200
    assert all(d["project_id"] == p1.id for d in response.json())


def test_update_invoice_replaces_items(client, factories):
    invoice = factories.InvoiceFactory.create()
    factories.InvoiceItemFactory.create(invoice=invoice, raw_name="Старая позиция")
    document_id = invoice.document_id

    response = client.put(
        f"/api/invoices/{invoice.id}",
        json={
            "number": "СФ-NEW",
            "date": "2026-05-01",
            "supplier_name": "Новый",
            "supplier_inn": None,
            "vat_rate": 20.0,
            "items": [
                {
                    "id": None,
                    "raw_name": "Новая",
                    "item_type": "material",
                    "material_class_id": None,
                    "quantity": 3.0,
                    "unit": "м3",
                    "unit_price": 9000.0,
                    "amount": 27000.0,
                    "vat_amount": 4500.0,
                }
            ],
        },
    )
    assert response.status_code == 200

    # Проверяем, что новый шейп действительно сохранился — старая позиция удалена,
    # новая на месте, поля СФ обновлены.
    doc_response = client.get(f"/api/invoices/documents/{document_id}")
    assert doc_response.status_code == 200
    doc = doc_response.json()
    assert len(doc["invoices"]) == 1
    inv = doc["invoices"][0]
    assert inv["number"] == "СФ-NEW"
    assert inv["supplier_name"] == "Новый"
    assert len(inv["items"]) == 1
    assert inv["items"][0]["raw_name"] == "Новая"
    assert inv["items"][0]["quantity"] == 3.0


def test_delete_invoice(client, factories):
    invoice = factories.InvoiceFactory.create()
    document_id = invoice.document_id

    response = client.delete(f"/api/invoices/{invoice.id}")
    assert response.status_code == 200

    # Проверяем, что СФ действительно убрана из документа.
    doc_response = client.get(f"/api/invoices/documents/{document_id}")
    assert doc_response.status_code == 200
    assert doc_response.json()["invoice_count"] == 0
    assert doc_response.json()["invoices"] == []


def test_delete_document_removes_from_s3(
    client, factories, in_memory_s3,
):
    doc = factories.DocumentFactory.create(s3_key="2026/05/test.pdf")
    in_memory_s3["2026/05/test.pdf"] = b"fake"

    response = client.delete(f"/api/invoices/documents/{doc.id}")
    assert response.status_code == 200
    assert "2026/05/test.pdf" not in in_memory_s3


# --- Верификация СФ ---

def test_invoice_unverified_by_default(client, factories):
    invoice = factories.InvoiceFactory.create()
    doc = client.get(f"/api/invoices/documents/{invoice.document_id}").json()
    inv = doc["invoices"][0]
    assert inv["verified"] is False
    assert inv["verified_at"] is None


def test_verify_invoice(client, factories):
    invoice = factories.InvoiceFactory.create()
    response = client.post(f"/api/invoices/{invoice.id}/verify")
    assert response.status_code == 200
    body = response.json()
    assert body["invoice_id"] == invoice.id
    assert body["verified_at"] is not None


def test_verify_invoice_reflected_in_document(client, factories):
    invoice = factories.InvoiceFactory.create()
    client.post(f"/api/invoices/{invoice.id}/verify")

    doc = client.get(f"/api/invoices/documents/{invoice.document_id}").json()
    inv = doc["invoices"][0]
    assert inv["verified"] is True
    assert inv["verified_at"] is not None


def test_unverify_invoice(client, factories):
    invoice = factories.InvoiceFactory.create()
    client.post(f"/api/invoices/{invoice.id}/verify")

    response = client.post(f"/api/invoices/{invoice.id}/unverify")
    assert response.status_code == 200

    doc = client.get(f"/api/invoices/documents/{invoice.document_id}").json()
    inv = doc["invoices"][0]
    assert inv["verified"] is False
    assert inv["verified_at"] is None


def test_verify_nonexistent_invoice_returns_404(client):
    response = client.post("/api/invoices/9999/verify")
    assert response.status_code == 404


def test_unverify_nonexistent_invoice_returns_404(client):
    response = client.post("/api/invoices/9999/unverify")
    assert response.status_code == 404


def test_update_verified_invoice_returns_409(client, factories):
    invoice = factories.InvoiceFactory.create()
    client.post(f"/api/invoices/{invoice.id}/verify")

    response = client.put(
        f"/api/invoices/{invoice.id}",
        json={
            "number": "СФ-HACK",
            "date": "2026-05-01",
            "supplier_name": "Хакер",
            "supplier_inn": None,
            "vat_rate": 20.0,
            "items": [],
        },
    )
    assert response.status_code == 409


def test_reparse_verified_document_returns_409(client, factories):
    doc = factories.DocumentFactory.create()
    invoice = factories.InvoiceFactory.create(document=doc)
    client.post(f"/api/invoices/{invoice.id}/verify")

    response = client.post(f"/api/invoices/documents/{doc.id}/reparse")
    assert response.status_code == 409


def test_reparse_returns_409_when_already_processing(client, factories, in_memory_s3):
    """Reparse документа, уже находящегося в processing → 409 (AC-S0-4)."""
    doc = factories.DocumentFactory.create(s3_key="k/busy.pdf", status="processing")
    in_memory_s3["k/busy.pdf"] = b"%PDF"
    resp = client.post(f"/api/invoices/documents/{doc.id}/reparse")
    assert resp.status_code == 409


def test_delete_verified_invoice_returns_409(client, factories):
    invoice = factories.InvoiceFactory.create()
    client.post(f"/api/invoices/{invoice.id}/verify")

    response = client.delete(f"/api/invoices/{invoice.id}")
    assert response.status_code == 409


def test_delete_document_with_verified_invoice_returns_409(client, factories):
    doc = factories.DocumentFactory.create()
    invoice = factories.InvoiceFactory.create(document=doc)
    client.post(f"/api/invoices/{invoice.id}/verify")

    response = client.delete(f"/api/invoices/documents/{doc.id}")
    assert response.status_code == 409


# --- Блокировка мутаций СФ во время обработки документа (S0-8, расширено на pending в S1) ---

@pytest.mark.parametrize("busy_status", ["pending", "processing"])
@pytest.mark.parametrize("method,path_tmpl,body", [
    ("put", "/api/invoices/{invoice_id}", {"number": "X", "date": "2026-05-01", "vat_rate": 20, "items": []}),
    ("post", "/api/invoices/{invoice_id}/verify", None),
    ("post", "/api/invoices/{invoice_id}/unverify", None),
    ("delete", "/api/invoices/{invoice_id}", None),
])
def test_invoice_mutations_return_409_while_busy(client, factories, method, path_tmpl, body, busy_status):
    """Любая мутация СФ документа в pending|processing → 409 (S1: guard закрывает и pending-окно)."""
    doc = factories.DocumentFactory.create(status=busy_status)
    inv = factories.InvoiceFactory.create(document=doc)
    url = path_tmpl.format(invoice_id=inv.id)
    resp = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert resp.status_code == 409


@pytest.mark.parametrize("busy_status", ["pending", "processing"])
def test_bulk_delete_returns_409_when_any_document_busy(client, factories, db_session, busy_status):
    """bulk-delete, если хоть один документ набора в pending|processing → 409, НИЧЕГО не удалено (S0-8, S1)."""
    from models import Invoice

    doc_busy = factories.DocumentFactory.create(status=busy_status)
    doc_free = factories.DocumentFactory.create(status="parsed")
    inv_busy = factories.InvoiceFactory.create(document=doc_busy)
    inv_free = factories.InvoiceFactory.create(document=doc_free)
    resp = client.request("DELETE", "/api/invoices/bulk", json={"ids": [inv_free.id, inv_busy.id]})
    assert resp.status_code == 409
    # атомарность bulk (часть контракта): 409 ⇒ не удалили НИ ОДНОЙ СФ, включая свободную.
    db_session.expire_all()
    remaining = {i.id for i in db_session.query(Invoice).all()}
    assert inv_free.id in remaining and inv_busy.id in remaining


def test_bulk_delete_locks_documents_in_id_order(client, factories):
    """bulk-delete с несколькими документами (разный порядок id) не дедлокает и работает (S0-8, анти-дедлок)."""
    docs = [factories.DocumentFactory.create(status="parsed") for _ in range(3)]
    invs = [factories.InvoiceFactory.create(document=d) for d in docs]
    # Порядок id во входе обратный — блокировка всё равно по возрастанию id.
    resp = client.request("DELETE", "/api/invoices/bulk", json={"ids": [i.id for i in reversed(invs)]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3


@pytest.mark.parametrize("busy_status", ["pending", "processing"])
def test_delete_document_returns_409_while_busy(client, factories, busy_status):
    """delete документа в pending|processing → 409 (S0-8, S1)."""
    doc = factories.DocumentFactory.create(status=busy_status)
    resp = client.delete(f"/api/invoices/documents/{doc.id}")
    assert resp.status_code == 409


# --- Интеграция с поставщиками ---

def test_upload_creates_supplier_record(client, factories, sample_pdf_bytes, mock_openrouter, db_session):
    """После парсинга PDF в таблице suppliers должна появиться запись для поставщика из СФ."""
    from models import Invoice, Supplier

    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": project.id},
    )
    assert resp.status_code == 202

    # Фикстура happy_path содержит supplier_inn="0000000000", supplier_name="ООО Поставщик"
    supplier = db_session.query(Supplier).filter(Supplier.inn == "0000000000").first()
    assert supplier is not None
    assert supplier.name == "ООО Поставщик"

    # Invoice должен быть связан с этим поставщиком
    inv = db_session.query(Invoice).filter(Invoice.supplier_inn == "0000000000").first()
    assert inv is not None
    assert inv.supplier_id == supplier.id


def test_upload_reuse_existing_supplier(client, factories, sample_pdf_bytes, mock_openrouter, db_session):
    """Два инвойса с одним ИНН → один supplier в БД."""
    from models import Invoice, Supplier

    p1 = factories.ProjectFactory.create()
    p2 = factories.ProjectFactory.create()

    resp1 = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": p1.id},
    )
    assert resp1.status_code == 202
    resp2 = client.post(
        "/api/invoices/upload",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"project_id": p2.id},
    )
    assert resp2.status_code == 202

    suppliers = db_session.query(Supplier).filter(Supplier.inn == "0000000000").all()
    assert len(suppliers) == 1

    invoices = db_session.query(Invoice).filter(Invoice.supplier_inn == "0000000000").all()
    assert len(invoices) == 2
    assert all(inv.supplier_id == suppliers[0].id for inv in invoices)


def test_update_invoice_links_supplier(client, factories, db_session):
    """PUT /invoices/{id} с новыми данными поставщика создаёт/находит запись в suppliers."""
    from models import Invoice, Supplier

    invoice = factories.InvoiceFactory.create(supplier_id=None, supplier_name=None, supplier_inn=None)

    resp = client.put(
        f"/api/invoices/{invoice.id}",
        json={
            "number": "СФ-UPD",
            "date": "2026-05-01",
            "supplier_name": "ООО Новый",
            "supplier_inn": "9876543210",
            "vat_rate": 20.0,
            "items": [],
        },
    )
    assert resp.status_code == 200

    db_session.expire_all()
    inv = db_session.query(Invoice).filter(Invoice.id == invoice.id).first()
    assert inv.supplier_id is not None
    supplier = db_session.query(Supplier).filter(Supplier.id == inv.supplier_id).first()
    assert supplier.inn == "9876543210"
    assert supplier.name == "ООО Новый"


def test_update_invoice_clears_supplier_when_name_empty(client, factories, db_session):
    """PUT /invoices/{id} с пустым supplier_name → supplier_id = None."""
    from models import Invoice

    supplier = factories.SupplierFactory.create(name="ООО Старый", inn="1231231230")
    invoice = factories.InvoiceFactory.create(supplier_id=supplier.id, supplier_name="ООО Старый")

    resp = client.put(
        f"/api/invoices/{invoice.id}",
        json={
            "number": invoice.number,
            "date": str(invoice.date),
            "supplier_name": "",
            "supplier_inn": None,
            "vat_rate": 20.0,
            "items": [],
        },
    )
    assert resp.status_code == 200

    db_session.expire_all()
    inv = db_session.query(Invoice).filter(Invoice.id == invoice.id).first()
    assert inv.supplier_id is None


def test_update_invoice_inn_without_name_returns_422(client, factories):
    """PUT /invoices/{id}: supplier_inn без supplier_name → 422."""
    invoice = factories.InvoiceFactory.create(supplier_id=None, supplier_name=None)

    resp = client.put(
        f"/api/invoices/{invoice.id}",
        json={
            "number": invoice.number,
            "date": str(invoice.date),
            "supplier_name": None,
            "supplier_inn": "7707083893",
            "vat_rate": 20.0,
            "items": [],
        },
    )
    assert resp.status_code == 422


def test_update_invoice_renames_supplier_and_cascades(client, factories, db_session):
    """PUT /invoices/{id}: тот же ИНН, изменённое имя → каноническое переименование
    поставщика + каскад во все его счета + warning supplier_renamed."""
    from models import Invoice, Supplier

    supplier = factories.SupplierFactory.create(
        name="общество с ограниченной ответственностью Ромашка",
        inn="7707083893",
    )
    inv1 = factories.InvoiceFactory.create(
        supplier_id=supplier.id,
        supplier_name="общество с ограниченной ответственностью Ромашка",
        supplier_inn="7707083893",
    )
    inv2 = factories.InvoiceFactory.create(
        supplier_id=supplier.id,
        supplier_name="общество с ограниченной ответственностью Ромашка",
        supplier_inn="7707083893",
    )

    resp = client.put(
        f"/api/invoices/{inv1.id}",
        json={
            "number": inv1.number,
            "date": str(inv1.date),
            "supplier_name": "ООО Ромашка",
            "supplier_inn": "7707083893",
            "vat_rate": 20.0,
            "items": [],
        },
    )
    assert resp.status_code == 200

    body = resp.json()
    assert any(w["code"] == "supplier_renamed" for w in body["warnings"])

    db_session.expire_all()
    # Каноническое имя обновлено, новый поставщик НЕ создан
    suppliers = db_session.query(Supplier).filter(Supplier.inn == "7707083893").all()
    assert len(suppliers) == 1
    assert suppliers[0].name == "ООО Ромашка"
    # Каскад: оба счёта получили новое имя
    for inv_id in (inv1.id, inv2.id):
        inv = db_session.query(Invoice).filter(Invoice.id == inv_id).first()
        assert inv.supplier_id == supplier.id
        assert inv.supplier_name == "ООО Ромашка"


# --- Deskew-reparse ---

def test_deskew_reparse_rotates_and_backs_up(client, factories, in_memory_s3, mock_openrouter, monkeypatch):
    """Повороты ≠ 0: создаётся {key}.orig, основной ключ перезаписан, документ распарсен."""
    from decimal import Decimal

    import pdf_orientation as po

    doc = factories.DocumentFactory.create(s3_key="k/sample.pdf", status="parsed")
    in_memory_s3["k/sample.pdf"] = b"%PDF-original"

    async def fake_deskew(pdf_bytes):
        """Возвращает исправленные байты + поворот + detect-cost."""
        return b"%PDF-corrected", [270], Decimal("0.001")
    monkeypatch.setattr(po, "deskew_pdf", fake_deskew)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 202
    assert in_memory_s3["k/sample.pdf.orig"] == b"%PDF-original"   # бэкап оригинала
    assert in_memory_s3["k/sample.pdf"] == b"%PDF-corrected"        # перезапись


def test_deskew_reparse_no_rotation_keeps_s3(client, factories, in_memory_s3, mock_openrouter, monkeypatch):
    """Все нули: S3 не трогаем, бэкап не создаём, документ всё равно распарсен."""
    from decimal import Decimal

    import pdf_orientation as po

    doc = factories.DocumentFactory.create(s3_key="k/up.pdf", status="parsed")
    in_memory_s3["k/up.pdf"] = b"%PDF-up"

    async def fake_deskew(pdf_bytes):
        """Возвращает исходные байты без поворотов + detect-cost."""
        return pdf_bytes, [0], Decimal("0.001")
    monkeypatch.setattr(po, "deskew_pdf", fake_deskew)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 202
    assert "k/up.pdf.orig" not in in_memory_s3   # бэкап не создан


def test_deskew_reparse_verified_returns_409(client, factories, in_memory_s3):
    """Документ с подтверждённой СФ → 409 до попытки коррекции ориентации."""
    doc = factories.DocumentFactory.create(s3_key="k/v.pdf", status="parsed")
    factories.InvoiceFactory.create(document=doc, verified=True)
    in_memory_s3["k/v.pdf"] = b"%PDF"
    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 409


def test_deskew_vision_failure_becomes_error_via_polling(client, factories, in_memory_s3, monkeypatch):
    """Сбой vision (TransientError с http_status=502) → 202 сразу; ошибка доезжает
    через polling как status=error + last_error (S1: reraise/try-except убраны из
    эндпоинта, фоновой таске отвечать некому). S3 не тронут (AC-S0-8 сохранён)."""
    import pdf_orientation as po
    from processing import TransientError

    doc = factories.DocumentFactory.create(s3_key="k/x.pdf", status="parsed")
    in_memory_s3["k/x.pdf"] = b"%PDF-x"

    async def boom(pdf_bytes):
        """Эмулирует недоступность vision-сервиса на detect — тот же текст, что и
        реальный сбой httpx в pdf_orientation.detect_orientation (сверено grep'ом)."""
        raise TransientError("Сервис распознавания ориентации недоступен", http_status=502)
    monkeypatch.setattr(po, "deskew_pdf", boom)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 202
    g = client.get(f"/api/invoices/documents/{doc.id}").json()
    assert g["status"] == "error"
    assert g["last_error"] == "Сервис распознавания ориентации недоступен"
    assert "k/x.pdf.orig" not in in_memory_s3
    assert in_memory_s3["k/x.pdf"] == b"%PDF-x"          # оригинал не тронут


def test_new_document_defaults_parse_cost_zero(db_session, factories):
    """Свежесозданный документ имеет нулевую стоимость и нулевой счётчик разборов."""
    from decimal import Decimal

    from crud.documents import create_document

    project = factories.ProjectFactory.create()
    doc = create_document(db_session, project.id, "x.pdf", "2026/07/x.pdf")

    assert doc.parse_cost_usd == Decimal("0")
    assert doc.parse_count == 0


def test_new_document_defaults_to_pending(db_session, factories):
    """create_document создаёт документ в статусе pending, не parsed (S0-1, AC-S0-5)."""
    from crud.documents import create_document

    project = factories.ProjectFactory.create()
    doc = create_document(db_session, project.id, "x.pdf", "2026/07/x.pdf")

    assert doc.status == "pending"
    assert doc.processing_started_at is None
    assert doc.last_error is None


def test_upload_records_parse_cost(client, mock_openrouter, factories, sample_pdf_bytes):
    """Успешный разбор записывает стоимость и счётчик разборов на документ (202 → GET, S1)."""
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 202
    body = client.get(f"/api/invoices/documents/{resp.json()['id']}").json()
    assert body["parse_cost_usd"] > 0
    assert body["parse_count"] == 1


def test_reparse_accumulates_parse_cost(client, mock_openrouter, factories, sample_pdf_bytes):
    """Повторный разбор суммирует стоимость, а не перезаписывает (202 → GET после каждого шага, S1)."""
    project = factories.ProjectFactory.create()
    up = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert up.status_code == 202
    doc_id = up.json()["id"]
    first_cost = client.get(f"/api/invoices/documents/{doc_id}").json()["parse_cost_usd"]

    re = client.post(f"/api/invoices/documents/{doc_id}/reparse")
    assert re.status_code == 202
    final = client.get(f"/api/invoices/documents/{doc_id}").json()
    assert final["parse_count"] == 2
    assert final["parse_cost_usd"] > first_cost


def test_failed_parse_is_still_billed(client, mock_openrouter, factories, sample_pdf_bytes):
    """КЛЮЧЕВОЙ ИНВАРИАНТ: провал сверки итогов — платный, стоимость учтена (202 → GET, S1)."""
    mock_openrouter.use_scenario("incomplete_totals")
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 202
    body = client.get(f"/api/invoices/documents/{resp.json()['id']}").json()
    assert body["status"] == "error"
    assert body["parse_cost_usd"] > 0
    assert body["parse_count"] == 1


def test_missing_cost_defaults_zero_but_counts(client, mock_openrouter, factories, sample_pdf_bytes):
    """usage.cost отсутствует → стоимость 0, но вызов был — parse_count растёт (202 → GET, S1)."""
    mock_openrouter.use_scenario("happy_path_no_cost")
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 202
    body = client.get(f"/api/invoices/documents/{resp.json()['id']}").json()
    assert body["parse_cost_usd"] == 0
    assert body["parse_count"] == 1


def test_non_200_is_not_billed(client, mock_openrouter, factories, sample_pdf_bytes):
    """Ошибка ДО платного ответа (OpenRouter != 200) → документ не биллится (202 → GET, S1)."""
    mock_openrouter.use_http_status(500)
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 202
    body = client.get(f"/api/invoices/documents/{resp.json()['id']}").json()
    assert body["status"] == "error"
    assert body["parse_cost_usd"] == 0
    assert body["parse_count"] == 0


def test_200_invalid_json_is_billed(client, mock_openrouter, factories, sample_pdf_bytes):
    """HTTP 200 с непарсящимся телом — платный вызов: parse_count растёт, стоимость 0 (202 → GET, S1)."""
    mock_openrouter.use_raw_body(b"not a json body")
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 202
    body = client.get(f"/api/invoices/documents/{resp.json()['id']}").json()
    assert body["status"] == "error"
    assert body["parse_cost_usd"] == 0
    assert body["parse_count"] == 1
