"""Тесты оркестратора `parse_estimate` на реальной раскладке сметы ГП.

Два источника данных:

* `fixtures/gp_estimate_fixture.xlsx` — обезличенная копия реального образца
  (фаза 0). Коммитится, работает в CI, на нём держатся основные проверки.
* `samples/` — реальные оферты (может лежать несколько). В репозиторий не
  попадают (AGENTS.md §9), тесты на них пропускаются, если каталога нет.
  Проверяются инварианты формата и семантика §6 на КАЖДОЙ оферте, плюс
  присутствие первоисточника fixture — что обезличивание не изменило
  поведения парсера.

Числа в ожиданиях — не «что получилось», а замеры фазы 0
(`docs/phase0-input-data.md`): 2576 строк позиций, 1828 расценённых строк с
заполненным «Предлагаемым количеством». Они принадлежат первоисточнику
fixture, а не любому образцу.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from parser import PARSER_VERSION, EstimateParseError, parse_estimate, parse_worksheet
from parser.constants import TABLE_PARSE_POSITION_COLUMN_HEADERS
from parser.postprocess import BASELINE_MISSING_TITLE

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "gp_estimate_fixture.xlsx"
SAMPLES_DIR = REPO_ROOT / "samples"

# Замеры фазы 0 на реальном образце (docs/phase0-input-data.md).
EXPECTED_POSITIONS = 2576
EXPECTED_PRICED_ROWS = 1828

# Стоимости в файле округлены до копеек, поэтому произведение
# «цена за единицу × количество» сходится с точностью до копейки, а не побитово.
# На крупных суммах округление самих сомножителей даёт больше копейки, поэтому
# рядом стоит относительный допуск. Оба заведомо меньше, чем расхождение от
# перепутанных колонок: оно было бы на порядки.
KOPECK = Decimal("0.01")
RELATIVE_TOLERANCE = Decimal("1e-6")

# Денежные поля позиции — те, что AGENTS.md §3 требует держать строками.
MONEY_PATHS = (
    ("unit_cost", "materials"),
    ("unit_cost", "works"),
    ("unit_cost", "indirect_costs"),
    ("unit_cost", "total"),
    ("total_cost", "materials"),
    ("total_cost", "works"),
    ("total_cost", "indirect_costs"),
    ("total_cost", "total"),
    ("total_cost_for_organizer_quantity",),
)


# Все локальные оферты. Берутся только «Оферта_*»: в samples/ могут лежать и
# файлы других форматов, которые парсер текущей фазы намеренно отвергает.
SAMPLE_PATHS = sorted(SAMPLES_DIR.glob("Оферта*.xlsx")) if SAMPLES_DIR.is_dir() else []


@pytest.fixture(scope="module")
def fixture_result():
    """Разбор обезличенного fixture. Модульная область — разбор небыстрый."""
    if not FIXTURE_PATH.is_file():
        pytest.fail(f"Обезличенный fixture обязан лежать в репозитории: {FIXTURE_PATH}")
    return parse_estimate(str(FIXTURE_PATH))


@pytest.fixture(scope="module")
def sample_results():
    """Разбор всех локальных оферт; пропуск, если их нет.

    В сообщениях тестов образцы именуются по номеру, не по имени файла:
    в именах файлов фигурируют контрагенты, а вывод тестов может попасть
    в логи (AGENTS.md §9).
    """
    if not SAMPLE_PATHS:
        pytest.skip("Каталог samples/ пуст или отсутствует — реальные оферты не коммитятся (AGENTS.md §9)")
    return [(f"образец №{i}", parse_estimate(str(path))) for i, path in enumerate(SAMPLE_PATHS, start=1)]


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
            unit_total = _money(position["unit_cost"]["total"])
            row_total = _money(position["total_cost"]["total"])
            weight = _amount(position.get("suggested_quantity"))

            if unit_total is None or row_total is None or not weight:
                continue

            _assert_close(row_total, unit_total * weight, position["job_title"])
            checked += 1

        assert checked > 1500, f"проверено слишком мало строк: {checked}"

    def test_organizer_quantity_total_uses_quantity(self, fixture_result):
        """Справочная колонка S считается по «Общему кол-ву», а не по J.

        Обратная сторона той же проверки: колонки не перепутаны местами.
        """
        positions = _positions(fixture_result)

        checked = 0
        for position in positions.values():
            unit_total = _money(position["unit_cost"]["total"])
            organizer_total = _money(position.get("total_cost_for_organizer_quantity"))
            quantity = _amount(position.get("quantity"))

            if unit_total is None or organizer_total is None or not quantity:
                continue

            _assert_close(organizer_total, unit_total * quantity, position["job_title"])
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


class TestParseEstimateOnRealSamples:
    """Инварианты формата на каждой локальной оферте + сверка первоисточника.

    Точные числа (2576/1828) принадлежат первоисточнику fixture; остальные
    оферты проверяются на инварианты формата и семантику §6 — так каждый новый
    образец в samples/ автоматически становится проверкой парсера.
    """

    def test_every_sample_parses_without_warnings(self, sample_results):
        for name, result in sample_results:
            assert result.warnings == [], name

    def test_every_sample_has_gp_layout(self, sample_results):
        """J6, 11 колонок — раскладка, на которой держится смысл стоимостей."""
        for name, result in sample_results:
            proposal = _proposal(result)

            assert proposal["contractor_width"] == 11, name
            assert proposal["contractor_coordinate"] == "J6", name

    def test_every_sample_extracts_header_identifier(self, sample_results):
        """На реальных файлах исходный парсер оставлял оба поля пустыми."""
        for name, result in sample_results:
            assert result.data["tender_id"], name
            assert result.data["tender_title"], name

    def test_every_sample_has_no_baseline(self, sample_results):
        for name, result in sample_results:
            lot = result.data["lots"]["lot_1"]

            assert lot["baseline_proposal"]["title"] == BASELINE_MISSING_TITLE, name

    def test_weight_is_suggested_quantity_on_every_sample(self, sample_results):
        """`Стоимость всего = цена × Предлагаемое количество` (AGENTS.md §6).

        Фаза 0 доказала это на одном образце (1828/1828); каждая новая оферта
        в samples/ перепроверяет вывод. На втором образце (2026-08-03,
        экспорт из системы-источника): 1193/1193.
        """
        for name, result in sample_results:
            checked = 0
            for position in _positions(result).values():
                unit_total = _money(position["unit_cost"]["total"])
                row_total = _money(position["total_cost"]["total"])
                weight = _amount(position.get("suggested_quantity"))

                if unit_total is None or row_total is None or not weight:
                    continue

                _assert_close(row_total, unit_total * weight, f"{name}: {position['job_title']}")
                checked += 1

            assert checked > 500, f"{name}: проверено слишком мало строк: {checked}"

    def test_coalesce_weight_fallback_is_safe_on_every_sample(self, sample_results):
        """Нет строк, где `quantity` осмыслен (≠1) и отличается от suggested.

        Это условие, при котором `w = COALESCE(suggested_quantity, quantity)`
        из §6 не может подменить вес чужим числом (фаза 0, п. «Чем доказано»).
        """
        for name, result in sample_results:
            for position in _positions(result).values():
                quantity = _amount(position.get("quantity"))
                suggested = _amount(position.get("suggested_quantity"))

                if quantity is None or suggested is None or quantity == 1:
                    continue

                assert quantity == suggested, (
                    f"{name}: «{position['job_title']}»: quantity={quantity}, suggested={suggested}"
                )

    def test_fixture_source_is_among_samples(self, sample_results, fixture_result):
        """Первоисточник fixture лежит в samples/ и совпадает с ним по числам.

        Если ни один образец не дал числа fixture — либо первоисточник убрали
        из каталога, либо парсер разошёлся с fixture на реальном файле. И то
        и другое должно быть громким.
        """
        fixture_positions = _positions(fixture_result)

        def priced(positions):
            return sum(1 for p in positions.values() if p.get("suggested_quantity") is not None)

        matches = [
            name
            for name, result in sample_results
            if len(_positions(result)) == len(fixture_positions) == EXPECTED_POSITIONS
            and priced(_positions(result)) == priced(fixture_positions) == EXPECTED_PRICED_ROWS
        ]

        assert matches, (
            f"ни один из {len(sample_results)} образцов не совпал с fixture "
            f"({EXPECTED_POSITIONS} позиций / {EXPECTED_PRICED_ROWS} расценённых)"
        )


class TestMoneyContract:
    """Деньги в `raw_data` — десятичные строки, ни одного `float` (AGENTS.md §3, §11)."""

    def test_position_money_fields_are_decimal_strings(self, fixture_result):
        """Каждое денежное поле позиции — строка либо None (пустая стоимость → NULL)."""
        for position in _positions(fixture_result).values():
            for path in MONEY_PATHS:
                value = _dig(position, path)
                assert value is None or isinstance(value, str), (
                    f"{'.'.join(path)} = {value!r} ({type(value).__name__}) у «{position.get('job_title')}»"
                )

    def test_summary_money_fields_are_decimal_strings(self, fixture_result):
        """Итоги идут через тот же `parse_contractor_row` — контракт тот же."""
        summary = _proposal(fixture_result)["contractor_items"]["summary"]

        assert summary, "блок итогов пуст — проверять нечего"
        for key, line in summary.items():
            for path in MONEY_PATHS:
                value = _dig(line, path)
                assert value is None or isinstance(value, str), f"{key}.{'.'.join(path)} = {value!r}"

    def test_money_strings_parse_back_to_decimal(self, fixture_result):
        """Фаза 4 делает `Decimal(value)` — значит, строка обязана им приниматься."""
        checked = 0
        for position in _positions(fixture_result).values():
            for path in MONEY_PATHS:
                value = _dig(position, path)
                if value is None:
                    continue
                Decimal(value)  # InvalidOperation здесь и есть провал теста
                checked += 1

        assert checked > 1500, f"проверено слишком мало значений: {checked}"

    def test_data_survives_json_round_trip_without_encoder(self, fixture_result):
        """`ParseResult.data` кладётся в jsonb как есть — без своего энкодера.

        И обратно: после round-trip'а деньги остаются теми же строками, то есть
        сериализация ничего не переводит в число.
        """
        restored = json.loads(json.dumps(fixture_result.data, ensure_ascii=False))

        assert _find_money_floats(restored) == []
        assert restored["lots"] == fixture_result.data["lots"]

    def test_totals_are_not_floats_anywhere_in_data(self, fixture_result):
        """Сквозная проверка: под денежными ключами `float` не встречается нигде."""
        floats = _find_money_floats(fixture_result.data)

        assert floats == [], f"float в денежных полях: {floats[:5]}"

    def test_temporal_and_error_cells_become_json_safe(self):
        """Дата в ячейке и ошибка Excel не ломают контракт «data → jsonb как есть».

        openpyxl отдаёт date-форматированную ячейку объектом `datetime` —
        на fixture таких нет, поэтому проверка на синтетическом листе:
        дата → ISO-строка, `#N/A` → None, соседняя сумма не задета.
        """
        import datetime as dt

        ws = _minimal_sheet(contractor_colspan=11)
        # A и B заполнены не для красоты: строка без номера и без раздела —
        # кандидат в агрегатную строку допработ, и лист был бы отвергнут
        # (спека Ф2 §2.2). Реальные файлы несут здесь номер и раздел.
        ws.cell(row=12, column=1, value=1)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Работа с датой в комментарии")
        ws.cell(row=12, column=11, value="#N/A")  # unit_cost.materials
        ws.cell(row=12, column=14, value=60.5)  # unit_cost.total
        ws.cell(row=12, column=20, value=dt.datetime(2025, 2, 1))  # comment_contractor

        result = parse_worksheet(ws)

        json.dumps(result.data, ensure_ascii=False)  # TypeError здесь — провал теста

        position = _positions(result)["2"]
        assert position["comment_contractor"] == "2025-02-01T00:00:00"
        assert position["unit_cost"]["materials"] is None
        assert position["unit_cost"]["total"] == "60.5"


class TestAdditionalWorksInJson:
    """Поле доезжает до итоговой структуры рядом с positions и summary."""

    def test_additional_works_lands_next_to_positions(self):
        ws = _minimal_sheet(11)
        ws.cell(row=12, column=1, value=1)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Обычная работа")
        ws.cell(row=13, column=4, value="Дополнительные работы")

        items = _proposal(parse_worksheet(ws))["contractor_items"]

        assert set(items) >= {"positions", "summary", "additional_works"}
        assert items["additional_works"]["job_title"] == "Дополнительные работы"
        assert items["additional_works"]["source_row"] == 13

    def test_additional_works_is_none_when_row_absent(self):
        """42-ТУ и 449-ТУ: ключ есть, значение None — это валидное состояние."""
        ws = _minimal_sheet(11)
        ws.cell(row=12, column=1, value=1)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Обычная работа")

        items = _proposal(parse_worksheet(ws))["contractor_items"]

        assert items["additional_works"] is None


def test_parser_version_is_bumped_for_the_new_key():
    """1.1.0: в contractor_items появился `additional_works` (спека Ф2 §2.4).

    Версия — часть контракта: она ложится в `estimate_raw_data.parser_version`,
    и по ней потом отличают, каким кодом разобран сохранённый JSON.
    """
    assert PARSER_VERSION == "1.1.0"


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

    def test_raises_on_unsupported_contractor_colspan(self):
        """Неизвестная ширина блока — отказ, а не предупреждение и не ValueError.

        Смысл колонок задаётся их числом; при colspan 12 раскладки нет, и разбор
        был бы выдумкой. Раньше сюда прилетал необработанный `ValueError` из
        `parse_contractor_row`, и предупреждение `check_estimate_layout` не
        доезжало до `import_jobs.warnings` — `ParseResult` просто не создавался.
        """
        ws = _minimal_sheet(contractor_colspan=12)

        with pytest.raises(EstimateParseError, match="12 колонок"):
            parse_worksheet(ws)

    def test_raises_when_contractor_header_is_not_merged(self):
        """Необъединённый заголовок раньше давал `KeyError` по `merged_shape`."""
        ws = _minimal_sheet(contractor_colspan=None)

        with pytest.raises(EstimateParseError, match="не объединён"):
            parse_worksheet(ws)

    def test_supported_but_non_gp_colspan_is_a_warning_not_an_error(self):
        """Ширина 8 разбирается: раскладка известна, просто это не смета ГП.

        Здесь предупреждение обещает импорт — и импорт действительно возможен.
        """
        ws = _minimal_sheet(contractor_colspan=8)

        result = parse_worksheet(ws)

        assert any("8 колонок" in w for w in result.warnings)


# --- вспомогательное ---


def _minimal_sheet(contractor_colspan: int | None):
    """Лист с шапкой контрагентов и маркером лота, но без строк позиций.

    Args:
        contractor_colspan: ширина объединённого блока подрядчика; None —
            заголовок вообще не объединён.
    """
    from openpyxl import Workbook

    ws = Workbook().active
    ws["G6"] = "Наименование контрагента"
    ws["J6"] = 'ООО "Тест"'
    ws["D11"] = "Лот №1 Тестовый"

    for column, title in TABLE_PARSE_POSITION_COLUMN_HEADERS.items():
        ws.cell(row=9, column=column, value=title)

    ws["A11"] = 1
    ws["B11"] = 1

    if contractor_colspan is not None:
        ws.merge_cells(start_row=6, start_column=10, end_row=6, end_column=9 + contractor_colspan)

    return ws


def _proposal(result):
    return result.data["lots"]["lot_1"]["proposals"]["contractor_1"]


def _positions(result):
    return _proposal(result)["contractor_items"]["positions"]


def _dig(container, path):
    """Достаёт значение по пути ключей; None, если по дороге нет словаря."""
    value = container
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _money(value):
    """Денежное значение из raw_data → Decimal; None, если это не число-строка."""
    if not isinstance(value, str):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _amount(value):
    """Количество (оно осталось числом) → Decimal; None, если это не число."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return Decimal(str(value))


def _assert_close(actual: Decimal, expected: Decimal, label) -> None:
    """Сравнение денег в Decimal: копейка округления либо относительный допуск."""
    tolerance = max(KOPECK, abs(expected) * RELATIVE_TOLERANCE)

    assert abs(actual - expected) <= tolerance, f"{label}: {actual} != {expected} (допуск {tolerance})"


def _find_money_floats(node, path=()):
    """Возвращает пути до `float`, лежащих под денежными ключами."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_find_money_floats(value, (*path, str(key))))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_find_money_floats(value, (*path, str(index))))
    elif isinstance(node, float) and _is_money_path(path):
        found.append(".".join(path))
    return found


def _is_money_path(path) -> bool:
    """Оканчивается ли путь одним из денежных полей."""
    return any(path[-len(money_path) :] == money_path for money_path in MONEY_PATHS)


def test_parse_error_is_importable_from_the_package_root():
    """Публичный контракт: `from parser import EstimateParseError`.

    Класс переехал в `parser.errors` ради разрыва цикла импортов, но снаружи
    имя прежнее — на него завязан `services/import_pipeline`. Сверяется
    идентичность объекта, а не только импортируемость: два разных класса с одним
    именем ловились бы `except` мимо.
    """
    import parser as parser_package
    from parser.errors import EstimateParseError as FromErrors

    assert parser_package.EstimateParseError is FromErrors


class TestColumnHeaderGuard:
    """Чужая раскладка колонок A–D — структурный отказ (спека Ф2 §2.1).

    Не предупреждение: колонки A, B, C, D читаются по фиксированным позициям,
    поэтому при чужой шапке недостоверен весь позиционный разбор, а не только
    колонка статьи. Та же граница, что у `_validate_contractor_blocks`.
    """

    def test_correct_headers_parse(self):
        ws = _minimal_sheet(11)
        result = parse_worksheet(ws)
        assert result.data is not None

    @pytest.mark.parametrize(
        ("column", "letter", "wrong_value"),
        [
            (2, "B", "Глава"),
            (3, "C", "Артикул СМР"),
            (4, "D", "Наименование видов работ"),
        ],
    )
    def test_wrong_header_in_any_column_is_rejected(self, column, letter, wrong_value):
        """Колонка A сюда НЕ входит намеренно.

        Строка шапки ищется именно по маркеру в A, поэтому испорченный A даёт не
        «чужой заголовок», а «строка не найдена» — и то сообщение фактическое
        значение не называет (спека §2.1, пункт 4). Этот случай покрывает
        `test_missing_header_row_is_rejected_without_naming_a_row`.
        """
        ws = _minimal_sheet(11)
        ws.cell(row=9, column=column, value=wrong_value)

        with pytest.raises(EstimateParseError) as exc:
            parse_worksheet(ws)

        message = str(exc.value)
        assert letter in message
        assert wrong_value in message

    def test_missing_header_row_is_rejected_without_naming_a_row(self):
        """Маркер «№ п/п» испорчен — «ту самую» строку определить нельзя.

        Поэтому сообщение называет ожидаемый маркер и просмотренный диапазон,
        но НЕ фактическое значение: назвать его было бы выдумкой.
        """
        ws = _minimal_sheet(11)
        ws.cell(row=9, column=1, value="Порядковый номер")

        with pytest.raises(EstimateParseError) as exc:
            parse_worksheet(ws)

        message = str(exc.value)
        assert "№ п/п" in message
        # Диапазон: строка заголовка контрагентов у `_minimal_sheet` — 6, маркер
        # лота — 11, значит просмотрены строки 7–10.
        assert "7–10" in message, message
        # Ключевое утверждение docstring'а: фактического значения в сообщении нет.
        # Без этой строки тест был бы зелёным и у реализации, которая его называет
        # (найдено финальным ревью, проверено снятием защиты).
        assert "Порядковый номер" not in message, message

    def test_header_row_is_found_not_hardcoded(self):
        """Шапка сдвинута на строку — файл валиден и должен разбираться.

        Ради этого строка ищется по маркеру, а не берётся константой 9.
        """
        ws = _minimal_sheet(11)
        for column in TABLE_PARSE_POSITION_COLUMN_HEADERS:
            ws.cell(row=9, column=column, value=None)
        for column, title in TABLE_PARSE_POSITION_COLUMN_HEADERS.items():
            ws.cell(row=8, column=column, value=title)

        result = parse_worksheet(ws)
        assert result.data is not None

    def test_headers_are_compared_ignoring_case_and_extra_spaces(self):
        ws = _minimal_sheet(11)
        ws.cell(row=9, column=3, value="  статья  смр  ")

        result = parse_worksheet(ws)
        assert result.data is not None
