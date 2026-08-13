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
                   estimate_date=dt.date(2025, 4, 1), vat_rate=Decimal("0")):
    """Цепочка договор → смета → лот → предложение.

    `vat_rate` по умолчанию `0` (задача 4 пересчёта НДС, тот же приём, что в
    `test_analytics_api.py::_estimate_with`): при базе 0 % нетто численно равно
    валовому (`gross_to_net(x, 0) == x`), поэтому все тесты этого файла, писавшиеся
    ДО перевода отчёта «для банка» на нетто-ось и не указывавшие ставку явно,
    продолжают проверять те же значения — без ставки НДС проверять здесь нечего,
    это дело `test_bank_report_partition_covers_every_priced_position` и соседних
    тестов четырёхклассового разбиения.
    """
    contract = contract or factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(
        contract=contract, amendment_no=amendment_no, data_prepared_on_date=estimate_date
    )
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(
        lot=lot, contractor=contract.contractor, vat_rate=vat_rate
    )
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

    def test_priced_position_without_volume_is_counted_not_silently_dropped(self, client, factories):
        """Позиция с ценой, но без обоих количеств, не должна исчезать молча.

        **Находка собственного ревью фазы.** Свод заявляет «все расценённые работы»,
        но фильтр `weight > 0` (он нужен: взвешивать без объёма нечем, §6) выбрасывал
        такие позиции без следа. В матрице это законно — §6 прямо говорит «ячейка
        пустая», — а в документе, который показывает предмет торга целиком, молчаливая
        потеря строки с ценой означает, что читатель о ней не узнает вовсе.

        Строки в таблице нет — это правильно; но счётчик обязан сказать, что она была.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        normal = factories.CatalogPositionFactory.create(standard_job_title="Обычная работа")
        _position(factories, proposal, normal, unit_cost="100", weight="10")

        ghost = factories.CatalogPositionFactory.create(standard_job_title="Позиция без объёма")
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=ghost,
            unit_cost_total=Decimal("500"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=None,
        )

        ws = _sheet(
            client.get("/api/v1/reports/contract-summary", params={"contract_id": contract.id})
        )
        text = _text_of(ws)

        assert "Позиция без объёма" not in text  # в строках её нет — взвесить нечем
        assert "но без объёма (в расчёт не вошли): 1" in text

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

    def test_vat_axis_is_declared_on_the_sheet(self, client, factories):
        """Спека §2.5: подпись ставки показа обязана быть на листе, и до «Периода».

        Ревью задачи 4: строка была на листе, но её не читал ни один тест — исчезни
        она молча, никто бы не заметил. Читатель обязан видеть, в чём измерены
        числа, которые он складывает, ДО того как увидит саму таблицу.
        """
        self._two_classes(factories)
        ws = _sheet(client.get("/api/v1/reports/bank-comparison"))

        note_at, _ = _find_row(ws, lambda r: r[0] == "Все суммы и нормативы — без НДС")
        period_at, _ = _find_row(
            ws, lambda r: isinstance(r[0], str) and r[0].startswith("Период:")
        )
        assert note_at < period_at

    def test_bank_report_converts_fact_to_net_across_different_vat_bases(self, client, factories):
        """Задача 4: факт приводится к нетто по БАЗЕ каждой позиции — числовой замер.

        Ревью задачи 4: весь класс `TestBankComparison` до этого теста гонялся на
        ставке НДС по умолчанию (0 %), при которой `gross_to_net(x, 0) == x`
        тождественно, — то есть центральная содержательная часть задачи (перевод
        факта в нетто) не была проверена НИ ОДНИМ тестом. Перепутанные местами
        аргументы `gross_to_net`, потерянная группировка по базе или подстановка
        ставки 0 вместо настоящей базы оставили бы CI зелёным.

        `unit_cost=120` при ставке 20 % и `unit_cost=112` при ставке 12 % дают ОДНО
        и то же нетто — 100, при том же нормативе 100: обе работы обязаны показать
        отклонение 0, а не валовые 120/112. Две РАЗНЫЕ ставки в одном классе
        проверяют ещё и группировку по `vat_rate_base` внутри работы, а не только
        единственный вызов `gross_to_net`.
        """
        rate_class = factories.RateClassFactory.create(title="Класс с реальным НДС")

        contract20 = factories.ContractFactory.create(rate_class=rate_class)
        _c1, _e1, p20 = _estimate_with(factories, contract=contract20, vat_rate=Decimal("20"))
        work20 = factories.CatalogPositionFactory.create(standard_job_title="Ставка 20")
        _position(factories, p20, work20, unit_cost="120", weight="10")
        _standard(factories, work20, rate_class, "100")

        contract12 = factories.ContractFactory.create(rate_class=rate_class)
        _c2, _e2, p12 = _estimate_with(factories, contract=contract12, vat_rate=Decimal("12"))
        work12 = factories.CatalogPositionFactory.create(standard_job_title="Ставка 12")
        _position(factories, p12, work12, unit_cost="112", weight="10")
        _standard(factories, work12, rate_class, "100")

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )

        row20, _ = _find_row(ws, lambda r: r[0] == "Ставка 20")
        assert Decimal(str(ws.cell(row=row20, column=4).value)) == Decimal("100")  # Ставка (нетто)
        assert Decimal(str(ws.cell(row=row20, column=6).value)) == Decimal("1000")  # Стоимость
        assert Decimal(str(ws.cell(row=row20, column=7).value)) == Decimal("0")  # Отклонение, %
        assert Decimal(str(ws.cell(row=row20, column=8).value)) == Decimal("0")  # Отклонение, ₽

        row12, _ = _find_row(ws, lambda r: r[0] == "Ставка 12")
        assert Decimal(str(ws.cell(row=row12, column=4).value)) == Decimal("100")
        assert Decimal(str(ws.cell(row=row12, column=6).value)) == Decimal("1000")
        assert Decimal(str(ws.cell(row=row12, column=7).value)) == Decimal("0")
        assert Decimal(str(ws.cell(row=row12, column=8).value)) == Decimal("0")

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
        assert "Позиций с объёмом, но без норматива (в отклонение не вошли): 1" in text
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
        assert "Позиций с объёмом, но без норматива (в отклонение не вошли): 1" in _text_of(ws)

    def test_counter_is_printed_even_when_zero(self, client, factories):
        """Ноль печатается: отсутствие строки читалось бы как «не проверяли»."""
        first_class, _s, _a, _b = self._two_classes(factories)
        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": first_class.id})
        )
        text = _text_of(ws)
        assert "Позиций с объёмом, но без норматива (в отклонение не вошли): 0" in text
        # Тот же принцип для позиций без объёма (находка собственного ревью).
        assert "но без объёма (в расчёт не вошли): 0" in text

    def test_class_with_only_volume_less_positions_still_has_a_section(self, client, factories):
        """Класс, где все расценённые позиции без объёма, не исчезает из отчёта.

        **Замечание внешнего ревью на код собственного ревью.** Секции строились
        только из строк, прошедших `weight > 0`, а счётчик «без объёма» был одним
        общим числом. В итоге шапка говорила «Классов: 1», а секции этого класса не
        было вовсе — класс исчезал молча, нарушая согласованный макет «итоги по
        каждому классу и общий».
        """
        ghost_class = factories.RateClassFactory.create(title="Класс из призраков")
        contract = factories.ContractFactory.create(rate_class=ghost_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)
        ghost = factories.CatalogPositionFactory.create(standard_job_title="Только без объёма")
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=ghost,
            unit_cost_total=Decimal("500"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=None,
        )

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": ghost_class.id})
        )
        text = _text_of(ws)

        # Шапка считает класс — значит и секция обязана быть.
        assert "Классов: 1" in text
        assert "КЛАСС: Класс из призраков" in text
        assert "ИТОГО ПО КЛАССУ «Класс из призраков»" in text
        # Счётчик «без объёма» — по классу, а не только общим числом.
        assert "но без объёма (в расчёт не вошли): 1" in text
        # И итог не утверждает «отклонение 0» там, где сравнивать нечего.
        row_index, _row = _find_row(
            ws, lambda r: r[0] == "ИТОГО ПО КЛАССУ «Класс из призраков»"
        )
        assert ws.cell(row=row_index, column=8).value != 0

    def test_both_missing_position_is_counted_once_and_honestly(self, client, factories):
        """Позиция без объёма И без норматива не даёт ложного «без норматива: 0».

        **Замечание внешнего ревью.** Прежняя подпись «Позиций без норматива: 0» для
        файла, где есть позиция без норматива (но и без объёма), была ложью: счётчик
        считался после фильтра `weight > 0`. Решение — не пересечение счётчиков, а
        разбиение с точной подписью: «с объёмом, но без норматива». Позиция без
        объёма считается один раз, в счётчике объёма, — это блокирующая причина,
        норматив ей не помог бы.
        """
        rate_class = factories.RateClassFactory.create(title="Класс разбиения")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)
        compared = factories.CatalogPositionFactory.create(standard_job_title="Сравнимая")
        _position(factories, proposal, compared, unit_cost="100", weight="10")
        _standard(factories, compared, rate_class, "100")

        both_missing = factories.CatalogPositionFactory.create(standard_job_title="Без всего")
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=both_missing,
            unit_cost_total=Decimal("999"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=None,
        )

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        text = _text_of(ws)

        # Подпись точная, и ноль в ней — правда: позиций С ОБЪЁМОМ без норматива нет.
        assert "с объёмом, но без норматива (в отклонение не вошли): 0" in text
        assert "но без объёма (в расчёт не вошли): 1" in text
        # Разбиение сходится и здесь: 1 сравнимая + 0 + 1 без объёма = 2.
        assert "Сравнимых позиций (в расчёте отклонения): 1" in text
        assert "Всего расценённых позиций: 2" in text
        # Старой двусмысленной подписи больше нет. Проверка ищет подпись БЕЗ
        # уточнения «с объёмом» как отдельную строку файла: новая подпись содержит
        # старую как подстроку, поэтому сравниваются целые строки, а не вхождение.
        lines = text.split("\n")
        assert not any(
            line.split(" | ")[0].startswith("Позиций без норматива") for line in lines
        )

    def test_partition_is_checkable_from_the_file(self, client, factories):
        """Разбиение позиций обязано проверяться сложением ЧИСЕЛ ИЗ ФАЙЛА.

        **Замечание внешнего ревью (третий круг).** Строка отчёта агрегирована по
        каталожной работе: две сравнимые позиции одной работы дают одну видимую
        строку, оба счётчика исключённого — нули, и обещанное «строки + два счётчика
        = все позиции» по файлу не сходилось. Само разбиение в backend было верным;
        ложным было обещание его проверяемости.

        Теперь в итогах печатается счётчик сравнимых позиций, а в сноске —
        независимо посчитанное «Всего расценённых позиций»: равенство проверяется
        сложением трёх напечатанных чисел.
        """
        rate_class = factories.RateClassFactory.create(title="Класс арифметики")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)

        # Работа с ДВУМЯ сравнимыми позициями — пример из замечания: одна строка в файле.
        twice = factories.CatalogPositionFactory.create(standard_job_title="Дважды сравнимая")
        _position(factories, proposal, twice, unit_cost="100", weight="30")
        _position(factories, proposal, twice, unit_cost="200", weight="20")
        _standard(factories, twice, rate_class, "100")

        # Позиция с объёмом, но без норматива.
        no_std = factories.CatalogPositionFactory.create(standard_job_title="Без норматива")
        _position(factories, proposal, no_std, unit_cost="50", weight="5")

        # Позиция без объёма вовсе.
        ghost = factories.CatalogPositionFactory.create(standard_job_title="Призрак")
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=ghost,
            unit_cost_total=Decimal("999"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=None,
        )

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        text = _text_of(ws)

        # Видимая строка данных одна, но позиций за ней две — и это напечатано.
        assert "Сравнимых позиций (в расчёте отклонения): 2" in text
        assert "с объёмом, но без норматива (в отклонение не вошли): 1" in text
        assert "но без объёма (в расчёт не вошли): 1" in text
        # Общий счёт посчитан НЕЗАВИСИМО (count по VIEW, не сумма счётчиков):
        # равенство 2 + 1 + 1 = 4 — проверяемый инвариант, а не тавтология.
        assert "Всего расценённых позиций: 4" in text

    def test_bank_report_partition_covers_every_priced_position(self, client, factories):
        """Четыре класса образуют разбиение: сумма сходится с независимым счётчиком.

        Задача 4 пересчёта НДС расширяет разбиение с трёх частей до четырёх — здесь
        по одной позиции каждого класса: без объёма, с объёмом но без базы НДС, с
        объёмом и базой но без норматива, сравнимая.
        """
        rate_class = factories.RateClassFactory.create(title="Класс всех четырёх")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, estimate, proposal = _estimate_with(factories, contract=contract)

        comparable = factories.CatalogPositionFactory.create(standard_job_title="Сравнимая")
        _position(factories, proposal, comparable, unit_cost="100", weight="10")
        _standard(factories, comparable, rate_class, "100")

        no_standard = factories.CatalogPositionFactory.create(standard_job_title="Без норматива")
        _position(factories, proposal, no_standard, unit_cost="50", weight="5")

        no_volume = factories.CatalogPositionFactory.create(standard_job_title="Без объёма")
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=no_volume,
            unit_cost_total=Decimal("999"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=None,
        )

        # База НДС неизвестна — отдельное предложение той же сметы, ставка не
        # заявлена (`vat_rate=None`). Норматив у неё ЕСТЬ (нарочно): приоритет §7.6
        # ставит неизвестную базу выше отсутствия норматива, и позиция обязана
        # попасть именно в счётчик базы, а не в «сравнимые» и не в «без норматива».
        unknown_base_lot = factories.LotFactory.create(estimate=estimate)
        unknown_base_proposal = factories.ProposalFactory.create(
            lot=unknown_base_lot, contractor=contract.contractor, vat_rate=None
        )
        unknown_base = factories.CatalogPositionFactory.create(standard_job_title="Без базы НДС")
        _position(factories, unknown_base_proposal, unknown_base, unit_cost="80", weight="8")
        _standard(factories, unknown_base, rate_class, "70")

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        text = _text_of(ws)

        assert "Сравнимых позиций (в расчёте отклонения): 1" in text
        assert "Позиций с объёмом, но без базы НДС (в отклонение не вошли): 1" in text
        assert "с объёмом, но без норматива (в отклонение не вошли): 1" in text
        assert "но без объёма (в расчёт не вошли): 1" in text
        # Независимый общий счёт: 1 + 1 + 1 + 1 = 4 — проверяемый инвариант файла.
        assert "Всего расценённых позиций: 4 (равно сумме четырёх счётчиков выше)" in text

    def test_position_without_volume_and_without_base_counts_once(self, client, factories):
        """Приоритет блокирующей причины: объём перевешивает базу НДС (задача 4).

        Позиция без обоих (объёма и базы) считается ОДИН раз, в счётчике объёма —
        тот же довод §7.6, что и для пары «объём/норматив»: норматив (здесь —
        база НДС) без объёма не помог бы ничем.
        """
        rate_class = factories.RateClassFactory.create(title="Класс приоритета базы")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract, vat_rate=None)
        ghost = factories.CatalogPositionFactory.create(standard_job_title="Без объёма и без базы")
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=ghost,
            unit_cost_total=Decimal("777"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=None,
        )

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        text = _text_of(ws)

        assert "но без объёма (в расчёт не вошли): 1" in text
        assert "Позиций с объёмом, но без базы НДС (в отклонение не вошли): 0" in text

    def test_bank_counts_priced_positions_without_volume(self, client, factories):
        """Счётчик «без объёма» работает и в отчёте «для банка», по выборке."""
        rate_class = factories.RateClassFactory.create(title="Класс с призраком")
        contract = factories.ContractFactory.create(rate_class=rate_class)
        _c, _e, proposal = _estimate_with(factories, contract=contract)
        normal = factories.CatalogPositionFactory.create(standard_job_title="Нормальная работа")
        _position(factories, proposal, normal, unit_cost="100", weight="10")
        _standard(factories, normal, rate_class, "100")

        ghost = factories.CatalogPositionFactory.create(standard_job_title="Призрак без объёма")
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=ghost,
            unit_cost_total=Decimal("500"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=None,
        )

        ws = _sheet(
            client.get("/api/v1/reports/bank-comparison", params={"rate_class_id": rate_class.id})
        )
        text = _text_of(ws)
        assert "Призрак без объёма" not in text
        assert "но без объёма (в расчёт не вошли): 1" in text

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


def test_fold_bank_rows_breaks_ties_by_catalog_position_id_ascending():
    """Юнит-тест на саму сортировку `_fold_bank_rows` — не на её эмерджентное
    проявление через целый HTTP-запрос (ре-ревью задачи 4, третий круг).

    **Почему прежний интеграционный тест был вакуозным.** Он гонял ДВЕ строки
    через весь конвейер (фабрики → SQL → fold → xlsx) и совпадал с ожидаемым
    порядком СЛУЧАЙНО: сортировка Python устойчива (stable — при равных ключах
    сохраняет порядок входа), а `_bank_position_groups_select` не несёт
    `ORDER BY` и отдаёт группы в порядке вставки — который как раз совпадал с
    ожидаемым порядком по `catalog_position_id`, потому что тестовая работа
    «А» создавалась раньше «Б». Сняв тай-брейк из `_fold_bank_rows`, ревьюер
    прогнал тот тест пять раз подряд — все пять зелёные: тест не отличал
    «защита есть» от «защиты нет» (`docs/insights/verifying-guards.md`, слой 7).

    **Как это исправлено.** Вход собран НАПРЯМУЮ для `_fold_bank_rows` (минуя
    HTTP, SQL и фабрики), и порядок специально сделан ПРОТИВОПОЛОЖНЫМ
    ожидаемому: группа с БОЛЬШИМ `catalog_position_id` (200) идёт ПЕРВОЙ, с
    МЕНЬШИМ (100) — второй, а суммы (`weighted_fact_gross`/`weight_comparable`/
    `weighted_standard`) у обеих групп ОДИНАКОВЫЕ, чтобы порядок решался
    ИСКЛЮЧИТЕЛЬНО тай-брейком, а не разницей сумм. Устойчивая сортировка без
    тай-брейка сохранила бы вход как есть → `[200, 100]` на выходе (тест обязан
    покраснеть, см. проверку снятием в отчёте task-4-report.md); с тай-брейком
    (`crud/reports.py::_fold_bank_rows`, ключ `(-amount, catalog_position_id)`)
    выход обязан быть `[100, 200]` — по возрастанию id, независимо от входа.
    """
    from types import SimpleNamespace

    from crud.reports import _fold_bank_rows

    def group(catalog_position_id: int) -> SimpleNamespace:
        """Одна группа работа×база (форма строки `_bank_position_groups_select`)."""
        return SimpleNamespace(
            rate_class_id=1,
            catalog_position_id=catalog_position_id,
            job_title=f"Работа {catalog_position_id}",
            unit_code="шт",
            vat_rate_base=Decimal("0"),
            weighted_fact_gross=Decimal("1000"),
            weight_comparable=Decimal("10"),
            weighted_standard=Decimal("1000"),
            positions=1,
            positions_without_standard=0,
        )

    # Вход НАРОЧНО в порядке УБЫВАНИЯ id (200, потом 100) — обратно ожидаемому.
    rows_by_class = _fold_bank_rows([group(200), group(100)])

    ids_in_order = [r["catalog_position_id"] for r in rows_by_class[1]]
    assert ids_in_order == [100, 200]


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
