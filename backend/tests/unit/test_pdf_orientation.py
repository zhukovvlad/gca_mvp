import io
from decimal import Decimal

import httpx
import pikepdf
import pypdfium2 as pdfium
import pytest
import respx
from PIL import Image

import pdf_orientation as po
from processing import PermanentError, TransientError

# Захватываем настоящий AsyncClient.send на импорте модуля (до autouse-гарда
# block_real_openrouter из conftest, который рубит любые вызовы к openrouter.ai на
# уровне send). respx работает на transport-уровне — через настоящий send проходит,
# поэтому в respx-тестах восстанавливаем его (тот же приём, что в фикстуре mock_openrouter).
_REAL_SEND = httpx.AsyncClient.send


@pytest.fixture
def _allow_respx(monkeypatch):
    """Восстанавливает настоящий AsyncClient.send, чтобы respx.mock мог перехватывать запросы."""
    monkeypatch.setattr(httpx.AsyncClient, "send", _REAL_SEND)


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
    return sum(band.get_flattened_data()) / (band.size[0] * band.size[1]) < 64  # тёмная полоса сверху


def _render_first_page_png(pdf_bytes: bytes) -> bytes:
    pdf = pdfium.PdfDocument(pdf_bytes)
    img = pdf[0].render(scale=1.0).to_pil()
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_apply_rotations_sign_makes_upright():
    """Контракт: вход = на сколько страница ПОВЁРНУТА по часовой стрелке; apply её выпрямляет.

    `_sideways_scan_pdf` — страница, повёрнутая на 90° CW (маркер-верх уехал вправо).
    Значит detect вернёт 90, и apply([90]) обязан вернуть маркер наверх (отменив поворот)."""
    sideways = _sideways_scan_pdf()
    assert not _top_strip_is_dark(_render_first_page_png(sideways))  # до: не сверху
    corrected = po.apply_rotations(sideways, [90])  # «повёрнута на 90° CW» → apply отменяет
    assert _top_strip_is_dark(_render_first_page_png(corrected))     # после: сверху


def test_apply_rotations_resets_rotate_flag():
    """У пересобранной image-страницы /Rotate == 0 (не наследуется)."""
    src = pikepdf.open(io.BytesIO(_sideways_scan_pdf()))
    src.pages[0].Rotate = 90  # искусственный ненулевой флаг на исходнике
    buf = io.BytesIO()
    src.save(buf)
    corrected = po.apply_rotations(buf.getvalue(), [90])
    out = pikepdf.open(io.BytesIO(corrected))
    assert int(out.pages[0].get("/Rotate", 0)) == 0


def test_apply_rotations_zero_delta_keeps_page_untouched():
    """Страница с delta==0 переносится как есть (не перерисовывается)."""
    two_pages = pikepdf.new()
    src = pikepdf.open(io.BytesIO(_sideways_scan_pdf()))
    two_pages.pages.append(src.pages[0])
    two_pages.pages.append(src.pages[0])
    buf = io.BytesIO()
    two_pages.save(buf)
    corrected = po.apply_rotations(buf.getvalue(), [90, 0])
    # стр.1 перерисована (маркер сверху), стр.2 — исходная боковая (маркер не сверху)
    assert _top_strip_is_dark(_render_first_page_png(corrected))
    pdf = pdfium.PdfDocument(corrected)
    p2 = pdf[1].render(scale=1.0).to_pil()
    b = io.BytesIO()
    p2.save(b, format="PNG")
    assert not _top_strip_is_dark(b.getvalue())


def test_apply_rotations_two_rotated_pages():
    """≥2 повёрнутых страниц: обе выпрямлены, foreign-ссылки не оборвались до save."""
    src = pikepdf.open(io.BytesIO(_sideways_scan_pdf()))
    two = pikepdf.new()
    two.pages.append(src.pages[0])
    two.pages.append(src.pages[0])
    buf = io.BytesIO()
    two.save(buf)
    corrected = po.apply_rotations(buf.getvalue(), [90, 90])
    out = pikepdf.open(io.BytesIO(corrected))
    assert len(out.pages) == 2
    pdf = pdfium.PdfDocument(corrected)
    for idx in range(2):
        img = pdf[idx].render(scale=1.0).to_pil()
        b = io.BytesIO()
        img.save(b, format="PNG")
        assert _top_strip_is_dark(b.getvalue())  # обе выпрямлены


def _openrouter_reply(text: str, cost: str | None = None) -> httpx.Response:
    """Ответ OpenRouter с опциональным usage.cost (S0-9: detect — платный вызов)."""
    body = {"choices": [{"message": {"content": text}}]}
    if cost is not None:
        body["usage"] = {"cost": cost}
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_parses_list(_allow_respx):
    """Разбор корректного JSON-массива поворотов + чтение cost из usage (S0-9)."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=_openrouter_reply("Вот повороты: [0, 90, 270]", cost="0.0012")
    )
    rots, cost = await po.detect_rotations([b"img0", b"img1", b"img2"])
    assert rots == [0, 90, 270]
    assert cost == Decimal("0.0012")


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_nan_cost_clamped_to_zero(_allow_respx):
    """FIX B: `usage.cost = "NaN"` парсится Decimal-ом без ошибки, но неклэмпленный
    NaN испортил бы накопленный parse_cost_usd — клэмп в 0."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=_openrouter_reply("Вот повороты: [0, 90]", cost="NaN")
    )
    rots, cost = await po.detect_rotations([b"img0", b"img1"])
    assert rots == [0, 90]
    assert cost == Decimal(0)


@pytest.mark.asyncio
@respx.mock
async def test_broken_envelope_raises_permanent(_allow_respx):
    """§2.5: битый envelope платного 200 (нет `choices`) → PermanentError с cost, НЕ тихие нули."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"usage": {"cost": 0.01}}))
    with pytest.raises(PermanentError, match="отклонил запрос") as ei:
        await po.detect_rotations([b"jpeg1"])
    assert ei.value.cost_usd == Decimal("0.01")


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_garbage_to_zeros(_allow_respx):
    """Непарсящееся содержимое → нули, но cost из usage всё равно возвращается (вызов оплачен)."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=_openrouter_reply("не смог", cost="0.0005")
    )
    rots, cost = await po.detect_rotations([b"img0", b"img1"])
    assert rots == [0, 0]  # длина = числу страниц, всё в 0
    assert cost == Decimal("0.0005")


def test_render_pages_for_detect_rejects_too_many_pages_before_rendering(monkeypatch):
    """FIX 2: лимит MAX_DESKEW_PAGES проверяется СРАЗУ после открытия PdfDocument, ДО
    цикла рендера — иначе гигантский PDF исчерпывает CPU/память на растеризации ВСЕХ
    страниц ещё до того, как лимит успевает отклонить его 413-м. Подменяем
    `pdfium.PdfDocument`, чтобы вернуть фейковый документ с `len() > MAX_DESKEW_PAGES`,
    но итерация по нему (т.е. рендер хоть одной страницы) считается провалом теста —
    `__iter__` бросает, если до него вообще дошло исполнение."""

    class _FakePage:
        """Страница, чей рендер не должен вызываться (заглушка, здесь не используется)."""

        def render(self, *a, **k):
            """Не должен быть вызван — обозначен для наглядности контракта фейка."""
            raise AssertionError("render() вызван — страница была растеризована до page-limit guard")

    class _FakeTooBigDoc:
        """Фейковый pdfium.PdfDocument: длина превышает лимит, итерация запрещена."""

        def __len__(self):
            """Возвращает число страниц больше MAX_DESKEW_PAGES."""
            return po.MAX_DESKEW_PAGES + 1

        def __iter__(self):
            """Не должна вызываться — рендер обязан быть отклонён ДО цикла по страницам."""
            raise AssertionError("PdfDocument был проитерирован — рендер начался до page-limit guard")

    monkeypatch.setattr(po.pdfium, "PdfDocument", lambda pdf_bytes: _FakeTooBigDoc())

    with pytest.raises(PermanentError) as ei:
        po.render_pages_for_detect(b"%PDF-fake")
    assert ei.value.http_status == 413


@pytest.mark.asyncio
async def test_detect_rotations_too_many_pages():
    """Слишком много страниц → PermanentError с http_status=413 (было HTTPException до Task 7)."""
    with pytest.raises(PermanentError) as ei:
        await po.detect_rotations([b"x"] * (po.MAX_DESKEW_PAGES + 1))
    assert ei.value.http_status == 413


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_upstream_500_raises_502(_allow_respx):
    """Транспортный сбой/не-2xx → TransientError с http_status=502 (detect не оплачен)."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(return_value=httpx.Response(500))
    with pytest.raises(TransientError) as ei:
        await po.detect_rotations([b"x"])
    assert ei.value.http_status == 502
    assert ei.value.cost_usd == Decimal(0)  # сбой до чтения usage — cost не читается


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_upstream_503_raises_transient(_allow_respx):
    """FIX F: 503 (>=500) остаётся транзиентной (ретраебельно на S2), http_status=502 наружу."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(return_value=httpx.Response(503))
    with pytest.raises(TransientError) as ei:
        await po.detect_rotations([b"x"])
    assert ei.value.http_status == 502


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_upstream_400_raises_permanent(_allow_respx):
    """FIX F: не-retriable 4xx (не 408/429) — upstream отклонил запрос по содержимому,
    ретраить бессмысленно → PermanentError, а не TransientError (S2 retry policy).
    http_status=502 наружу не меняется (S0 контракт эндпоинта)."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(return_value=httpx.Response(400))
    with pytest.raises(PermanentError) as ei:
        await po.detect_rotations([b"x"])
    assert ei.value.http_status == 502


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_upstream_429_still_transient(_allow_respx):
    """FIX F: 429 (rate limit) остаётся транзиентной, несмотря на то что это 4xx."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(return_value=httpx.Response(429))
    with pytest.raises(TransientError) as ei:
        await po.detect_rotations([b"x"])
    assert ei.value.http_status == 502


@pytest.mark.asyncio
@respx.mock
async def test_detect_rotations_prose_around_array(_allow_respx):
    """Число-подобная проза вокруг JSON-массива не путает парсер."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=_openrouter_reply("Страница 1 и 2: ответ [90, 0]")
    )
    rots, _cost = await po.detect_rotations([b"a", b"b"])
    assert rots == [90, 0]  # «1»,«2» из прозы не попали


@pytest.mark.asyncio
async def test_deskew_pdf_all_zeros_returns_original(monkeypatch):
    """Все нули → исходные байты без перерисовки (идентичность), cost прокидывается наружу."""
    original = _sideways_scan_pdf()

    async def fake_detect(images):
        """Возвращает (rotations, cost) — новый контракт detect_rotations (Task 7)."""
        return [0] * len(images), Decimal("0.001")
    monkeypatch.setattr(po, "detect_rotations", fake_detect)

    out_bytes, rots, cost = await po.deskew_pdf(original)
    assert rots == [0]
    assert out_bytes == original  # тот же объект байтов, без перерисовки
    assert cost == Decimal("0.001")


@pytest.mark.asyncio
async def test_deskew_pdf_applies_rotation(monkeypatch):
    """Ненулевой поворот → apply_rotations вызван, cost detect прокинут наружу."""
    original = _sideways_scan_pdf()

    async def fake_detect(images):
        """Возвращает (rotations, cost) — новый контракт detect_rotations (Task 7)."""
        return [90] * len(images), Decimal("0.002")
    monkeypatch.setattr(po, "detect_rotations", fake_detect)

    out_bytes, rots, cost = await po.deskew_pdf(original)
    assert rots == [90]
    assert out_bytes != original
    assert cost == Decimal("0.002")
    assert _top_strip_is_dark(_render_first_page_png(out_bytes))  # выпрямлено


@pytest.mark.asyncio
async def test_deskew_pdf_apply_failure_carries_detect_cost(monkeypatch):
    """Сбой apply_rotations ПОСЛЕ оплаченного detect → TransientError несёт detect cost (F3)."""
    original = _sideways_scan_pdf()

    async def fake_detect(images):
        """detect «нашёл» поворот, но растеризация ниже упадёт."""
        return [90] * len(images), Decimal("0.003")
    monkeypatch.setattr(po, "detect_rotations", fake_detect)

    def boom_apply(pdf_bytes, rotations):
        """Эмулирует сбой растеризации (например, битый PDF)."""
        raise RuntimeError("raster boom")
    monkeypatch.setattr(po, "apply_rotations", boom_apply)

    with pytest.raises(TransientError) as ei:
        await po.deskew_pdf(original)
    assert ei.value.cost_usd == Decimal("0.003")
    assert ei.value.paid_calls == 1
