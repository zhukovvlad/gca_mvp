"""On-demand коррекция ориентации страниц PDF: vision-детект + селективный raster.

Используется эндпоинтом deskew-reparse. См. спеку
docs/superpowers/specs/2026-06-15-pdf-orientation-deskew-design.md.
"""
import io
import json
import logging
import re
from decimal import Decimal

import anyio
import httpx
import pikepdf
import pypdfium2 as pdfium

import llm
from processing import PermanentError, TransientError

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


def _page_to_image_pdf(src_pdf: pdfium.PdfDocument, index: int, render_rotation: int, target_dpi: int) -> bytes:
    """Отрендерить страницу с заданным поворотом pdfium (grayscale) и обернуть в 1-страничный PDF.

    render_rotation — это уже ОТМЕНЯЮЩИЙ поворот, который скармливается pypdfium2
    `render(rotation=...)` (по часовой стрелке). target_dpi используется ДВАЖДЫ: как scale
    рендера и как resolution записи image→PDF, иначе Pillow пишет 72 DPI/пиксель и mediabox раздувается.
    """
    page = src_pdf[index]
    img = page.render(scale=target_dpi / 72.0, rotation=render_rotation, grayscale=True).to_pil()
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=float(target_dpi))
    return out.getvalue()


def apply_rotations(pdf_bytes: bytes, rotations: list[int]) -> bytes:
    """Селективная raster-коррекция. `rotations[i]` — НА СКОЛЬКО градусов по часовой стрелке
    страница повёрнута (выход detect_rotations); чтобы её выпрямить, рендерим с ОТМЕНЯЮЩИМ
    поворотом (360 - r) % 360 (pypdfium2.render крутит по часовой). Прямые (r==0) переносим
    как есть. Пересобираем смешанный документ через pikepdf."""
    src_pikepdf = pikepdf.open(io.BytesIO(pdf_bytes))
    src_pdfium = pdfium.PdfDocument(pdf_bytes)
    result = pikepdf.new()
    keepalive = []  # foreign-PDF должны жить до result.save() (иначе copy_foreign может оборваться)
    for i, page in enumerate(src_pikepdf.pages):
        detected = rotations[i] % 360 if i < len(rotations) else 0
        if detected == 0:
            result.pages.append(page)  # как есть, без перерисовки
            continue
        render_rotation = (360 - detected) % 360  # отменяем поворот страницы
        target_dpi = _target_dpi(page)
        img_pdf = pikepdf.open(io.BytesIO(_page_to_image_pdf(src_pdfium, i, render_rotation, target_dpi)))
        keepalive.append(img_pdf)
        result.pages.append(img_pdf.pages[0])
        last = result.pages[-1]
        if "/Rotate" in last:          # пиксели уже прямые — флаг не нужен
            del last["/Rotate"]        # словарный интерфейс (надёжнее атрибутного)
    buf = io.BytesIO()
    result.save(buf)
    return buf.getvalue()


DETECT_TIMEOUT = 30.0
_DETECT_PROMPT = (
    "На вход — страницы PDF по порядку. Для КАЖДОЙ страницы определи, НА СКОЛЬКО градусов "
    "ПО ЧАСОВОЙ СТРЕЛКЕ она повёрнута относительно нормальной (вертикально читаемой) "
    "ориентации: 0, 90, 180 или 270. Если страница уже вертикальна — верни 0. "
    "Верни ТОЛЬКО JSON-массив целых чисел длиной по числу страниц, например [0,90,0]."
)


def render_pages_for_detect(pdf_bytes: bytes, long_side: int = 768) -> list[bytes]:
    """Уменьшенные JPEG-страницы для vision-детекта (ориентации хватает низкого разрешения).

    Лимит MAX_DESKEW_PAGES проверяем ЗДЕСЬ, ДО цикла рендера — иначе гигантский PDF
    исчерпывает CPU/память на растеризации всех страниц ещё до того, как
    detect_rotations успевает отклонить его 413-м (FIX 2). Проверка в detect_rotations
    остаётся как defense-in-depth (безвредный дублирующий guard).
    """
    pdf = pdfium.PdfDocument(pdf_bytes)
    if len(pdf) > MAX_DESKEW_PAGES:
        raise PermanentError(f"Слишком много страниц для коррекции (> {MAX_DESKEW_PAGES})",
                             http_status=413)
    images: list[bytes] = []
    for page in pdf:
        w, h = page.get_size()
        scale = long_side / max(w, h)
        img = page.render(scale=scale, grayscale=True).to_pil()
        out = io.BytesIO()
        img.convert("L").save(out, format="JPEG", quality=70)
        images.append(out.getvalue())
    return images


async def detect_rotations(images: list[bytes]) -> tuple[list[int], Decimal]:
    """Один vision-запрос (через llm.get_provider()): per-page поворот 0/90/180/270 и стоимость вызова.

    Транспортный сбой/таймаут/не-2xx → TransientError (detect не оплачен, cost не читается);
    слишком много страниц → PermanentError. §2.5: битый envelope платного 200 (нет `choices`
    и т.п.) → PermanentError с cost/paid_calls из провайдера (НЕ тихие нули). Envelope цел,
    но СОДЕРЖИМОЕ не парсится → нули (безопасная деградация уровня контента), cost
    из usage возвращается (вызов был платным).
    """
    n = len(images)
    if n > MAX_DESKEW_PAGES:
        # http_status=413 — прежний код эндпоинта; на S0 доходит до клиента (AC-S0-8).
        raise PermanentError(f"Слишком много страниц для коррекции (> {MAX_DESKEW_PAGES})",
                             http_status=413)
    try:
        resp = await llm.get_provider().vision_completion(
            system=None, user_text=_DETECT_PROMPT,
            attachment=llm.ImagesAttachment(images=tuple(images)),
            max_tokens=200,
            timeout=httpx.Timeout(DETECT_TIMEOUT, connect=5.0),
        )
    except llm.LLMProviderError as e:
        # НЕ деградируем в нули (иначе переразберём оригинал под видом исправленного).
        # §2.5: битый envelope платного 200 — тоже ошибка (retryable=False, cost, paid=1),
        # а не тихие нули; биллинг доезжает через cost_usd/paid_calls исключения.
        logger.warning(f"detect_rotations: vision-запрос упал: {e}")
        if e.retryable:
            raise TransientError("Сервис распознавания ориентации недоступен",
                                 http_status=502, cost_usd=e.cost_usd,
                                 paid_calls=e.paid_calls) from e
        raise PermanentError("Сервис распознавания ориентации отклонил запрос",
                             http_status=502, cost_usd=e.cost_usd,
                             paid_calls=e.paid_calls) from e

    cost = resp.cost_usd
    content_text = resp.content
    # envelope цел: непарсящееся СОДЕРЖИМОЕ → нули (деградация уровня контента)
    try:
        m = re.search(r"\[[\d,\s]*\]", content_text)
        nums = json.loads(m.group(0)) if m else []
        allowed = {0, 90, 180, 270}
        rots = [v % 360 if (v % 360) in allowed else 0 for v in nums[:n]]
    except Exception:  # noqa: BLE001 — кривое содержимое не должно ронять вызывающего
        rots = []
    rots += [0] * (n - len(rots))
    logger.info(f"detect_rotations: n={n}, rotations={rots}, cost=${cost}, raw={content_text[:300]!r}")
    return rots, cost


async def deskew_pdf(pdf_bytes: bytes) -> tuple[bytes, list[int], Decimal]:
    """render-for-detect → detect → селективный raster. Возвращает (bytes, rotations, detect_cost).

    detect уже оплачен к моменту apply_rotations; сбой растеризации оборачиваем в
    TransientError с detect-cost, чтобы стоимость не потерялась в generic-ветке (F3, §2.3).
    Рендер/раст CPU-bound — уходят в поток через anyio.to_thread (S0-6, event loop свободен).
    """
    images = await anyio.to_thread.run_sync(render_pages_for_detect, pdf_bytes)
    rotations, cost = await detect_rotations(images)
    if not any(r % 360 for r in rotations):
        return pdf_bytes, rotations, cost          # short-circuit, без перерисовки
    try:
        corrected = await anyio.to_thread.run_sync(apply_rotations, pdf_bytes, rotations)
    except Exception as exc:  # noqa: BLE001 — detect оплачен, cost не теряем
        raise TransientError(f"Не удалось применить коррекцию ориентации: {exc}",
                             cost_usd=cost, paid_calls=1) from exc
    return corrected, rotations, cost
