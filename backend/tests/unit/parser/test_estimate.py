"""Тесты оркестратора `parse_estimate` на реальной раскладке сметы ГП.

Два источника данных:

* `fixtures/gp_estimate_fixture.xlsx` — обезличенная копия реального образца
  (фаза 0). Коммитится, работает в CI, на нём держатся основные проверки.
* `samples/` — сам реальный образец. В репозиторий не попадает (AGENTS.md §9),
  тесты на нём пропускаются, если каталога нет. Их задача — подтвердить, что
  обезличивание не изменило поведения парсера.

Числа в ожиданиях — не «что получилось», а замеры фазы 0
(`docs/phase0-input-data.md`): 2576 строк позиций, 1828 расценённых строк с
заполненным «Предлагаемым количеством».
"""
from __future__ import annotations

from pathlib import Path

import pytest

from parser import PARSER_VERSION, EstimateParseError, parse_estimate, parse_worksheet
from parser.postprocess import BASELINE_MISSING_TITLE

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "gp_estimate_fixture.xlsx"
SAMPLES_DIR = REPO_ROOT / "samples"

# Замеры фазы 0 на реальном образце (docs/phase0-input-data.md).
EXPECTED_POSITIONS = 2576
EXPECTED_PRICED_ROWS = 1828

# Стоимости в файле округлены до копеек, поэтому произведение
# «цена за единицу × количество» сходится с точностью до копейки, а не побитово.
# Допуск нужен именно такой: перепутанные колонки дали бы расхождение на порядки,
# и абсолютная копейка их не спрячет.
KOPECK = 0.01


def _real_sample_path() -> Path | None:
    """Путь к реальному образцу или None, если каталога samples/ нет."""
    if not SAMPLES_DIR.is_dir():
        return None
    candidates = sorted(SAMPLES_DIR.glob("*.xlsx"))
    return candidates[0] if candidates else None


@pytest.fixture(scope="module")
def fixture_result():
    """Разбор обезличенного fixture. Модульная область — разбор небыстрый."""
    if not FIXTURE_PATH.is_file():
        pytest.fail(f"Обезличенный fixture обязан лежать в репозитории: {FIXTURE_PATH}")
    return parse_estimate(str(FIXTURE_PATH))


@pytest.fixture(scope="module")
def real_sample_result():
    """Разбор реального образца; пропуск, если его нет локально."""
    path = _real_sample_path()
    if path is None:
        pytest.skip("Каталог samples/ отсутствует — реальный образец не коммитится (AGENTS.md §9)")
    return parse_estimate(str(path))


class TestParseEstimateOnFixture:
    """Разбор обезличенного fixture."""

    def test_parses_without_warnings(self, fixture_result):
        """Раскладка совпадает с ожидаемой — предупреждений нет."""
        assert fixture_result.warnings == []

    def test_reports_parser_version(self, fixture_result):
        assert fixture_result.parser_version == PARSER_VERSION

    def test_extracts_header(self, fixture_result):
        """Шапка сметы ГП лежит в строке 2 — исходный диапазон 3–5 её терял."""
        data = fixture_result.data

        assert data["tender_id"] == "001-ТУ"
        assert data["tender_title"] == '"Тестовый ЖК_Генподряд"'
        assert data["tender_object"] == "Тестовый ЖК Корпус 1"
        assert data["tender_address"] == "г. Тестоград, ул. Примерная, вл 1"

    def test_single_lot_and_single_proposal(self, fixture_result):
        """Смета ГП: один лот, один подрядчик (AGENTS.md §4)."""
        lots = fixture_result.data["lots"]

        assert list(lots) == ["lot_1"]
        assert lots["lot_1"]["lot_title"].startswith("Лот №1")
        assert list(lots["lot_1"]["proposals"]) == ["contractor_1"]

    def test_contractor_block_is_eleven_columns(self, fixture_result):
        """J..T — раскладка, на которой держится смысл колонок стоимости."""
        proposal = fixture_result.data["lots"]["lot_1"]["proposals"]["contractor_1"]

        assert proposal["contractor_width"] == 11
        assert proposal["contractor_coordinate"] == "J6"
        assert proposal["title"] == 'ООО "ТЕСТПОДРЯД"'
        assert proposal["inn"] == "0000000000"

    def test_baseline_is_absent(self, fixture_result):
        """Колонки «Расчётная стоимость» в смете ГП нет — это норма, не ошибка."""
        lot = fixture_result.data["lots"]["lot_1"]

        assert lot["baseline_proposal"]["title"] == BASELINE_MISSING_TITLE

    def test_no_deviation_fields_on_positions(self, fixture_result):
        """Без baseline поля отклонения вычищены (AGENTS.md §4)."""
        positions = _positions(fixture_result)

        assert all("deviation_from_baseline_cost" not in p for p in positions.values())

    def test_position_count_matches_phase0_measurement(self, fixture_result):
        """Все строки блока позиций на месте — включая первые две.

        Исходная константа START_INDEXING_POSITION_ROW = 13 отрезала строки
        11 и 12, давая 2574 вместо 2576.
        """
        assert len(_positions(fixture_result)) == EXPECTED_POSITIONS

    def test_first_rows_are_not_dropped(self, fixture_result):
        """Первая позиция — строка-раздел лота, вторая — первый раздел сметы."""
        positions = _positions(fixture_result)

        assert positions["1"]["job_title"].startswith("Лот №1")
        assert positions["1"]["is_chapter"] is True
        assert positions["2"]["job_title"].startswith("Подготовительные работы")

    def test_priced_rows_match_phase0_measurement(self, fixture_result):
        """1828 строк с «Предлагаемым количеством» — цифра замера фазы 0.

        На ней держится вывод §6: вес позиции берётся из suggested_quantity.
        """
        positions = _positions(fixture_result)
        with_suggested = [p for p in positions.values() if p.get("suggested_quantity") is not None]

        assert len(with_suggested) == EXPECTED_PRICED_ROWS

    def test_suggested_quantity_drives_total_cost(self, fixture_result):
        """`Стоимость всего = цена за единицу × Предлагаемое количество`.

        Ключевая проверка семантики §6: именно это соотношение фаза 0 нашла на
        1828 из 1828 строк. Если раскладка колонок поедет, оно развалится.
        """
        positions = _positions(fixture_result)

        checked = 0
        for position in positions.values():
            unit_total = position["unit_cost"]["total"]
            row_total = position["total_cost"]["total"]
            weight = position.get("suggested_quantity")

            if not isinstance(unit_total, int | float) or not isinstance(row_total, int | float):
                continue
            if not isinstance(weight, int | float) or weight == 0:
                continue

            assert row_total == pytest.approx(unit_total * weight, rel=1e-6, abs=KOPECK), position["job_title"]
            checked += 1

        assert checked > 1500, f"проверено слишком мало строк: {checked}"

    def test_organizer_quantity_total_uses_quantity(self, fixture_result):
        """Справочная колонка S считается по «Общему кол-ву», а не по J.

        Обратная сторона той же проверки: колонки не перепутаны местами.
        """
        positions = _positions(fixture_result)

        checked = 0
        for position in positions.values():
            unit_total = position["unit_cost"]["total"]
            organizer_total = position.get("total_cost_for_organizer_quantity")
            quantity = position.get("quantity")

            if not isinstance(unit_total, int | float) or not isinstance(organizer_total, int | float):
                continue
            if not isinstance(quantity, int | float) or quantity == 0:
                continue

            assert organizer_total == pytest.approx(
                unit_total * quantity, rel=1e-6, abs=KOPECK
            ), position["job_title"]
            checked += 1

        assert checked > 1500, f"проверено слишком мало строк: {checked}"

    def test_summary_block_parsed(self, fixture_result):
        """Итоги распознаны семантически, а не как merged_<строка>."""
        summary = _proposal(fixture_result)["contractor_items"]["summary"]

        assert sorted(summary) == ["total_cost_with_vat", "vat"]

    def test_additional_info_parsed(self, fixture_result):
        """Блок дополнительной информации — шесть пунктов."""
        additional = _proposal(fixture_result)["additional_info"]

        assert len(additional) == 6

    def test_positions_stop_before_additional_info_block(self, fixture_result):
        """Досрочный выход по merged-ячейке отработал (AGENTS.md §11).

        В блоке дополнительной информации колонка подрядчика содержит текст
        («Представлено», «К обсуждению»). Если бы позиции дотянулись до него,
        этот текст попал бы в suggested_quantity.
        """
        positions = _positions(fixture_result)

        for position in positions.values():
            weight = position.get("suggested_quantity")
            assert not isinstance(weight, str), f"текст в количестве: {weight!r}"

        titles = {str(p.get("job_title")) for p in positions.values()}
        assert not any("Дополнительная информация" in t for t in titles)

    def test_executor_block_is_empty(self, fixture_result):
        """В смете ГП блока исполнителя нет — data_prepared_on_date будет NULL."""
        assert fixture_result.data["executor"] == {
            "executor_name": None,
            "executor_phone": None,
            "executor_date": None,
        }

    def test_every_position_with_title_has_normalized_title(self, fixture_result):
        """Нормализованное наименование — вход матчинга фазы 4."""
        positions = _positions(fixture_result)

        for position in positions.values():
            if position.get("job_title"):
                assert position["job_title_normalized"], position["job_title"]


class TestParseEstimateOnRealSample:
    """Те же проверки на реальном образце — сверка обезличивания."""

    def test_parses_without_warnings(self, real_sample_result):
        assert real_sample_result.warnings == []

    def test_counts_match_the_fixture(self, real_sample_result, fixture_result):
        """Обезличивание не изменило структуру: те же числа, что у fixture."""
        real_positions = _positions(real_sample_result)
        fixture_positions = _positions(fixture_result)

        assert len(real_positions) == len(fixture_positions) == EXPECTED_POSITIONS

        def priced(positions):
            return sum(1 for p in positions.values() if p.get("suggested_quantity") is not None)

        assert priced(real_positions) == priced(fixture_positions) == EXPECTED_PRICED_ROWS

    def test_layout_matches_the_fixture(self, real_sample_result, fixture_result):
        real_proposal = _proposal(real_sample_result)
        fixture_proposal = _proposal(fixture_result)

        assert real_proposal["contractor_width"] == fixture_proposal["contractor_width"] == 11
        assert real_proposal["contractor_coordinate"] == fixture_proposal["contractor_coordinate"] == "J6"

    def test_header_identifier_is_extracted(self, real_sample_result):
        """На реальном файле исходный парсер оставлял оба поля пустыми."""
        assert real_sample_result.data["tender_id"]
        assert real_sample_result.data["tender_title"]

    def test_baseline_is_absent(self, real_sample_result):
        lot = real_sample_result.data["lots"]["lot_1"]

        assert lot["baseline_proposal"]["title"] == BASELINE_MISSING_TITLE


class TestParseEstimateFailures:
    """Структурно непригодные файлы отвергаются с внятной причиной."""

    def test_raises_without_contractor_header_row(self):
        from openpyxl import Workbook

        ws = Workbook().active
        ws["A1"] = "Просто таблица"

        with pytest.raises(EstimateParseError, match="Наименование контрагента"):
            parse_worksheet(ws)

    def test_raises_when_contractor_marker_has_no_contractor(self):
        from openpyxl import Workbook

        ws = Workbook().active
        ws["G6"] = "Наименование контрагента"

        with pytest.raises(EstimateParseError, match="самого подрядчика"):
            parse_worksheet(ws)

    def test_raises_without_lot_marker(self):
        from openpyxl import Workbook

        ws = Workbook().active
        ws["G6"] = "Наименование контрагента"
        ws["J6"] = 'ООО "Тест"'

        with pytest.raises(EstimateParseError, match="Лот №"):
            parse_worksheet(ws)


# --- вспомогательное ---


def _proposal(result):
    return result.data["lots"]["lot_1"]["proposals"]["contractor_1"]


def _positions(result):
    return _proposal(result)["contractor_items"]["positions"]
