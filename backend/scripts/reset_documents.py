"""Одноразовый скрипт: очистить документы и MinIO для перезаливки.

Сохраняет:
- projects (объекты)
- material_classes (классы)
- reference_prices (эталоны)

Очищает:
- documents → invoices → invoice_items (cascade)
- price_calculations
- весь bucket в MinIO

Запуск из backend/:
    python scripts/reset_documents.py

Скрипт идемпотентен: повторный запуск на пустой БД ничего не сломает.
"""
import os
import sys
from pathlib import Path

# Чтобы импорты "из backend/" работали, когда запускаем из backend/scripts/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from database import SessionLocal  # noqa: E402
from models import Document, Invoice, InvoiceItem  # noqa: E402
from s3 import S3_BUCKET, get_s3_client  # noqa: E402


def reset_db() -> None:
    db = SessionLocal()
    try:
        item_count = db.query(InvoiceItem).count()
        inv_count = db.query(Invoice).count()
        doc_count = db.query(Document).count()

        # Cascade: документы → инвойсы → позиции (определено в models.py)
        db.query(InvoiceItem).delete(synchronize_session=False)
        db.query(Invoice).delete(synchronize_session=False)
        db.query(Document).delete(synchronize_session=False)
        db.commit()

        print(
            f"[db] удалено: documents={doc_count}, invoices={inv_count}, "
            f"invoice_items={item_count}"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_minio() -> None:
    client = get_s3_client()

    # Проверим bucket
    try:
        client.head_bucket(Bucket=S3_BUCKET)
    except client.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            print(f"[s3] bucket '{S3_BUCKET}' не существует — пропускаем")
            return
        raise

    # Соберём ключи постранично
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET):
        for obj in page.get("Contents", []) or []:
            keys.append({"Key": obj["Key"]})

    if not keys:
        print(f"[s3] bucket '{S3_BUCKET}' уже пустой")
        return

    # delete_objects принимает максимум 1000 ключей за раз
    deleted = 0
    for i in range(0, len(keys), 1000):
        chunk = keys[i:i + 1000]
        resp = client.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": chunk, "Quiet": True})
        deleted += len(chunk)
        errors = resp.get("Errors") or []
        if errors:
            for err in errors:
                print(f"[s3] ОШИБКА удаления {err.get('Key')}: {err.get('Message')}")
                deleted -= 1

    print(f"[s3] удалено объектов из '{S3_BUCKET}': {deleted}")


if __name__ == "__main__":
    print("=== reset_documents.py ===")
    print("Сохраняются: projects, material_classes, reference_prices")
    print("Удаляются: documents, invoices, invoice_items, price_calculations, MinIO bucket")
    print()
    reset_db()
    reset_minio()
    print("Готово.")
