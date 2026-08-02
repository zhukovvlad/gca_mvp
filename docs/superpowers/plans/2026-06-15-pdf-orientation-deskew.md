# Коррекция поворота страниц PDF (on-demand deskew-reparse) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Кнопка «Выпрямить и переразобрать»: по запросу определить ориентацию каждой страницы УПД (vision), селективно перерисовать только повёрнутые страницы в правильной ориентации (raster), переразобрать документ.

**Architecture:** Изолированный модуль `backend/pdf_orientation.py` (render-for-detect → vision-detect → селективный raster через pypdfium2/Pillow/pikepdf). Эндпоинт `POST /api/invoices/documents/{id}/deskew-reparse` бэкапит оригинал в `{key}.orig`, перезаписывает основной ключ исправленным PDF и переиспользует общий хелпер reparse. Фронт зеркалит существующий reparse в двух местах.

**Tech Stack:** Python 3.12, FastAPI (async), SQLAlchemy, pypdfium2, Pillow, pikepdf, httpx, pytest+respx. React 19, TanStack Query. Команды — через `just`.

Спека: [docs/superpowers/specs/2026-06-15-pdf-orientation-deskew-design.md](../specs/2026-06-15-pdf-orientation-deskew-design.md)

Shell-обёртка (Windows): `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && <cmd> 2>&1"`. Точечный pytest: `cd backend && python -m pytest <путь>::<тест> -v` внутри того же bash-вызова.

---

## File Structure

- **Create:** `backend/pdf_orientation.py` — рендер для детекта, vision-детект, селективная raster-коррекция, оркестратор. Одна ответственность: ориентация страниц.
- **Create:** `backend/tests/unit/test_pdf_orientation.py` — unit-тесты модуля.
- **Modify:** `backend/routers/invoices.py` — рефактор `_reparse_from_s3`, новый эндпоинт `deskew-reparse`.
- **Modify:** `backend/tests/integration/test_invoices.py` — integration-тесты эндпоинта.
- **Modify:** `backend/requirements.txt` — `pypdfium2`, `Pillow`, `pikepdf`.
- **Modify:** `frontend/src/services/api/invoices.ts`, `frontend/src/services/queries.ts` — API+мутация.
- **Modify:** `frontend/src/components/projects/ErrorDocsTab.tsx`, `frontend/src/pages/Review.tsx` — кнопки.
- **Modify:** `frontend/src/test/handlers.ts`, `*.test.tsx` — моки/тесты фронта.
- **Modify:** `docs/agent/pdf-parsing.md` — документация.

---

### Task 0: Ветка и зависимости

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Ветка от main**

```bash
git checkout main
git checkout -b feat/pdf-orientation-deskew
```

- [ ] **Step 2: Добавить зависимости в requirements.txt**

Дописать в конец `backend/requirements.txt` (после `click==8.3.3`):

```
pypdfium2==5.10.1
pikepdf==10.8.0
Pillow==12.2.0
```

- [ ] **Step 3: Установить и проверить импорт**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && pip install -r requirements.txt && python -c 'import pypdfium2, pikepdf, PIL; print(\"ok\")' 2>&1"`
Expected: заканчивается `ok`.

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt
git commit -m "build(backend): pypdfium2 + pikepdf + Pillow для deskew

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 1: `target_dpi` — native-aware DPI страницы

**Files:**
- Create: `backend/pdf_orientation.py`
- Test: `backend/tests/unit/test_pdf_orientation.py`

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/unit/test_pdf_orientation.py`:

```python
import io

import pikepdf
import pypdfium2 as pdfium
from PIL import Image

import pdf_orientation as po


def _image_pdf(px_w: int, px_h: int, dpi: float = 150.0, color=(255, 255, 255)) -> bytes:
    """PDF из одной картинки px_w×px_h (имитация полностраничного скана), сохранённой при `dpi`.

    Pillow при resolution=R делает страницу px/R дюймов, поэтому native-DPI страницы ≈ R —
    это и есть управляемый рычаг для проверки _target_dpi (пол/кап/совпадение)."""
    img = Image.new("RGB", (px_w, px_h), color)
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=dpi)
    return out.getvalue()


def test_target_dpi_matches_native_within_cap():
    # Скан при 150 DPI (A4-подобный лист) → native ≈ 150, внутри [пол 150, кап 300].
    pdf = pikepdf.open(io.BytesIO(_image_pdf(1240, 1754, dpi=150.0)))
    dpi = po._target_dpi(pdf.pages[0])
    assert 150 <= dpi <= 300


def test_target_dpi_caps_at_300():
    # Скан при 400 DPI → native ≈ 400 → кап 300.
    pdf = pikepdf.open(io.BytesIO(_image_pdf(2000, 2800, dpi=400.0)))
    assert po._target_dpi(pdf.pages[0]) == 300


def test_target_dpi_floors_at_150():
    # Скан при 100 DPI → native ≈ 100 → пол 150.
    pdf = pikepdf.open(io.BytesIO(_image_pdf(800, 1100, dpi=100.0)))
    assert po._target_dpi(pdf.pages[0]) == 150
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -v 2>&1"`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdf_orientation'`.

- [ ] **Step 3: Создать модуль с `_target_dpi`**

Создать `backend/pdf_orientation.py`:

```python
"""On-demand коррекция ориентации страниц PDF: vision-детект + селективный raster.

Используется эндпоинтом deskew-reparse. См. спеку
docs/superpowers/specs/2026-06-15-pdf-orientation-deskew-design.md.
"""
import io
import logging

import pikepdf

logger = logging.getLogger(__name__)

MAX_DESKEW_PAGES = 20
DPI_CAP = 300
DPI_FLOOR = 150
DPI_DEFAULT = 200  # born-digital без вшитого растра


def _target_dpi(page: pikepdf.Page) -> int:
    """Native-aware целевой DPI для полностраничного скана.

    target_dpi ≈ image_px_width / (page_width_pt / 72), с полом DPI_FLOOR и капом DPI_CAP.
    Допущение: картинка занимает весь лист (наш кейс). Если вшитого растра нет —
    DPI_DEFAULT (born-digital, lossy-ограничение зафиксировано в спеке).
    """
    width_pt = float(page.mediabox[2]) - float(page.mediabox[0])
    if width_pt <= 0:
        return DPI_DEFAULT
    px_widths = []
    for obj in page.images.values():
        try:
            px_widths.append(pikepdf.PdfImage(obj).width)
        except Exception:  # noqa: BLE001 — нестандартный XObject не должен ронять deskew
            continue
    if not px_widths:
        return DPI_DEFAULT
    dpi = max(px_widths) / (width_pt / 72.0)
    return max(DPI_FLOOR, min(DPI_CAP, round(dpi)))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -v 2>&1"`
Expected: PASS (3 теста).

- [ ] **Step 5: Commit**

```bash
git add backend/pdf_orientation.py backend/tests/unit/test_pdf_orientation.py
git commit -m "feat(deskew): native-aware target_dpi для страницы

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `apply_rotations` — селективная raster-коррекция

**Files:**
- Modify: `backend/pdf_orientation.py`
- Test: `backend/tests/unit/test_pdf_orientation.py`

- [ ] **Step 1: Написать падающие тесты (селективность, знак, сброс /Rotate)**

Добавить в `backend/tests/unit/test_pdf_orientation.py`. Хелпер строит «скан, положенный боком»: картинку с чёрной полосой сверху поворачиваем на 90° CW (полоса уходит вправо) и пишем в PDF; корректный поворот вернёт полосу наверх.

```python
def _sideways_scan_pdf() -> bytes:
    """Картинка с маркер-полосой сверху, повёрнутая на 90° CW (полоса справа)."""
    img = Image.new("RGB", (800, 1000), (255, 255, 255))
    for y in range(100):           # верхняя полоса = маркер «верх»
        for x in range(800):
            img.putpixel((x, y), (0, 0, 0))
    sideways = img.rotate(-90, expand=True)  # PIL: отрицательный угол = по часовой
    out = io.BytesIO()
    sideways.save(out, format="PDF", resolution=150.0)
    return out.getvalue()


def _top_strip_is_dark(page_png: bytes) -> bool:
    img = Image.open(io.BytesIO(page_png)).convert("L")
    w, h = img.size
    band = img.crop((0, 0, w, h // 12))
    return sum(band.getdata()) / (band.size[0] * band.size[1]) < 64  # тёмная полоса сверху


def _render_first_page_png(pdf_bytes: bytes) -> bytes:
    pdf = pdfium.PdfDocument(pdf_bytes)
    img = pdf[0].render(scale=1.0).to_pil()
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_apply_rotations_sign_makes_upright():
    """Боковой скан (маркер справа) + корректирующий поворот → маркер снова сверху."""
    sideways = _sideways_scan_pdf()
    assert not _top_strip_is_dark(_render_first_page_png(sideways))  # до: не сверху
    corrected = po.apply_rotations(sideways, [270])  # 270° CW возвращает наверх
    assert _top_strip_is_dark(_render_first_page_png(corrected))     # после: сверху


def test_apply_rotations_resets_rotate_flag():
    """У пересобранной image-страницы /Rotate == 0 (не наследуется)."""
    src = pikepdf.open(io.BytesIO(_sideways_scan_pdf()))
    src.pages[0].Rotate = 90  # искусственный ненулевой флаг на исходнике
    buf = io.BytesIO(); src.save(buf)
    corrected = po.apply_rotations(buf.getvalue(), [270])
    out = pikepdf.open(io.BytesIO(corrected))
    assert int(out.pages[0].get("/Rotate", 0)) == 0


def test_apply_rotations_zero_delta_keeps_page_untouched():
    """Страница с delta==0 переносится как есть (не перерисовывается)."""
    two_pages = pikepdf.new()
    src = pikepdf.open(io.BytesIO(_sideways_scan_pdf()))
    two_pages.pages.append(src.pages[0])
    two_pages.pages.append(src.pages[0])
    buf = io.BytesIO(); two_pages.save(buf)
    corrected = po.apply_rotations(buf.getvalue(), [270, 0])
    out = pikepdf.open(io.BytesIO(corrected))
    # стр.1 перерисована (маркер сверху), стр.2 — исходная боковая (маркер не сверху)
    assert _top_strip_is_dark(_render_first_page_png(corrected))
    pdf = pdfium.PdfDocument(corrected)
    p2 = pdf[1].render(scale=1.0).to_pil(); b = io.BytesIO(); p2.save(b, format="PNG")
    assert not _top_strip_is_dark(b.getvalue())


def test_apply_rotations_two_rotated_pages():
    """≥2 повёрнутых страниц: обе выпрямлены, foreign-ссылки не оборвались до save."""
    src = pikepdf.open(io.BytesIO(_sideways_scan_pdf()))
    two = pikepdf.new()
    two.pages.append(src.pages[0]); two.pages.append(src.pages[0])
    buf = io.BytesIO(); two.save(buf)
    corrected = po.apply_rotations(buf.getvalue(), [270, 270])
    out = pikepdf.open(io.BytesIO(corrected))
    assert len(out.pages) == 2
    pdf = pdfium.PdfDocument(corrected)
    for idx in range(2):
        img = pdf[idx].render(scale=1.0).to_pil(); b = io.BytesIO(); img.save(b, format="PNG")
        assert _top_strip_is_dark(b.getvalue())  # обе выпрямлены
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -k apply_rotations -v 2>&1"`
Expected: FAIL — `AttributeError: module 'pdf_orientation' has no attribute 'apply_rotations'`.

- [ ] **Step 3: Реализовать `apply_rotations`**

Добавить в `backend/pdf_orientation.py` (импорты `pypdfium2 as pdfium`, `PIL.Image` — в начало файла):

```python
import pypdfium2 as pdfium
from PIL import Image
```

```python
def _page_to_image_pdf(src_pdf: pdfium.PdfDocument, index: int, delta: int, target_dpi: int) -> bytes:
    """Отрендерить страницу в исправленной ориентации (grayscale) и обернуть в 1-страничный PDF.

    target_dpi используется ДВАЖДЫ: как scale рендера и как resolution записи image→PDF,
    иначе Pillow пишет 72 DPI/пиксель и mediabox раздувается.
    """
    page = src_pdf[index]
    img = page.render(scale=target_dpi / 72.0, rotation=delta, grayscale=True).to_pil()
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=float(target_dpi))
    return out.getvalue()


def apply_rotations(pdf_bytes: bytes, rotations: list[int]) -> bytes:
    """Селективная raster-коррекция: перерисовываем только страницы с ненулевым поворотом,
    прямые переносим как есть. Пересобираем смешанный документ через pikepdf."""
    src_pikepdf = pikepdf.open(io.BytesIO(pdf_bytes))
    src_pdfium = pdfium.PdfDocument(pdf_bytes)
    result = pikepdf.new()
    keepalive = []  # foreign-PDF должны жить до result.save() (иначе copy_foreign может оборваться)
    for i, page in enumerate(src_pikepdf.pages):
        delta = rotations[i] % 360 if i < len(rotations) else 0
        if delta == 0:
            result.pages.append(page)  # как есть, без перерисовки
            continue
        target_dpi = _target_dpi(page)
        img_pdf = pikepdf.open(io.BytesIO(_page_to_image_pdf(src_pdfium, i, delta, target_dpi)))
        keepalive.append(img_pdf)
        result.pages.append(img_pdf.pages[0])
        last = result.pages[-1]
        if "/Rotate" in last:          # пиксели уже прямые — флаг не нужен
            del last["/Rotate"]        # словарный интерфейс (надёжнее атрибутного)
    buf = io.BytesIO()
    result.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -v 2>&1"`
Expected: PASS (все, включая знак, сброс /Rotate, селективность).

- [ ] **Step 5: Commit**

```bash
git add backend/pdf_orientation.py backend/tests/unit/test_pdf_orientation.py
git commit -m "feat(deskew): селективная raster-коррекция apply_rotations

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `render_pages_for_detect` + `detect_rotations` (async vision)

**Files:**
- Modify: `backend/pdf_orientation.py`
- Test: `backend/tests/unit/test_pdf_orientation.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в тест-файл (вверху — `import json, respx, pytest, httpx`):

```python
import json
import httpx
import pytest
import respx


def _openrouter_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_parses_list(monkeypatch):
    monkeypatch.setattr(po.settings, "OPENROUTER_API_KEY", "test-key")
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=_openrouter_reply("Вот повороты: [0, 90, 270]")
    )
    out = await po.detect_rotations([b"img0", b"img1", b"img2"])
    assert out == [0, 90, 270]


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_garbage_to_zeros(monkeypatch):
    monkeypatch.setattr(po.settings, "OPENROUTER_API_KEY", "test-key")
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=_openrouter_reply("не смог")
    )
    out = await po.detect_rotations([b"img0", b"img1"])
    assert out == [0, 0]  # длина = числу страниц, всё в 0


@pytest.mark.asyncio
async def test_detect_rotations_too_many_pages():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await po.detect_rotations([b"x"] * (po.MAX_DESKEW_PAGES + 1))
    assert ei.value.status_code == 413


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_upstream_500_raises_502(monkeypatch):
    monkeypatch.setattr(po.settings, "OPENROUTER_API_KEY", "test-key")
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(return_value=httpx.Response(500))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await po.detect_rotations([b"x"])
    assert ei.value.status_code == 502


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_prose_around_array(monkeypatch):
    monkeypatch.setattr(po.settings, "OPENROUTER_API_KEY", "test-key")
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=_openrouter_reply("Страница 1 и 2: ответ [90, 0]")
    )
    assert await po.detect_rotations([b"a", b"b"]) == [90, 0]  # «1»,«2» из прозы не попали
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -k detect -v 2>&1"`
Expected: FAIL — нет `detect_rotations` / `render_pages_for_detect`.

- [ ] **Step 3: Реализовать рендер для детекта и detect_rotations**

Добавить в `backend/pdf_orientation.py` (импорты вверх: `import base64`, `import json`, `import re`, `import httpx`, `from fastapi import HTTPException`, `from config import settings`, `from pdf_parser import OPENROUTER_URL`):

```python
DETECT_TIMEOUT = 30.0
_DETECT_PROMPT = (
    "На вход — страницы PDF по порядку. Для КАЖДОЙ страницы определи поворот по часовой "
    "стрелке (0, 90, 180 или 270 градусов), который сделает её вертикально читаемой. "
    "Верни ТОЛЬКО JSON-массив целых чисел длиной по числу страниц, например [0,90,0]."
)


def render_pages_for_detect(pdf_bytes: bytes, long_side: int = 768) -> list[bytes]:
    """Уменьшенные JPEG-страницы для vision-детекта (ориентации хватает низкого разрешения)."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    images: list[bytes] = []
    for page in pdf:
        w, h = page.get_size()
        scale = long_side / max(w, h)
        img = page.render(scale=scale, grayscale=True).to_pil()
        out = io.BytesIO()
        img.convert("L").save(out, format="JPEG", quality=70)
        images.append(out.getvalue())
    return images


async def detect_rotations(images: list[bytes]) -> list[int]:
    """Один vision-запрос: per-page поворот 0/90/180/270. Любой сбой парсинга → нули."""
    n = len(images)
    if n > MAX_DESKEW_PAGES:
        raise HTTPException(status_code=413, detail=f"Слишком много страниц для коррекции (> {MAX_DESKEW_PAGES})")
    content = [{"type": "text", "text": _DETECT_PROMPT}]
    for img in images:
        b64 = base64.b64encode(img).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    payload = {
        "model": settings.AI_MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": content}],
    }
    headers = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(DETECT_TIMEOUT, connect=5.0)  # быстрый фейл на коннекте, долгий read
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        # транспортный сбой / таймаут / не-2xx → НЕ деградируем в нули (иначе переразберём
        # оригинал под видом «исправлено»), а сигналим 502; эндпоинт не тронет S3 и не переразберёт
        logger.warning(f"detect_rotations: vision-запрос упал: {e}")
        raise HTTPException(status_code=502, detail="Сервис распознавания ориентации недоступен")
    # успешный 200: непарсящееся СОДЕРЖИМОЕ → нули (безопасная деградация на уровне контента)
    try:
        text = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\[[\d,\s]*\]", text)          # берём именно JSON-массив, не любые числа
        nums = json.loads(m.group(0)) if m else []
        allowed = {0, 90, 180, 270}
        rots = [v % 360 if (v % 360) in allowed else 0 for v in nums[:n]]
    except Exception:  # noqa: BLE001 — кривое содержимое не должно ронять эндпоинт
        rots = []
    rots += [0] * (n - len(rots))   # добиваем до длины n
    return rots
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/pdf_orientation.py backend/tests/unit/test_pdf_orientation.py
git commit -m "feat(deskew): render_pages_for_detect + async detect_rotations

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `deskew_pdf` — оркестратор

**Files:**
- Modify: `backend/pdf_orientation.py`
- Test: `backend/tests/unit/test_pdf_orientation.py`

- [ ] **Step 1: Написать падающие тесты**

```python
@pytest.mark.asyncio
async def test_deskew_pdf_all_zeros_returns_original(monkeypatch):
    """Все нули → исходные байты без перерисовки (идентичность)."""
    original = _sideways_scan_pdf()

    async def fake_detect(images):
        return [0] * len(images)
    monkeypatch.setattr(po, "detect_rotations", fake_detect)

    out_bytes, rots = await po.deskew_pdf(original)
    assert rots == [0]
    assert out_bytes == original  # тот же объект байтов, без перерисовки


@pytest.mark.asyncio
async def test_deskew_pdf_applies_rotation(monkeypatch):
    original = _sideways_scan_pdf()

    async def fake_detect(images):
        return [270] * len(images)
    monkeypatch.setattr(po, "detect_rotations", fake_detect)

    out_bytes, rots = await po.deskew_pdf(original)
    assert rots == [270]
    assert out_bytes != original
    assert _top_strip_is_dark(_render_first_page_png(out_bytes))  # выпрямлено
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -k deskew_pdf -v 2>&1"`
Expected: FAIL — нет `deskew_pdf`.

- [ ] **Step 3: Реализовать `deskew_pdf`**

Добавить в `backend/pdf_orientation.py`:

```python
async def deskew_pdf(pdf_bytes: bytes) -> tuple[bytes, list[int]]:
    """render-for-detect → detect → селективный raster. Все нули → исходные байты как есть."""
    images = render_pages_for_detect(pdf_bytes)
    rotations = await detect_rotations(images)
    if not any(r % 360 for r in rotations):
        return pdf_bytes, rotations          # short-circuit, без перерисовки
    return apply_rotations(pdf_bytes, rotations), rotations
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/unit/test_pdf_orientation.py -v 2>&1"`
Expected: PASS (весь файл).

- [ ] **Step 5: Commit**

```bash
git add backend/pdf_orientation.py backend/tests/unit/test_pdf_orientation.py
git commit -m "feat(deskew): оркестратор deskew_pdf (short-circuit на нулях)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Рефактор `_reparse_from_s3` (без смены поведения reparse)

**Files:**
- Modify: `backend/routers/invoices.py` (функция `reparse_document`, строки ~159-204)

- [ ] **Step 1: Прогнать существующие reparse-тесты (зелёный baseline)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py -k reparse -v 2>&1"`
Expected: PASS (фиксируем, что до рефактора зелено).

- [ ] **Step 2: Выделить хелпер `_reparse_from_s3`**

В `backend/routers/invoices.py` заменить тело `reparse_document` после гардов (блок «удалить старые СФ → скачать → parse → status/doc_type → serialize») на вызов хелпера. Добавить хелпер над `reparse_document`:

```python
async def _reparse_from_s3(doc, db: Session, pdf_bytes: bytes | None = None) -> dict:
    """Удалить старые СФ, распарсить PDF (переданный или скачанный из S3), выставить статус.
    Возвращает сериализованный документ."""
    old_count = len(doc.invoices)
    for inv in list(doc.invoices):
        db.delete(inv)
    db.commit()
    logger.info(f"Reparse: удалено старых СФ для doc={doc.id}: {old_count}")

    if pdf_bytes is None:
        try:
            pdf_bytes = download_file(doc.s3_key)
            logger.info(f"Reparse: скачан PDF из S3 (key={doc.s3_key}, размер={len(pdf_bytes)})")
        except Exception as e:
            logger.exception(f"Reparse: ошибка скачивания из S3 для doc={doc.id}")
            raise HTTPException(status_code=404, detail=f"Файл не найден в хранилище: {e}")

    from pdf_parser import parse_invoice_pdf
    result = await parse_invoice_pdf(pdf_bytes, db, doc.id)

    if result.get("error"):
        doc.status = "error"
        doc.doc_type = "unknown"
        db.commit()
        logger.warning(f"Reparse doc={doc.id} завершён с ошибкой: {result['error']}")
        db.refresh(doc)
        return _serialize_document(doc)

    doc.doc_type = result.get("doc_type", "invoice")
    doc.status = "parsed"
    db.commit()
    db.refresh(doc)
    logger.info(f"Reparse doc={doc.id} успешно завершён, СФ: {len(result.get('invoices_created', []))}")
    return _serialize_document(doc)
```

Тело `reparse_document` после гардов становится:

```python
    return await _reparse_from_s3(doc, db)
```

(Гарды 404/400/409 в начале `reparse_document` остаются как есть.)

- [ ] **Step 3: Прогнать reparse-тесты — поведение не изменилось**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py -k reparse -v 2>&1"`
Expected: PASS (как в Step 1).

- [ ] **Step 4: Commit**

```bash
git add backend/routers/invoices.py
git commit -m "refactor(invoices): выделить _reparse_from_s3 из reparse_document

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Эндпоинт `POST /documents/{doc_id}/deskew-reparse`

**Files:**
- Modify: `backend/routers/invoices.py`
- Test: `backend/tests/integration/test_invoices.py`

- [ ] **Step 1: Написать падающие integration-тесты**

Добавить в `backend/tests/integration/test_invoices.py`. `in_memory_s3` — dict ключ→bytes; `mock_openrouter` не подходит (detect — другой ответ), поэтому мокаем `pdf_orientation.detect_rotations` и `parse_invoice_pdf` напрямую через monkeypatch.

```python
def test_deskew_reparse_rotates_and_backs_up(client, factories, db_session, in_memory_s3, monkeypatch):
    """Повороты ≠ 0: создаётся {key}.orig, основной ключ перезаписан, reparse выполнен."""
    import routers.invoices as inv_router
    import pdf_orientation as po

    doc = factories.DocumentFactory.create(s3_key="k/sample.pdf", status="parsed")
    in_memory_s3["k/sample.pdf"] = b"%PDF-original"

    async def fake_deskew(pdf_bytes):
        return b"%PDF-corrected", [270]
    monkeypatch.setattr(po, "deskew_pdf", fake_deskew)

    async def fake_reparse(d, db, pdf_bytes=None):
        return {"id": d.id, "rotations_placeholder": True, "invoices": []}
    monkeypatch.setattr(inv_router, "_reparse_from_s3", fake_reparse)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 200
    assert resp.json()["rotations_applied"] == [270]
    assert in_memory_s3["k/sample.pdf.orig"] == b"%PDF-original"   # бэкап оригинала
    assert in_memory_s3["k/sample.pdf"] == b"%PDF-corrected"        # перезапись


def test_deskew_reparse_no_rotation_keeps_s3(client, factories, in_memory_s3, monkeypatch):
    """Все нули: S3 не трогаем, бэкап не создаём, reparse всё равно выполнен."""
    import routers.invoices as inv_router
    import pdf_orientation as po

    doc = factories.DocumentFactory.create(s3_key="k/up.pdf", status="parsed")
    in_memory_s3["k/up.pdf"] = b"%PDF-up"

    async def fake_deskew(pdf_bytes):
        return pdf_bytes, [0]
    monkeypatch.setattr(po, "deskew_pdf", fake_deskew)

    async def fake_reparse(d, db, pdf_bytes=None):
        return {"id": d.id, "invoices": []}
    monkeypatch.setattr(inv_router, "_reparse_from_s3", fake_reparse)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 200
    assert resp.json()["rotations_applied"] == [0]
    assert "k/up.pdf.orig" not in in_memory_s3   # бэкап не создан


def test_deskew_reparse_verified_returns_409(client, factories, in_memory_s3):
    doc = factories.DocumentFactory.create(s3_key="k/v.pdf", status="parsed")
    factories.InvoiceFactory.create(document=doc, verified=True)
    in_memory_s3["k/v.pdf"] = b"%PDF"
    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 409


def test_deskew_reparse_vision_failure_502(client, factories, in_memory_s3, monkeypatch):
    """Сбой vision (502 из deskew_pdf) → 502, S3 не тронут, бэкап не создан."""
    import pdf_orientation as po
    from fastapi import HTTPException
    doc = factories.DocumentFactory.create(s3_key="k/x.pdf", status="parsed")
    in_memory_s3["k/x.pdf"] = b"%PDF-x"

    async def boom(pdf_bytes):
        raise HTTPException(status_code=502, detail="vision down")
    monkeypatch.setattr(po, "deskew_pdf", boom)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 502
    assert "k/x.pdf.orig" not in in_memory_s3
    assert in_memory_s3["k/x.pdf"] == b"%PDF-x"   # оригинал не тронут
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py -k deskew -v 2>&1"`
Expected: FAIL — 404 (роут не существует).

- [ ] **Step 3: Реализовать эндпоинт**

В начало `backend/routers/invoices.py` добавить импорт: `from botocore.exceptions import ClientError`
(boto3 уже в зависимостях). Рядом с эндпоинтом — хелпер различения «не найдено»:

```python
def _is_not_found(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):           # in-memory-фикстура тестов
        return True
    if isinstance(exc, ClientError):                 # boto3 в проде
        return exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NoSuchBucket")
    return False
```

В `backend/routers/invoices.py` добавить после `reparse_document` (импорт `pdf_orientation` — лениво внутри функции, чтобы не тянуть pypdfium2 на старте):

```python
@router.post("/documents/{doc_id}/deskew-reparse")
async def deskew_reparse_document(doc_id: int, db: Session = Depends(get_db)):
    """Определить ориентацию страниц, выправить повёрнутые (raster) и переразобрать.
    Оригинал сохраняется в {s3_key}.orig; deskew всегда стартует от оригинала (идемпотентно)."""
    import pdf_orientation

    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if not doc.s3_key:
        raise HTTPException(status_code=400, detail="PDF недоступен в хранилище")
    if any(inv.verified for inv in doc.invoices):
        raise HTTPException(status_code=409, detail="Документ содержит подтверждённые СФ — снимите подтверждение перед коррекцией")

    orig_key = f"{doc.s3_key}.orig"
    # Источник — всегда оригинал: если бэкап есть, берём его. Различаем «нет бэкапа»
    # (ожидаемо → fallback) и транзиентный сбой S3 (→ 502): иначе при живом .orig, но
    # упавшем чтении, has_backup=False и upload_file(...orig_key) затрёт настоящий оригинал.
    try:
        source_bytes = download_file(orig_key)
        has_backup = True
    except Exception as e:
        if not _is_not_found(e):
            logger.exception(f"Deskew doc={doc_id}: ошибка чтения {orig_key}")
            raise HTTPException(status_code=502, detail="Хранилище временно недоступно")
        source_bytes = download_file(doc.s3_key)
        has_backup = False

    try:
        corrected, rotations = await pdf_orientation.deskew_pdf(source_bytes)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Deskew doc={doc_id}: ошибка коррекции")
        raise HTTPException(status_code=422, detail=f"Не удалось обработать PDF: {e}")

    if any(r % 360 for r in rotations):
        if not has_backup:
            upload_file(source_bytes, orig_key)   # одноразовый бэкап оригинала
        upload_file(corrected, doc.s3_key)        # перезапись основным ключом
        pdf_for_reparse = corrected
    else:
        pdf_for_reparse = source_bytes

    result = await _reparse_from_s3(doc, db, pdf_bytes=pdf_for_reparse)
    result["rotations_applied"] = rotations
    return result
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py -k deskew -v 2>&1"`
Expected: PASS (4 теста).

- [ ] **Step 5: Прогнать весь invoices-файл (нет регрессий)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py -v 2>&1"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/invoices.py backend/tests/integration/test_invoices.py
git commit -m "feat(deskew): эндпоинт deskew-reparse (.orig бэкап + идемпотентность)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Frontend — API + мутация

**Files:**
- Modify: `frontend/src/services/api/invoices.ts`
- Modify: `frontend/src/services/queries.ts`

- [ ] **Step 1: Метод API**

В `frontend/src/services/api/invoices.ts` рядом с `reparseDocument` (строка ~24) добавить:

```ts
  async deskewReparseDocument(docId: ID): Promise<DocumentDetail> {
    const { data } = await api.post<DocumentDetail>(
      `/invoices/documents/${docId}/deskew-reparse`
    );
    return data;
  },
```

- [ ] **Step 2: Мутация (зеркало useReparseDocument)**

В `frontend/src/services/queries.ts` после `useReparseDocument` (строка ~195) добавить:

```ts
export function useDeskewReparseDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: ID) => invoicesApi.deskewReparseDocument(docId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: qk.documents.detail(data.id) });
      qc.invalidateQueries({ queryKey: qk.documents.list(data.project_id) });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Документ выпрямлен и переразобран");
    },
  });
}
```

- [ ] **Step 3: Typecheck**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"`
Expected: без ошибок.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services/api/invoices.ts frontend/src/services/queries.ts
git commit -m "feat(deskew-fe): API-метод и мутация useDeskewReparseDocument

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Frontend — кнопки в ErrorDocsTab и Review

**Files:**
- Modify: `frontend/src/components/projects/ErrorDocsTab.tsx`
- Modify: `frontend/src/pages/Review.tsx`
- Modify: `frontend/src/test/handlers.ts`
- Modify: `frontend/src/components/projects/ErrorDocsTab.test.tsx`, `frontend/src/pages/Review.test.tsx`

- [ ] **Step 1: MSW-хендлер для нового роута**

В `frontend/src/test/handlers.ts` после строки с `reparse` (строка ~62) добавить:

```ts
  http.post("/api/invoices/documents/:id/deskew-reparse", () => HttpResponse.json(sampleDocument)),
```

- [ ] **Step 2: Кнопка в ErrorDocsTab**

В `frontend/src/components/projects/ErrorDocsTab.tsx`:
- импорт иконки: в строке импорта из `lucide-react` добавить `Crop` (используем как «выпрямить»);
- получить мутацию: после `const reparse = useReparseDocument();` (строка ~37) добавить
  `const deskew = useDeskewReparseDocument();` и в импорт из `@/services/queries` (строка 26)
  добавить `useDeskewReparseDocument`;
- кнопку добавить сразу после блока Reparse (после его `</Tooltip>`, перед блоком Delete, ~строка 154):

```tsx
                      {/* Deskew + reparse */}
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label="Выпрямить и переразобрать"
                              disabled={deskew.isPending || reparse.isPending}
                              onClick={() => deskew.mutate(doc.id)}
                            >
                              <Crop
                                size={14}
                                className={deskew.isPending && deskew.variables === doc.id ? "animate-pulse" : undefined}
                              />
                            </Button>
                          }
                        />
                        <TooltipContent>Выпрямить и переразобрать</TooltipContent>
                      </Tooltip>
```

- [ ] **Step 3: Кнопка в Review**

В `frontend/src/pages/Review.tsx`:
- в импорт из `@/services/queries` (строка ~23) добавить `useDeskewReparseDocument`;
- после `const reparse = useReparseDocument();` (строка ~43) добавить `const deskew = useDeskewReparseDocument();`;
- рядом с кнопкой «Переразобрать» (строки ~181-189) добавить вторую кнопку с теми же гардами:

```tsx
            <button
              type="button"
              onClick={() => { setUnitWarnings([]); deskew.mutate(docId); }}
              disabled={deskew.isPending || reparse.isPending || verify.isPending || unverify.isPending || documentLocked}
              title={documentLocked || verify.isPending || unverify.isPending ? "Сначала завершите или снимите подтверждение" : undefined}
              className="text-fg-secondary underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
            >
              Выпрямить и переразобрать
            </button>
```

ℹ️ `className` выше — буквальный класс кнопки «Переразобрать» из `Review.tsx`
(строки ~181-189); при реализации он подставлен в обе кнопки, чтобы стиль совпадал.

- [ ] **Step 4: Тесты фронта (зеркало reparse-тестов)**

В `frontend/src/components/projects/ErrorDocsTab.test.tsx` добавить тест (рядом с reparse-тестом, строка ~68):

```tsx
  it("calls deskew-reparse endpoint on button click", async () => {
    const onDeskew = vi.fn();
    server.use(
      http.post("/api/invoices/documents/:id/deskew-reparse", ({ params }) => {
        onDeskew(params.id);
        return HttpResponse.json(sampleDocument);
      }),
    );
    renderErrorDocsTab();  // тот же helper, что в reparse-тесте
    await userEvent.click(screen.getByRole("button", { name: /Выпрямить и переразобрать/i }));
    await waitFor(() => expect(onDeskew).toHaveBeenCalledWith("1"));
  });
```

В `frontend/src/pages/Review.test.tsx` — проверка наличия кнопки и её дизейбла при locked (зеркало строки ~143-144):

```tsx
    expect(screen.getByRole("button", { name: /Выпрямить и переразобрать/i })).toBeInTheDocument();
```

- [ ] **Step 5: Прогнать фронт-тесты + typecheck**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"`
Expected: PASS.
Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"`
Expected: без ошибок.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(deskew-fe): кнопки «Выпрямить и переразобрать» в ErrorDocsTab и Review

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Документация

**Files:**
- Modify: `docs/agent/pdf-parsing.md`

- [ ] **Step 1: Дописать раздел про коррекцию ориентации**

В `docs/agent/pdf-parsing.md` после раздела «Выбор движка» добавить:

```markdown
## On-demand коррекция ориентации (deskew-reparse)

Модуль `backend/pdf_orientation.py` + эндпоинт `POST /api/invoices/documents/{id}/deskew-reparse`
(кнопка «Выпрямить и переразобрать» в Review и ErrorDocsTab).

- **Детект:** vision-предзапрос в OpenRouter (`detect_rotations`, `AI_MODEL`, `max_tokens≈200`,
  `timeout≈30s`) — per-page поворот 0/90/180/270. Любой сбой парсинга → нули.
- **Коррекция:** селективный raster (`apply_rotations`) — `pypdfium2` перерисовывает ТОЛЬКО
  повёрнутые страницы (grayscale, native-aware DPI: `image_px_width/(page_width_pt/72)`, кап 300,
  пол 150), прямые переносятся как есть; сборка через `pikepdf`. У пересобранной страницы `/Rotate=0`.
- **Почему raster, а не `/Rotate`-флаг:** спайk 2026-06-15 — на mistral-ocr флаг давал
  стабильно-неверное количество (conf 0.72 > порога), raster — верное и conf ниже порога.
- **S3:** оригинал бэкапится в `{key}.orig`; deskew всегда стартует от оригинала (идемпотентно);
  исправленный PDF перезаписывает основной ключ.
- **Ограничение:** born-digital повёрнутый PDF деградирует от перерисовки (оригинал в `.orig`).
- **Долг:** синхронный запрос длинный (detect+reparse, worst-case ~6 мин) — поднять read-timeout
  фронт-прокси для роута; async-обёртка (фоновая задача+поллинг) осознанно отложена.
```

- [ ] **Step 2: Commit**

```bash
git add docs/agent/pdf-parsing.md
git commit -m "docs(pdf): раздел про deskew-reparse коррекцию ориентации

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Финальная проверка

- [ ] **Step 1: Линт**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1"`
Expected: без ошибок (ruff + eslint).

- [ ] **Step 2: Полный бэкенд + фронт**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration && just test-backend-unit && just test-frontend 2>&1"`
Expected: PASS.

- [ ] **Step 3: Перенести спеку и план в ветку (для истории)**

```bash
git add docs/superpowers/specs/2026-06-15-pdf-orientation-deskew-design.md docs/superpowers/plans/2026-06-15-pdf-orientation-deskew.md
git commit -m "docs(deskew): спека и план фичи коррекции ориентации

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Селективный raster (только повёрнутые, прямые как есть) → Task 2 ✓
- native-aware DPI + кап/пол + допущение → Task 1 ✓
- target_dpi дважды (render scale + Image.save resolution) → Task 2 Step 3 ✓
- /Rotate=0 на пересобранной странице → Task 2 (тест + реализация) ✓
- vision-детект, max_tokens≈200, timeout≈30с, guard MAX_DESKEW_PAGES→413, мусор→нули → Task 3 ✓
- deskew_pdf short-circuit на нулях (исходные байты) → Task 4 ✓
- `_reparse_from_s3` с опциональным pdf_bytes, без смены поведения reparse → Task 5 ✓
- Эндпоинт: гарды 404/400/409, .orig бэкап, deskew от оригинала, перезапись, rotations_applied → Task 6 ✓
- Контракт ошибок: render/битый PDF → 422; сбой vision (таймаут/не-2xx) → 502 и БЕЗ переразбора; транзиентный сбой S3 → 502; «не найдено» для .orig → fallback → Task 3/6 ✓
- Идемпотентность через .orig (повторный клик стартует от оригинала) → Task 6 ✓
- Frontend: мутация (те же ключи инвалидации), кнопки в ErrorDocsTab и Review, тесты → Task 7, 8 ✓
- Зависимости pypdfium2+Pillow+pikepdf → Task 0 ✓
- Документация + born-digital ограничение + долг по async/таймауту → Task 9 ✓
- Тест знака поворота на сгенерированной реальной фикстуре (по пикселям) → Task 2 ✓

**Placeholder scan:** один литеральный placeholder `className=""` в Task 8 Step 3 — помечен ⚠️ как ОБЯЗАТЕЛЬНЫЙ к замене на конкретный `className` соседней кнопки «Переразобрать» из `Review.tsx` (не оставлять пустым). Других заглушек в шагах нет.

**Type consistency:** `detect_rotations`/`render_pages_for_detect`/`apply_rotations`/`deskew_pdf`/`_target_dpi`/`_page_to_image_pdf` — имена едины между задачами и тестами. `_reparse_from_s3(doc, db, pdf_bytes=None)` — сигнатура совпадает в Task 5 (определение) и Task 6 (вызов). `rotations_applied` — ключ ответа, един в эндпоинте и тестах. `useDeskewReparseDocument`/`deskewReparseDocument` — едины во фронте.

**Замечание по async-тестам:** unit-тесты `detect_rotations`/`deskew_pdf` помечены `@pytest.mark.asyncio`; в проекте `asyncio_mode=auto` (см. вывод pytest в спайке), маркер избыточен, но безвреден.