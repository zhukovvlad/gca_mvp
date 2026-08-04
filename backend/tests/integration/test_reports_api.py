"""Excel-выгрузки §7.6: свод по договору и отчёт «для банка».

Содержимое проверяется через `load_workbook` по образцу `test_export.py` источника —
иначе тест доказывал бы только то, что эндпоинт вернул какие-то байты.

Под контролем ровно то, что можно проверить машиной:

* согласованный макет §6.1 — разрез класс → работа, набор колонок, итоги по классу
  **и** общий, шапка и блок подписей;
* «нет норматива» отличимо от «0 %» **в файле** (§10) — это не ноль и не пустота;
* отклонение в итогах взвешено по объёму, а не усреднено из процентов строк;
* деньги записаны числами (`Decimal`), а не строками: иначе банк не сможет
  просуммировать колонку в Excel;
* защита от formula injection: наименование из чужого XLSX не становится формулой;
* имя файла переносит русские буквы и не ломается на «/» в номере договора.

Вёрстку (цвета, ширины) не проверяем — это вкус, и тест на него только мешал бы.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from io import BytesIO
from urllib.parse import unquote

import pytest
from openpyxl import load_workbook

from models import CatalogKind

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _estimate_with(factories, *, contract=None, amendment_no=None,
                   estimate_date=dt.date(2025, 4, 1)):
    contract = contract or factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(
        contract=contract, amendment_no=amendment_no, data_prepared_on_date=estimate_date
    )
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(lot=lot, contractor=contract.contractor)
    return contract, estimate, proposal


def _position(factories, proposal, position, *, unit_cost, weight):
    return factories.PositionItemFactory.create(
        proposal=proposal,
        catalog_position=position,
        unit_cost_total=Decimal(unit_cost),
        suggested_quantity=Decimal(weight),
        quantity=Decimal("1"),
        total_cost_total=Decimal(unit_cost) * Decimal(weight),
    )


def _standard(factories, position, rate_class, rate):
    return factories.RateStandardFactory.create(
        catalog_position=position,
        rate_class=rate_class,
        standard_unit_rate=Decimal(rate),
        valid_from=dt.date(2024, 1, 1),
    )


def _sheet(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return load_workbook(BytesIO(response.content)).active


def _cells(ws) -> list[list]:
    return [[c.value for c in row] for row in ws.iter_rows()]


def _find_row(ws, predicate) -> tuple[int, list]:
    """Первая строка, удовлетворяющая условию, вместе с её номером (1-based)."""
    for index, row in enumerate(_cells(ws), start=1):
        if predicate(row):
            return index, row
    raise AssertionError("строка не найдена")


def _text_of(ws) -> str:
    return "\n".join(
        " | ".join("" if v is None else str(v) for v in row) for row in _cells(ws)
    )


# ---------------------------------------------------------------------------
#  (а) Свод расценок по договору
# ---------------------------------------------------------------------------

class TestContractSummary:
    def test_unknown_contract_gives_404(self, client):
        assert client.get(
            "/api/v1/reports/contract-summary", params={"contract_id": 999999}
        ).status_code == 404

    def test_sheet_has_header_columns_totals_and_signatures(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(standard_job_title="Кладка кирпичная")
        _position(factories, proposal, position, unit_cost="12000", weight="100")
        _standard(factories, position, contract.rate_class, "10000")

        ws = _sheet(
            client.get("/api/v1/reports/contract-summary", params={"contract_id": contract.id})
        )
        text = _text_of(ws)

        assert f"СВОД РАСЦЕНОК ПО ДОГОВОРУ {contract.contract_number}" in text
        assert "Класс объектов:" in text
        assert "Сформирован:" in text
        # Согласованный набор колонок §6.1.
        _, headers = _find_row(ws, lambda row: row[0] == "Работа")
        assert headers[:8] == [
            "Работа", "Ед.", "Объём", "Ставка", "Норматив", "Стоимость",
            "Отклонение, %", "Отклонение, ₽",
        ]
        assert "ИТОГО ПО ДОГОВОРУ" in text
        # Блок подписей — требование макета.
        assert "Составил" in text
        assert "Утвердил" in text

    def test_money_are_numbers_not_strings(self, client, factories):
        """Банк суммирует колонку в Excel — значит в ячейке обязано быть число.

        Строка выглядела бы так же, но `СУММ` по ней дала бы ноль. И это тот же §3,
        что на бэкенде: `Decimal` доезжает до файла без приведения к `float`.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="12000.55", weight="100")
        _standard(factories, position, contract.rate_class, "10000")

        ws = _sheet(
            client.get("/api/v1/reports/contract-summary", params={"contract_id": contract.id})
        )
        row_index, row = _find_row(ws, lambda r: isinstance(r[3], int | float | Decimal))

        for column in (3, 4, 5, 6, 8):  # объём, ставка, норматив, стоимость, откл. ₽
            value = ws.cell(row=row_index, column=column).value
            assert isinstance(value, int | float | Decimal), f"колонка {column}: {value!r}"
        assert Decimal(str(row[3])) == Decimal("12000.55")

    def test_work_without_standard_is_marked_not_zeroed(self, client, factories):
        """§10 в файле: «нет норматива» — не ноль и не пустота."""
        contract, _estimate, proposal = _estimate_with(factories)
        without = factories.CatalogPositionFactory.create(standard_job_title="Без норматива")
        _position(factories, proposal, without, unit_cost="500", weight="10")

        ws = _sheet(
            client.get("/api/v1/reports/contract-summary", params={"contract_id": contract.id})
        )
        row_index, _row = _find_row(ws, lambda r: r[0] == "Без норматива")

        assert ws.cell(row=row_index, column=5).value == "нет норматива"
        assert ws.cell(row=row_index, column=7).value == "нет норматива"
        # Отклонение в деньгах пустое — но НЕ нулевое: ноль означал бы «сошлось».
        assert ws.cell(row=row_index, column=8).value is None

    def test_zero_deviation_is_a_number(self, client, factories):
        """Ровно по нормативу — это 0, и он обязан быть числом, а не пометкой."""
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(standard_job_title="Ровно норматив")
        _position(factories, proposal, position, unit_cost="10000", weight="10")
        _standard(factories, position, contract.rate_class, "10000")

        ws = _sheet(
            client.get("/api/v1/reports/contract-summary", params={"contract_id": contract.id})
        )
        row_index, _row = _find_row(ws, lambda r: r[0] == "Ровно норматив")

        assert ws.cell(row=row_index, column=7).value == 0
        assert ws.cell(row=row_index, column=8).value == 0

    def test_formula_injection_is_neutralised(self, client, factories):
        """Наименование приходит из чужого XLSX — Excel не должен считать его формулой.

        `=1+1` в ячейке Excel вычислил бы; апостроф делает значение текстом. Проверяем
        именно наименование: остальные колонки числовые и такого разбора не имеют.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(standard_job_title="=1+1")
        _position(factories, proposal, position, unit_cost="100", weight="10")

        ws = _sheet(
            client.get("/api/v1/reports/contract-summary", params={"contract_id": contract.id})
        )
        _index, row = _find_row(ws, lambda r: isinstance(r[0], str) and r[0].endswith("1+1"))
        assert row[0] == "'=1+1"

    def test_filename_carries_cyrillic_and_survives_slash(self, client, factories):
        """Номер договора вида «12/2025» не должен ломать имя файла."""
        contract = factories.ContractFactory.create(contract_number="ГП-12/2025")
        response = client.get(
            "/api/v1/reports/contract-summary", params={"contract_id": contract.id}
        )
        assert response.status_code == 200
        disposition = unquote(response.headers["content-disposition"])
        assert "Свод расценок" in disposition
        assert "ГП-12-2025" in disposition  # слэш заменён, путь не сломан
        assert "/" not in disposition.split("''")[-1]

    def test_contract_without_estimate_still_produces_a_file(self, client, factories):
        """Смета не загружена — файл всё равно отдаётся, с реквизитами и пустой таблицей.

        Отказ здесь был бы неудобен и неверен: реквизиты уже есть что показать.
        """
        contract = factories.ContractFactory.create()
        ws = _sheet(
            client.get("/api/v1/reports/contract-summary", params={"contract_id": contract.id})
        )
        assert "ИТОГО ПО ДОГОВОРУ" in _text_of(ws)


# ---------------------------------------------------------------------------
#  (б) Сравнение с нормативами «для банка»
# ---------------------------------------------------------------------------

class TestBankComparison:
    def _two_classes(self, factories):
        """Две секции: у каждой свой класс, работа и норматив."""
        first_class = factories.RateClassFactory.create(title="Жилые дома")
        second_class = factories.RateClassFactory.create(title="Административные")

        first = factories.ContractFactory.create(rate_class=first_class)
        _c1, _e1, p1 = _estimate_with(factories, contract=first)
        work_a = factories.CatalogPositionFactory.create(standard_job_title="Кладка кирпичная")
        _position(factories, p1, work_a, unit_cost="12000", weight="100")
        _standard(factories, work_a, first_class, "10000")

        second = factories.ContractFactory.create(rate_class=second_class)
        _c2, _e2, p2 = _estimate_with(factories, contract=second)
        work_b = factories.CatalogPositionFactory.create(standard_job_title="Стяжка пола")
        _position(factories, p2, work_b, unit_cost="900", weight="1000")
        _standard(factories, work_b, second_class, "1000")
        return first_class, second_class, work_a, work_b

    def test_layout_is_class_then_work_with_both_totals(self, client, factories):
        """Согласованный разрез §6.1: секции по классам, итог по каждому и общий."""
        self._two_classes(factories)
        ws = _sheet(client.get("/api/v1/reports/bank-comparison"))
        text = _text_of(ws)

        assert "СРАВНЕНИЕ РАСЦЕНОК С НОРМАТИВАМИ" in text
        assert "КЛАСС: Жилые дома" in text
        assert "КЛАСС: Административные" in text
        assert "ИТОГО ПО КЛАССУ «Жилые дома»" in text
        assert "ИТОГО ПО КЛАССУ «Административные»" in text
        assert "ВСЕГО ПО ВЫБОРКЕ" in text
        # Шапка выборки и подписи — тоже часть макета.
        assert "Договоров:" in text
        assert "Составил" in text

    def test_work_appears_inside_its_class_section(self, client, factories):
        """Работа стоит в секции своего класса, а не где-нибудь на листе."""
        self._two_classes(factories)
        ws = _sheet(client.get("/api/v1/reports/bank-comparison"))

        housing_at, _ = _find_row(ws, lambda r: r[0] == "КЛАСС: Жилые дома")
        housing_total_at, _ = _find_row(ws, lambda r: r[0] == "ИТОГО ПО КЛАССУ «Жилые дома»")
        brick_at, _ = _find_row(ws, lambda r: r[0] == "Кладка кирпичная")
        assert housing_at < brick_at < housing_total_at

    def test_deviation_money_is_the_difference_of_shown_sums(self, client, factories):
        """Отклонение в деньгах обязано сходиться с показанными суммами.

        Банк проверяет на калькуляторе: стоимость минус норматив × объём. Если строка
        включала бы позиции без норматива, разница не сошлась бы, и объяснять это
        пришлось бы банку.
        """
        first_class, _second, _work_a, _work_b = self._two_classes(factories)
        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": first_class.id})
        )
        row_index, _row = _find_row(ws, lambda r: r[0] == "Кладка кирпичная")

        volume = Decimal(str(ws.cell(row=row_index, column=3).value))
        rate = Decimal(str(ws.cell(row=row_index, column=4).value))
        standard = Decimal(str(ws.cell(row=row_index, column=5).value))
        amount = Decimal(str(ws.cell(row=row_index, column=6).value))
        deviation_money = Decimal(str(ws.cell(row=row_index, column=8).value))

        assert amount == rate * volume
        assert deviation_money == (rate - standard) * volume

    def test_class_total_deviation_is_weighted_not_averaged(self, client, factories):
        """Итог по классу взвешен по объёму, а не усреднён из процентов строк.

        Числа подобраны так, что ответы РАЗЛИЧАЮТСЯ: работа на 12 000 000 с +20 % и
        работа на 40 000 с +100 % дают средневзвешенное ≈ +20,3 %, а среднее
        арифметическое процентов — +60 %. Без такого расхождения тест прошёл бы и на
        неверной формуле.
        """
        rate_class = factories.RateClassFactory.create(title="Взвешенный класс")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)

        big = factories.CatalogPositionFactory.create(standard_job_title="Крупная работа")
        _position(factories, proposal, big, unit_cost="12000", weight="1000")   # 12 000 000
        _standard(factories, big, rate_class, "10000")                          # +20 %

        small = factories.CatalogPositionFactory.create(standard_job_title="Мелкая работа")
        _position(factories, proposal, small, unit_cost="400", weight="100")     # 40 000
        _standard(factories, small, rate_class, "200")                           # +100 %

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        row_index, _row = _find_row(ws, lambda r: r[0] == "ИТОГО ПО КЛАССУ «Взвешенный класс»")
        total_deviation = Decimal(str(ws.cell(row=row_index, column=7).value))

        # (12 040 000 − 10 020 000) / 10 020 000 = 20,159...%
        expected = (Decimal("12040000") / Decimal("10020000") - 1) * 100
        assert abs(total_deviation - expected) < Decimal("0.0001")
        # И это НЕ среднее арифметическое процентов.
        assert abs(total_deviation - Decimal("60")) > Decimal("30")

    def test_positions_without_standard_are_excluded_and_counted(self, client, factories):
        """Прямое требование пользователя: не в расчёт, но отдельным счётчиком."""
        rate_class = factories.RateClassFactory.create(title="Класс со счётчиком")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)

        compared = factories.CatalogPositionFactory.create(standard_job_title="Сравнимая работа")
        _position(factories, proposal, compared, unit_cost="1000", weight="10")
        _standard(factories, compared, rate_class, "1000")

        skipped = factories.CatalogPositionFactory.create(standard_job_title="Без норматива вовсе")
        _position(factories, proposal, skipped, unit_cost="9999", weight="10")

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        text = _text_of(ws)

        # Строки нет — сравнивать её не с чем...
        assert "Без норматива вовсе" not in text
        # ...но счётчик о ней говорит, и он не нулевой.
        assert "Позиций без норматива (в отклонение не вошли): 1" in text
        # Отклонение итога не разбавлено этой работой: сравнимая часть ровно 0 %.
        row_index, _row = _find_row(ws, lambda r: r[0] == "ИТОГО ПО КЛАССУ «Класс со счётчиком»")
        assert Decimal(str(ws.cell(row=row_index, column=7).value)) == 0

    def test_class_without_any_standard_shows_no_zero_deviation(self, client, factories):
        """Класс, где сравнивать нечего, не должен показывать «отклонение 0 ₽».

        **Найдено прогоном стенда.** На реальных данных попался класс, у которого ни у
        одной работы нет норматива: в итоге стояло «откл. % — пусто, откл. ₽ — 0».
        Ноль рублей читается как «сошлось с нормативом», то есть утверждает
        противоположное действительности, и в отчёте для банка это опаснее всего.
        Процент и рубли обязаны пустеть вместе.
        """
        rate_class = factories.RateClassFactory.create(title="Класс без нормативов")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)
        work = factories.CatalogPositionFactory.create(standard_job_title="Работа без норматива")
        _position(factories, proposal, work, unit_cost="1000", weight="10")
        # Норматива нет намеренно.

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        row_index, _row = _find_row(
            ws, lambda r: r[0] == "ИТОГО ПО КЛАССУ «Класс без нормативов»"
        )

        assert ws.cell(row=row_index, column=7).value == "нет норматива"
        assert ws.cell(row=row_index, column=8).value == "нет норматива"
        assert ws.cell(row=row_index, column=8).value != 0
        # Счётчик при этом говорит, сколько позиций выпало.
        assert "Позиций без норматива (в отклонение не вошли): 1" in _text_of(ws)

    def test_counter_is_printed_even_when_zero(self, client, factories):
        """Ноль печатается: отсутствие строки читалось бы как «не проверяли»."""
        first_class, _s, _a, _b = self._two_classes(factories)
        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": first_class.id})
        )
        assert "Позиций без норматива (в отклонение не вошли): 0" in _text_of(ws)

    def test_only_latest_estimate_participates(self, client, factories):
        """§6 и в файле: исходная смета вытесняется допсоглашением."""
        rate_class = factories.RateClassFactory.create(title="Класс последней сметы")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c1, _initial, initial_proposal = _estimate_with(
            factories, contract=contract, amendment_no=None
        )
        _c2, _amend, amend_proposal = _estimate_with(factories, contract=contract, amendment_no=1)
        work = factories.CatalogPositionFactory.create(standard_job_title="Работа обеих смет")
        _position(factories, initial_proposal, work, unit_cost="100", weight="10")
        _position(factories, amend_proposal, work, unit_cost="500", weight="10")
        _standard(factories, work, rate_class, "100")

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        row_index, _row = _find_row(ws, lambda r: r[0] == "Работа обеих смет")
        assert Decimal(str(ws.cell(row=row_index, column=4).value)) == Decimal("500")

    def test_non_position_catalog_rows_never_reach_the_report(self, client, factories):
        """Каталожные строки не-POSITION с нормативами не сравниваются (§4)."""
        rate_class = factories.RateClassFactory.create(title="Класс без заголовков")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)
        header = factories.CatalogPositionFactory.create(
            standard_job_title="Заголовок раздела", kind=CatalogKind.HEADER.value
        )
        _position(factories, proposal, header, unit_cost="100", weight="10")

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        assert "Заголовок раздела" not in _text_of(ws)

    def test_period_filter_narrows_the_report(self, client, factories):
        rate_class = factories.RateClassFactory.create(title="Класс периода")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(
            factories, contract=contract, estimate_date=dt.date(2026, 5, 1)
        )
        work = factories.CatalogPositionFactory.create(standard_job_title="Работа 2026 года")
        _position(factories, proposal, work, unit_cost="100", weight="10")
        _standard(factories, work, rate_class, "100")

        inside = _sheet(
            client.get(
                "/api/v1/reports/bank-comparison",
                params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
            )
        )
        assert "Работа 2026 года" in _text_of(inside)

        outside = _sheet(
            client.get(
                "/api/v1/reports/bank-comparison",
                params={"date_from": "2025-01-01", "date_to": "2025-12-31"},
            )
        )
        assert "Работа 2026 года" not in _text_of(outside)

    def test_empty_selection_still_returns_a_valid_file(self, client):
        """Пустая выборка — валидный файл с шапкой, а не 404 и не битые байты."""
        ws = _sheet(client.get("/api/v1/reports/bank-comparison"))
        text = _text_of(ws)
        assert "СРАВНЕНИЕ РАСЦЕНОК С НОРМАТИВАМИ" in text
        assert "ВСЕГО ПО ВЫБОРКЕ" in text

    def test_footnote_explains_the_weighted_deviation(self, client, factories):
        """Сноска о способе расчёта — иначе итог читается как расхождение."""
        self._two_classes(factories)
        ws = _sheet(client.get("/api/v1/reports/bank-comparison"))
        assert "средневзвешенное по объёму" in _text_of(ws)


def test_xlsx_number_precision_boundary():
    """Граница точности чисел в xlsx — замером, а не на веру.

    §3 требует `Decimal` end-to-end, и в JSON это выполнимо (деньги едут строками).
    В **файле** — нет: формат xlsx хранит числа как IEEE-754 double, поэтому
    точность ограничена форматом, а не кодом.

    Тест закрепляет измеренную границу, чтобы её не приходилось открывать заново, и
    чтобы смена поведения openpyxl или формата не прошла незамеченной. Он же
    опровергает соблазнительное, но неверное утверждение «openpyxl сохраняет Decimal
    с полной точностью» — первая редакция модульной документации так и говорила.

    Тест не integration по смыслу, но живёт здесь: он про тот же файл и читается
    вместе с остальными проверками выгрузок.
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    exact = ["12000.55", "1234567890.12", "1075.35475", "22152600.00"]
    lossy = ["12345678901234567.89", "99999999999999999.99"]
    for index, value in enumerate(exact + lossy, start=1):
        ws.cell(row=index, column=1, value=Decimal(value))

    buffer = BytesIO()
    wb.save(buffer)
    back = load_workbook(BytesIO(buffer.getvalue())).active

    for index, value in enumerate(exact, start=1):
        stored = back.cell(row=index, column=1).value
        assert Decimal(str(stored)) == Decimal(value), f"{value} не доехал: {stored!r}"

    # А это — предел формата. Проверяем именно потерю: если она однажды исчезнет,
    # значит поведение изменилось, и документацию надо обновить.
    for offset, value in enumerate(lossy):
        stored = back.cell(row=len(exact) + offset + 1, column=1).value
        assert Decimal(str(stored)) != Decimal(value), (
            f"{value} внезапно доехал точно — граница сдвинулась, обновите "
            "документацию services/excel.py"
        )
