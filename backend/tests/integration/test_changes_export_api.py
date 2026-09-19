"""Маршрут, кнопка, справочник — `GET /api/v1/tenders/{tender_id}/changes-export`
(спека 2026-09-16-tender-changes-export-design.md §2.1, §2.11; план фичи, Task 5).

Хендлер сам не считает ничего — он только связывает чтение `crud.changes_export.
load_book` (задача 3), преобразование `SheetInput -> Sheet` через `services.
changes_export.build_sheet` (задача 2 — обязательная связка типов, которой не
называет план буквально, но без которой `load_book` и `build_changes_export` не
компонуются: см. отчёт задачи 5) и сборку книги `services.excel_changes_export.
build_changes_export` (задача 4) — все три уже проверены своими наборами тестов.
Здесь проверяется HTTP-контракт поверх них: маршрут, права, коды отказов, число и
имена листов, форма ответа и то, что роутер не заводит собственной арифметики.

Сметы строятся НАСТОЯЩИМ `import_round` — тем же образцом `chaptered`/`grid`, что
`test_stage_summary_api.py`; matching здесь не нужен (книга не разбирает каталог по
позициям в этих тестах, только собирает листы и заголовок), поэтому `import_and_match`
из `test_position_drilldown_api.py` не используется.
"""
from __future__ import annotations

from io import BytesIO
from urllib.parse import quote

import openpyxl
import pytest

from crud import changes_export as crud_changes_export
from services import changes_export as changes_export_sheet
from services.category_resolution import CategoryResolver
from services.excel_changes_export import build_changes_export
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.integration.test_stage_summary_api import chaptered
from tests.payloads import round_payload

pytestmark = pytest.mark.integration

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _build_tender(db_session, factories, rounds: dict[int, list[dict]], **tender_kwargs):
    """Тендер с раундами `rounds` = {stage_no: [chaptered(...), ...]}, импортированными
    НАСТОЯЩИМ `import_round` (тот же приём, что `grid` в `test_stage_summary_api.py`)."""
    tender = factories.TenderFactory.create(**tender_kwargs)
    round_objs = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in rounds}
    db_session.flush()
    for n, proposals in rounds.items():
        import_round(db_session, tender_round=round_objs[n], data=round_payload(proposals),
                     parser_version="4.0.0", import_job_id=None, replace=False,
                     unit_resolver=UnitResolver(db_session),
                     category_resolver=CategoryResolver.from_db(db_session))
    db_session.flush()
    return tender


def _two_stage_tender(db_session, factories, **tender_kwargs):
    """Простейший сравнимый тендер: один участник, два этапа — годится для
    тестов, которым нужен только успешный ответ (роль, заголовок файла, форма
    ответа), а не состав книги."""
    return _build_tender(
        db_session, factories,
        {1: [chaptered({"1": ("6", "120.00")}, total="120.00")],
         2: [chaptered({"1": ("6", "90.00")}, total="90.00")]},
        **tender_kwargs,
    )


def _workbook_matrix(content: bytes) -> list[tuple[str, list[list]]]:
    """Содержимое книги как данные, а не как формат: список (имя листа, строки
    значений). Используется, чтобы сравнить книгу эндпоинта с книгой, собранной
    напрямую `load_book`+`build_changes_export` (A5.8), не полагаясь на байтовое
    равенство — `Workbook()` кладёт метку времени создания в `docProps/core.xml`,
    и два независимых вызова `workbook_bytes` дают разные байты на ОДНИХ и тех же
    данных."""
    wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    return [(name, [[cell.value for cell in row] for row in wb[name].iter_rows()]) for name in wb.sheetnames]


class TestRouteAndMediaType:
    """A5.1: маршрут, тело 200 — байты xlsx с правильным media-type."""

    def test_200_returns_xlsx_bytes_readable_by_openpyxl(self, client, db_session, factories):
        tender = _two_stage_tender(db_session, factories)

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")

        assert response.status_code == 200
        assert response.headers["content-type"] == XLSX_MEDIA_TYPE
        wb = openpyxl.load_workbook(BytesIO(response.content))
        assert len(wb.sheetnames) == 1


class TestFilenameEncoding:
    """A5.2: имя файла — номер тендера через `safe_filename_part`, форма
    `filename*=UTF-8''<percent>`; номер с `/` не ломает путь, кириллица не искажается."""

    def test_slash_in_tender_number_is_percent_encoded_not_broken(self, client, db_session, factories):
        tender = _two_stage_tender(db_session, factories, tender_number="12/2025")

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")

        assert response.status_code == 200
        expected_filename = "Изменения КП 12-2025.xlsx"
        expected_header = f"attachment; filename*=UTF-8''{quote(expected_filename)}"
        assert response.headers["content-disposition"] == expected_header
        # ASCII-форма `filename=` не заводится вовсе (спека §2.11) — русские
        # буквы искажались бы именно в ней.
        assert "filename=" not in response.headers["content-disposition"].split(";")[0]


class TestReadAccess:
    """A5.3: читать вправе и `admin`, и `member` — отдельной зависимости на роль
    нет. Неаутентифицированный запрос отвергается тем же механизмом, что и
    остальные ручки (`test_auth_coverage.py::test_endpoint_requires_auth`
    покрывает ЭТОТ маршрут автоматически перечислением `app.routes` — второй
    копии той проверки здесь не заводится)."""

    @pytest.mark.parametrize("role", ["admin", "member"])
    def test_role_can_read(self, client, db_session, factories, role):
        from models import UserRole

        tender = _two_stage_tender(db_session, factories)
        client.auth_state["role"] = getattr(UserRole, role)

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")

        assert response.status_code == 200


class TestRefusals:
    """A5.4: тендера нет → 404 tender_not_found; участников с двумя и более
    сметами нет → 422 no_comparable_participants; тело в обоих случаях — объект."""

    def test_unknown_tender_is_404_with_object_detail(self, client, db_session):
        response = client.get("/api/v1/tenders/999999999/changes-export")

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "tender_not_found"

    def test_no_comparable_participants_is_422_with_object_detail(self, client, db_session, factories):
        # Один раунд, один участник — MIN_STAGES не достигнут (2), сравнивать нечего.
        tender = _build_tender(
            db_session, factories,
            {1: [chaptered({"1": ("6", "120.00")}, total="120.00")]},
        )

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "no_comparable_participants"


class TestSheetCount:
    """A5.5: листов столько, сколько участников с двумя и более сметами —
    участник с одним раундом в книгу не попадает."""

    def test_sheet_count_matches_comparable_participants_only(self, db_session, factories, client):
        # А — 2 раунда, Б — 3 раунда, В — только 1 раунд (round 1).
        tender = _build_tender(
            db_session, factories,
            {
                1: [chaptered({"1": ("6", "120.00")}, total="120.00", inn="7700000001", title="ООО А"),
                    chaptered({"1": ("6", "80.00")}, total="80.00", inn="7700000002", title="ООО Б"),
                    chaptered({"1": ("6", "50.00")}, total="50.00", inn="7700000003", title="ООО В")],
                2: [chaptered({"1": ("6", "110.00")}, total="110.00", inn="7700000001", title="ООО А"),
                    chaptered({"1": ("6", "70.00")}, total="70.00", inn="7700000002", title="ООО Б")],
                3: [chaptered({"1": ("6", "60.00")}, total="60.00", inn="7700000002", title="ООО Б")],
            },
        )

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")

        assert response.status_code == 200
        wb = openpyxl.load_workbook(BytesIO(response.content))
        assert len(wb.sheetnames) == 2
        assert set(wb.sheetnames) == {"ООО А", "ООО Б"}


class TestSheetNamesSanitizedTruncatedDeduplicated:
    """A5.6: имена листов очищены/обрезаны до 31 знака, совпадающие 31-знаковые
    префиксы разведены суффиксом (проверено на уровне книги, а не только на
    уровне `sheet_names` — задача 2 её уже покрывает изолированно)."""

    def test_long_and_colliding_names_stay_distinct_and_within_limit(self, db_session, factories, client):
        prefix = "О" * 31
        title_a = f"{prefix} Альфа"
        title_b = f"{prefix} Бета"
        tender = _build_tender(
            db_session, factories,
            {
                1: [chaptered({"1": ("6", "100.00")}, total="100.00", inn="7700000001", title=title_a),
                    chaptered({"1": ("6", "100.00")}, total="100.00", inn="7700000002", title=title_b)],
                2: [chaptered({"1": ("6", "90.00")}, total="90.00", inn="7700000001", title=title_a),
                    chaptered({"1": ("6", "90.00")}, total="90.00", inn="7700000002", title=title_b)],
            },
        )

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")

        assert response.status_code == 200
        wb = openpyxl.load_workbook(BytesIO(response.content))
        assert len(wb.sheetnames) == 2
        assert len(set(wb.sheetnames)) == 2
        assert all(len(name) <= 31 for name in wb.sheetnames)
        # Первое имя — голый префикс (31 знак, ничего резать не пришлось);
        # второе развело совпадение суффиксом и обрезалось ПОСЛЕ него (план,
        # решение 6) — обязано остаться в пределах 31 и нести суффикс.
        assert wb.sheetnames[0] == prefix
        assert wb.sheetnames[1].endswith(" (2)")


class TestResponseIsPlainBytes:
    """A5.7: ответ — `bytes` целиком в памяти, не `StreamingResponse` с файловым
    объектом. `Response(content=bytes, ...)` в отличие от `StreamingResponse`
    несёт заголовок `Content-Length`, а тело доступно ответу целиком сразу."""

    def test_content_length_header_matches_body_size(self, client, db_session, factories):
        tender = _two_stage_tender(db_session, factories)

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")

        assert response.status_code == 200
        assert "content-length" in {k.lower() for k in response.headers}
        assert int(response.headers["content-length"]) == len(response.content)


class TestNoOwnArithmetic:
    """A5.8: эндпоинт зовёт `load_book` и `build_changes_export` и не считает
    ничего своего — книга эндпоинта и книга, собранная теми же двумя вызовами
    напрямую в тесте, обязаны нести ОДНИ И ТЕ ЖЕ данные (сравнение по
    содержимому ячеек, а не по байтам: `Workbook()` метит книгу временем
    создания, и два независимых вызова дают разные байты на одних данных)."""

    def test_endpoint_output_matches_direct_load_book_and_build(self, client, db_session, factories):
        tender = _build_tender(
            db_session, factories,
            {
                1: [chaptered({"1": ("6", "120.00"), "2": ("2", "60.00")}, total="180.00", inn="7700000001", title="ООО А"),
                    chaptered({"1": ("6", "200.00")}, total="200.00", inn="7700000002", title="ООО Б")],
                2: [chaptered({"1": ("6", "96.00"), "2": ("2", "0")}, total="120.00", inn="7700000001", title="ООО А"),
                    chaptered({"1": ("6", "150.00")}, total="150.00", inn="7700000002", title="ООО Б")],
            },
        )

        response = client.get(f"/api/v1/tenders/{tender.id}/changes-export")
        assert response.status_code == 200

        raw_sheets = crud_changes_export.load_book(db_session, tender.id)
        sheets = [changes_export_sheet.build_sheet(raw) for raw in raw_sheets]
        direct_content = build_changes_export(
            sheets, tender_header={"tender_number": tender.tender_number, "tender_title": tender.title},
        )

        assert _workbook_matrix(response.content) == _workbook_matrix(direct_content)


class TestScreensDocRefersToButton:
    """A5.11: `docs/reference/screens.md` §8 получает описание кнопки и состава
    книги этим же коммитом; заголовок `## 8. Тендеры` не меняется (на нём стоит
    проверка стража `check_agents_index.py::check_13`)."""

    def test_section_8_header_unchanged_and_describes_the_button_and_book(self):
        from pathlib import Path

        text = Path(__file__).resolve().parents[3] / "docs" / "reference" / "screens.md"
        content = text.read_text(encoding="utf-8")

        assert "## 8. Тендеры" in content
        section_8 = content.split("## 8. Тендеры", 1)[1]
        assert "Изменения КП" in section_8
        assert "no_comparable_participants" in section_8
        assert "двумя и более сметами" in section_8
