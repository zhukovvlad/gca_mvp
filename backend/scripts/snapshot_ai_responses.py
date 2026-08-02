"""Локальный скрипт: real PDF → OpenRouter → sanitize → JSON фикстура.

Запуск:
    cd backend && python scripts/snapshot_ai_responses.py \\
        tests/fixtures/pdf/real/sample.pdf happy_path

Параметры:
    1. Путь к реальному PDF (обычно из tests/fixtures/pdf/real/, который в .gitignore).
    2. Имя сценария (без .json) — будет сохранено в tests/fixtures/openrouter/.

Скрипт ходит в OpenRouter с реальным API-ключом из .env, получает ответ,
прогоняет через sanitizer (заменяет ИНН и наименования на фейки),
сохраняет в tests/fixtures/openrouter/{scenario}.json.

Реальные PDF в репо НЕ попадают (они в .gitignore).
Sanitized JSON — попадает.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _sanitize_response(raw: dict) -> dict:
    """Заменяет PII в response модели на фейковые значения."""
    text = raw["choices"][0]["message"]["content"]

    # Очищаем markdown wrapper если есть
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    text = text.strip()

    parsed = json.loads(text)

    if parsed.get("doc_type") == "invoice":
        for idx, inv in enumerate(parsed.get("invoices", [])):
            inv["supplier_name"] = f"Поставщик {idx + 1}"
            inv["supplier_inn"] = "0000000000"

    # Записываем обратно
    raw["choices"][0]["message"]["content"] = json.dumps(parsed, ensure_ascii=False)
    return raw


async def _fetch(pdf_path: Path) -> dict:
    """Отправить PDF в реальный OpenRouter и вернуть сырой JSON-ответ (дев-снапшот)."""
    import base64
    import os

    import httpx
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or api_key == "mock-key-not-used":
        raise RuntimeError(
            "OPENROUTER_API_KEY не задан или установлен на mock-значение в .env. "
            "Скрипт snapshot работает с реальным API — заполни ключ в backend/.env."
        )

    pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode()

    from pdf_parser import SYSTEM_PROMPT
    base = (os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    OPENROUTER_URL = f"{base}/chat/completions"
    model = os.getenv("OPENROUTER_MODEL") or os.getenv("AI_MODEL", "anthropic/claude-sonnet-4.6")
    engine = (
        (os.getenv("OPENROUTER_PDF_ENGINE") or "").strip()
        or (os.getenv("PDF_ENGINE") or "").strip()
        or "native"
    )
    payload = {
        "model": model,
        "max_tokens": 8192,
        "plugins": [{"id": "file-parser", "pdf": {"engine": engine}}],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "file", "file": {"filename": "doc.pdf",
                                          "file_data": f"data:application/pdf;base64,{pdf_b64}"}},
                {"type": "text", "text": "Извлеки данные."},
            ]},
        ],
    }

    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    return response.json()


def main():
    if len(sys.argv) != 3:
        print("Usage: python snapshot_ai_responses.py <pdf-path> <scenario-name>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    scenario = sys.argv[2]
    if not pdf_path.exists():
        print(f"PDF не найден: {pdf_path}")
        sys.exit(1)

    print(f"Запрос к OpenRouter для {pdf_path.name}...")
    raw = asyncio.run(_fetch(pdf_path))
    print("Ответ получен. Санитизация...")
    sanitized = _sanitize_response(raw)

    out = ROOT / "tests" / "fixtures" / "openrouter" / f"{scenario}.json"
    out.write_text(json.dumps(sanitized, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
