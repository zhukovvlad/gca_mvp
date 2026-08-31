"""Чистый расчёт разложения статьи (спека 2026-08-30-position-drilldown-design.md
§2.2–§2.6, §2.9, §2.13). Без БД: литералы на входе и выходе."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from services import position_drilldown as pd
from services import stage_summary as ss

D = Decimal


def col(idx: int, rate: str | None = "20") -> pd.DrillColumn:
    return pd.DrillColumn(offer_id=idx, estimate_id=100 + idx, round_id=10 + idx, stage_no=idx + 1,
                          label=None, held_on=dt.date(2026, 3, 1 + idx),
                          vat_rate_base=None if rate is None else D(rate))


COLS4 = [col(0), col(1), col(2), col(3)]
BASIS = ss.pick_tax_basis([c.vat_rate_base for c in COLS4])


def grp(kind=pd.KIND_POSITION, cpid=1, ref=None, title="Работа", key=None, **stages) -> pd.GroupInput:
    """stages: s0=..., s1=... — GroupStage по индексу колонки."""
    return pd.GroupInput(kind=kind, catalog_position_id=cpid if kind == pd.KIND_POSITION else None,
                         chapter_ref_raw=ref, title=title,
                         stages={int(k[1:]): v for k, v in stages.items()},
                         key=key or ((cpid,) if kind == pd.KIND_POSITION else (ref,)))


class TestGroupCells:
    def test_absent_iff_zero_estimate_rows(self):
        g = grp(s0=pd.GroupStage(D("100"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert [c.state for c in cells] == ["amount", "absent", "absent", "amount"]
        assert [(c.estimate_rows == 0) == (c.state == "absent") for c in cells] == [True] * 4

    def test_hole_in_the_middle_is_disappeared_then_appeared_not_removed(self):
        """Дыра §2.5: средние этапы absent, переходы disappeared/appeared,
        «снято» не появляется ни разу."""
        g = grp(s0=pd.GroupStage(D("100"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert cells[1].change.kind == "disappeared"
        assert cells[3].change.kind == "appeared"
        assert all(c.change.kind != "removed" and c.state != "removed" for c in cells)

    def test_zero_after_amount_is_removed_and_zero_before_is_not_evaluated(self):
        g = grp(s0=pd.GroupStage(D("0"), 1), s1=pd.GroupStage(D("50"), 1),
                s2=pd.GroupStage(D("0"), 1), s3=pd.GroupStage(D("0"), 1))
        states = [c.state for c in pd.group_cells(g, COLS4, BASIS)]
        assert states == ["not_evaluated", "amount", "removed", "removed"]

    def test_appeared_is_impossible_on_first_column(self):
        g = grp(s0=pd.GroupStage(D("100"), 1))
        first = pd.group_cells(g, COLS4, BASIS)[0]
        assert first.change.kind == "none" and first.change.reason == "first_column"

    def test_quantity_changed_compares_with_previous_present_stage(self):
        """Как mark_volume_steps генератора: absent-этап пропускается, «предыдущий»
        — предыдущий этап ПРИСУТСТВИЯ."""
        g = grp(s0=pd.GroupStage(D("10"), 1, (D("6"),), "шт"),
                s2=pd.GroupStage(D("10"), 1, (D("6"),), "шт"),
                s3=pd.GroupStage(D("10"), 2, (D("6"), D("11")), "шт"))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert [c.quantity_changed for c in cells] == [False, False, False, True]

    def test_unknown_middle_column_carries_reason_and_no_amount(self):
        cols = [col(0), col(1, rate=None), col(2), col(3)]
        basis = ss.pick_tax_basis([c.vat_rate_base for c in cols])
        g = grp(s0=pd.GroupStage(D("100"), 1), s1=pd.GroupStage(D("90"), 1),
                s2=pd.GroupStage(D("80"), 1), s3=pd.GroupStage(D("70"), 1))
        cells = pd.group_cells(g, cols, basis)
        assert cells[1].shown is None and cells[1].unavailable_reason == "unknown_vat_base"
        assert cells[0].shown == D("100") and cells[2].unavailable_reason is None

    def test_net_axis_recomputes_shown_per_column_rate(self):
        cols = [col(0, "20"), col(1, "22")]
        basis = ss.pick_tax_basis([c.vat_rate_base for c in cols])
        g = grp(s0=pd.GroupStage(D("120"), 1), s1=pd.GroupStage(D("122"), 1))
        cells = pd.group_cells(g, cols, basis)
        assert cells[0].shown == D("100") and cells[1].shown == D("100")

    def test_removed_then_amount_is_reappeared(self):
        g = grp(s0=pd.GroupStage(D("100"), 1), s1=pd.GroupStage(D("0"), 1), s2=pd.GroupStage(D("100"), 1))
        cells = pd.group_cells(g, COLS4[:3], BASIS)
        assert cells[1].state == "removed"
        assert cells[2].change.kind == "reappeared"

    def test_money_at_is_zero_for_absent_and_zero_states(self):
        g = grp(s0=pd.GroupStage(D("0"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert pd.money_at(cells[0]) == D("0")   # not_evaluated
        assert pd.money_at(cells[1]) == D("0")   # absent
        assert pd.money_at(cells[3]) == D("90")


def one_stage_grp(idx_amounts: dict[int, str], *, kind=pd.KIND_POSITION, cpid=1, ref=None,
                  title="Работа", rows=1, key=None) -> pd.GroupInput:
    return pd.GroupInput(kind=kind, catalog_position_id=cpid if kind == pd.KIND_POSITION else None,
                         chapter_ref_raw=ref, title=title,
                         stages={i: pd.GroupStage(D(a), rows) for i, a in idx_amounts.items()},
                         key=key or ((cpid,) if kind == pd.KIND_POSITION else (ref,)))


COLS2 = [col(0), col(3)]
BASIS2 = ss.pick_tax_basis([c.vat_rate_base for c in COLS2])


class TestComputeDrilldown:
    def test_explainers_stop_when_cumulative_covers_90_percent(self):
        groups = [one_stage_grp({0: "100", 1: "10"}, cpid=1),      # вклад -90
                  one_stage_grp({0: "50", 1: "45"}, cpid=2),       # вклад -5
                  one_stage_grp({0: "30", 1: "29"}, cpid=3)]       # вклад -1
        out = pd.compute_drilldown(COLS2, groups)
        # delta = -96; после первой группы |−96 − (−90)| = 6 <= 9.6 — хватает одной
        assert out.rows[0].catalog_position_id == 1
        assert out.rows[-1].kind == pd.KIND_REST and out.rows[-1].group_count == 2

    def test_zero_delta_takes_no_explainers_but_shows_other_classes(self):
        groups = [one_stage_grp({0: "50", 1: "50"}, cpid=1),
                  one_stage_grp({1: "20"}, cpid=2),                 # появилась: класс 2
                  one_stage_grp({0: "20"}, cpid=3)]                 # исчезла: класс 2
        out = pd.compute_drilldown(COLS2, groups)
        kinds = [(r.kind, r.catalog_position_id) for r in out.rows]
        assert (pd.KIND_POSITION, 1) not in kinds                   # объяснителей нет
        assert {(k, i) for k, i in kinds if k == pd.KIND_POSITION} == {
            (pd.KIND_POSITION, 2), (pd.KIND_POSITION, 3)}
        assert out.rows[-1].kind == pd.KIND_REST

    def test_full_order_key_breaks_ties_by_kind_then_numeric_id_then_ref(self):
        groups = [one_stage_grp({0: "0", 1: "10"}, cpid=10),
                  one_stage_grp({0: "0", 1: "10"}, cpid=2),
                  one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="3.2.2"),
                  one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="3.2.10")]
        out = pd.compute_drilldown(COLS2, groups)
        head = [(r.kind, r.catalog_position_id, r.chapter_ref_raw) for r in out.rows[:4]]
        # равный |вклад| 10: позиции раньше допработ; id числом (2 < 10); ссылки строкой
        assert head == [("position", 2, None), ("position", 10, None),
                        ("additional_works", None, "3.2.10"), ("additional_works", None, "3.2.2")]

    def test_two_lots_with_one_ref_are_ordered_deterministically(self):
        """Следствие ревизии ключа §2.7: у двух допработ РАЗНЫХ лотов ссылка
        одна, вклад равный — тройка «модуль, вид, ссылка» их не разводит вовсе,
        и порядок зависел бы от выдачи БД. Разводит ключ группировки."""
        a = one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="1",
                          key=("lot_2", "1"), title="Работа лота 2")
        b = one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="1",
                          key=("lot_1", "1"), title="Работа лота 1")
        straight = [r.title for r in pd.compute_drilldown(COLS2, [a, b]).rows]
        flipped = [r.title for r in pd.compute_drilldown(COLS2, [b, a]).rows]
        assert straight == flipped
        assert straight[:2] == ["Работа лота 1", "Работа лота 2"]

    def test_row_key_is_unique_even_when_the_ref_is_shared(self):
        """§2.11: идентичность строки — `row_key`, а не пара kind + ссылка. У двух
        допработ разных лотов ссылка одна, и ключ, собранный из полей контракта,
        схлопнул бы строки на экране."""
        a = one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="1",
                          key=("lot_1", "1"), title="Работа лота 1")
        b = one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="1",
                          key=("lot_2", "1"), title="Работа лота 2")
        rows = pd.compute_drilldown(COLS2, [a, b]).rows
        keys = [r.row_key for r in rows]
        assert len(keys) == len(set(keys))                       # уникальны в ответе
        naive = [(r.kind, r.chapter_ref_raw) for r in rows if r.kind == pd.KIND_ADDITIONAL_WORKS]
        assert len(set(naive)) == 1                              # наивный ключ бы схлопнул
        assert {r.lot_key for r in rows if r.kind == pd.KIND_ADDITIONAL_WORKS} == {"lot_1", "lot_2"}

    def test_partial_cap_folds_the_tail_into_collapsed_row(self):
        # big объясняет 112 из delta=124 один: |124−112| = 12 <= 12.4 — отбор
        # останавливается, и все 12 появившихся уходят во второй класс.
        big = one_stage_grp({0: "100", 1: "212"}, cpid=99)
        born = [one_stage_grp({1: "1"}, cpid=i) for i in range(1, 13)]   # 12 появившихся
        out = pd.compute_drilldown(COLS2, [big] + born)
        collapsed = [r for r in out.rows if r.kind == pd.KIND_COLLAPSED]
        assert len(collapsed) == 1 and collapsed[0].group_count == 2       # 12 - PARTIAL_CAP
        assert collapsed[0].bargain.kind == "none" and collapsed[0].contribution.value is None
        named_born = [r for r in out.rows
                      if r.kind == pd.KIND_POSITION and r.catalog_position_id != 99
                      and r.contribution.value == D("1")]
        assert len(named_born) == pd.PARTIAL_CAP                            # десять поимённо
        # Строки «прочие» здесь быть НЕ ДОЛЖНО: свёрнутые в мешок группы уже
        # показаны и в третий класс не возвращаются. Без этой проверки дефект
        # разбиения невидим — «прочие» с нулевым остатком не ломают сходимость
        # (ревью плана 31.08.2026).
        assert all(r.kind != pd.KIND_REST for r in out.rows)

    def test_every_group_lands_in_exactly_one_class(self):
        """Инвариант разбиения, который сходимость НЕ проверяет: каждая входная
        группа показана ровно один раз — поимённо либо внутри одной свёрнутой."""
        groups = ([one_stage_grp({0: "100", 1: "212"}, cpid=99)]
                  + [one_stage_grp({1: "1"}, cpid=i) for i in range(1, 13)]      # 12 класса 2
                  + [one_stage_grp({0: "7", 1: "7"}, cpid=i) for i in range(20, 25)])  # 5 в «прочие»
        out = pd.compute_drilldown(COLS2, groups)
        named = sum(1 for r in out.rows if r.group_count is None)
        folded = sum(r.group_count for r in out.rows if r.group_count is not None)
        assert named + folded == len(groups)
        rest = next(r for r in out.rows if r.kind == pd.KIND_REST)
        assert rest.group_count == 5

    def test_full_order_places_explainers_before_named_before_folded_before_rest(self):
        """§2.9: порядок строк целиком — объяснители, затем поимённые класса 2,
        затем мешок (`KIND_COLLAPSED`), затем «прочие» (`KIND_REST`). Тесты
        выше проверяют лишь первую/последнюю строку (сценарии с двумя
        строками, где край не отличить от целого) либо состав через
        фильтры/`next()`, которые от позиции не зависят — сам порядок нигде не
        проверен. Конструкция — как в `test_every_group_lands_in_exactly_one_class`:
        big объясняет 112 из delta=124 один: |124−112|=12<=12.4 — отбор
        останавливается на первой строке; 12 появившихся уходят во второй
        класс (10 поимённо, 2 в мешок — 12 - PARTIAL_CAP); 5 полных групп с
        нулевым вкладом уходят в «прочие».

        Проверка по одному `kind` не развела бы объяснителя и поимённые класса
        2 — у обоих `kind == "position"` — поэтому вместе с видом проверяется
        `catalog_position_id`: объяснитель (99) обязан стоять ПЕРЕД поимёнными
        (1..10), а не после."""
        groups = ([one_stage_grp({0: "100", 1: "212"}, cpid=99)]
                  + [one_stage_grp({1: "1"}, cpid=i) for i in range(1, 13)]      # 12 класса 2
                  + [one_stage_grp({0: "7", 1: "7"}, cpid=i) for i in range(20, 25)])  # 5 в «прочие»
        out = pd.compute_drilldown(COLS2, groups)
        assert [(r.kind, r.catalog_position_id) for r in out.rows] == (
            [(pd.KIND_POSITION, 99)] + [(pd.KIND_POSITION, i) for i in range(1, 11)]
            + [(pd.KIND_COLLAPSED, None), (pd.KIND_REST, None)])

    def test_rest_carries_the_remainder_and_convergence_is_exact(self):
        groups = [one_stage_grp({0: "100", 1: "10"}, cpid=1),
                  one_stage_grp({0: "33.33", 1: "33.33"}, cpid=2),
                  one_stage_grp({0: "66.67", 1: "66.67"}, cpid=3)]
        out = pd.compute_drilldown(COLS2, groups)
        rest = out.rows[-1]
        assert rest.kind == pd.KIND_REST
        for conv, idx in zip(out.convergence, range(2), strict=True):
            shown = sum(pd.money_at(r.cells[idx]) for r in out.rows)
            assert conv.converged is True and conv.shown_sum == conv.article_amount == shown

    def test_net_axis_multi_group_convergence_is_exact(self):
        """Круг ревью Task 6: reviewer предлагал вместо квантования перед `==`
        накапливать `shown_sum` в высокой точности `_VAT_CONTEXT` (как
        `crud/comparison.py::own_net`, не начиная с `Decimal(0)` под амбиентным
        контекстом) — якобы это даёт точное равенство БЕЗ допуска. Замерено: так
        и есть, но ТОЛЬКО пока показанная строка одна (ровно случай
        интеграционного теста Task 6, `TestTaxAxis` — там объяснитель ровно один,
        и накопление одного слагаемого неотличимо от самого слагаемого). При
        НЕСКОЛЬКИХ показанных строках высокоточное накопление точное равенство НЕ
        восстанавливает: Σ отдельно округлённых на пределе точности слагаемых —
        не то же самое число, что округление их суммы на том же пределе (не
        ассоциативно). Четыре суммы ниже (ставка 22 %) — конкретный найденный
        перебором контрпример: `gross_to_net(Σx)` и `Σ gross_to_net(x)`
        расходятся В ПОСЛЕДНЕМ (100-м) знаке (`...5738` против `...5737`) — при
        сравнении высокоточным `==` это красит `converged` в `False`, при
        квантовании до копеек — нет, обе стороны дают одну и ту же сумму. На
        3000 случайных многогрупповых наборов (четыре ставки НДС) высокоточное
        накопление расходилось с `article_shown` 1196 раз (40%, расхождения до
        3e-94) — квантованное сравнение не разошлось ни разу."""
        cols = [col(0, "20"), col(1, "22")]
        basis = ss.pick_tax_basis([c.vat_rate_base for c in cols])
        assert basis.basis == ss.TAX_NET
        amounts = ["7722.47", "1074.74", "7095.71", "7766.47"]   # не делятся на 22% нацело
        groups = [one_stage_grp({0: a, 1: a}, cpid=i) for i, a in enumerate(amounts, start=1)]
        out = pd.compute_drilldown(cols, groups)
        assert all(c.converged is True for c in out.convergence)

    def test_empty_groups_is_no_rows_in_subtree(self):
        out = pd.compute_drilldown(COLS2, [])
        assert out.reason == pd.REASON_NO_ROWS_IN_SUBTREE and out.rows == [] and out.convergence == []

    def test_unknown_endpoint_refuses_with_unknown_vat_base(self):
        cols = [col(0, rate=None), col(1)]
        out = pd.compute_drilldown(cols, [one_stage_grp({0: "10", 1: "20"})])
        assert out.reason == "unknown_vat_base" and out.rows == []

    def test_unknown_middle_column_builds_and_marks_only_that_column(self):
        cols = [col(0), col(1, rate=None), col(2)]
        g = pd.GroupInput(kind=pd.KIND_POSITION, catalog_position_id=1, chapter_ref_raw=None,
                          title="Работа", stages={0: pd.GroupStage(D("10"), 1),
                                                  1: pd.GroupStage(D("20"), 1),
                                                  2: pd.GroupStage(D("30"), 1)})
        out = pd.compute_drilldown(cols, [g])
        assert out.reason is None
        assert out.convergence[1].converged is None and out.convergence[1].reason == "unknown_vat_base"
        assert out.convergence[0].converged is True and out.convergence[2].converged is True

    def test_wholly_appeared_article_still_gets_a_drilldown(self):
        """Негативная §6.2: статья, целиком появившаяся (absent_endpoint у свода),
        разложение ПОЛУЧАЕТ — предикат не цепляется за contribution фичи 3."""
        out = pd.compute_drilldown(COLS2, [one_stage_grp({1: "100"}, cpid=1)])
        assert out.reason is None and len(out.rows) >= 1
        assert out.rows[0].bargain.kind == "appeared"
        assert out.rows[0].contribution.value == D("100")

    def test_bargain_of_appeared_row_is_a_pill_not_a_percent(self):
        out = pd.compute_drilldown(COLS2, [one_stage_grp({1: "100"}, cpid=1),
                                           one_stage_grp({0: "1", 1: "1"}, cpid=2)])
        row = next(r for r in out.rows if r.catalog_position_id == 1)
        assert row.bargain.kind == "appeared" and row.bargain.value is None

    def test_ambiguous_flag_from_any_stage_with_more_than_one_row(self):
        """«Любой этап», а не только край: на COLS2 (два столбца) множественность
        стоит на первом или на последнем этапе, и «проверяет все ячейки» не
        отличить от «проверяет только края». Здесь COLS4, множественность —
        на СРЕДНЕЙ (второй) колонке, крайние rows=1."""
        g = pd.GroupInput(kind=pd.KIND_POSITION, catalog_position_id=1, chapter_ref_raw=None,
                          title="Работа", stages={0: pd.GroupStage(D("10"), 1),
                                                  1: pd.GroupStage(D("20"), 2),
                                                  2: pd.GroupStage(D("15"), 1),
                                                  3: pd.GroupStage(D("25"), 1)})
        out = pd.compute_drilldown(COLS4, [g])
        assert out.rows[0].ambiguous is True

    def test_cancelling_contributions_do_not_stop_the_selection_early(self):
        """§6.1: критерий — близость НАКОПЛЕННОГО вклада к движению статьи, а не
        доля суммы модулей. Вклады +100 и −100 гасят друг друга, и после каждого
        по отдельности накопленное далеко от delta = +10, поэтому берутся все три.
        Порог по СУММЕ МОДУЛЕЙ (отвергнутая формулировка §3) остановился бы на
        первой строке: 100 из 210 модулей — и разложение объявило бы объяснителем
        строку, которая ничего не объясняет."""
        groups = [one_stage_grp({0: "0", 1: "100"}, cpid=1),      # +100
                  one_stage_grp({0: "100", 1: "0"}, cpid=2),      # -100
                  one_stage_grp({0: "0", 1: "10"}, cpid=3)]       # +10
        out = pd.compute_drilldown(COLS2, groups)
        explainers = [r.catalog_position_id for r in out.rows if r.group_count is None]
        assert sorted(explainers) == [1, 2, 3]
        assert all(r.kind != pd.KIND_REST for r in out.rows)

    def test_result_does_not_depend_on_the_order_of_input_groups(self):
        """§2.3: ключ порядка полный, поэтому выдача БД на результат не влияет.
        Без четвёртого компонента ключа (ссылка) две допработы с равным по модулю
        вкладом здесь встали бы в порядке входа — и тест покраснел бы."""
        groups = [one_stage_grp({0: "100", 1: "10"}, cpid=1),
                  one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="3.2.2"),
                  one_stage_grp({0: "10", 1: "20"}, kind=pd.KIND_ADDITIONAL_WORKS, ref="3.2.10"),
                  one_stage_grp({0: "5", 1: "5"}, cpid=7)]
        straight = pd.compute_drilldown(COLS2, groups)
        reversed_out = pd.compute_drilldown(COLS2, list(reversed(groups)))

        def shape(out):
            return [(r.kind, r.catalog_position_id, r.chapter_ref_raw, r.group_count) for r in out.rows]

        assert shape(straight) == shape(reversed_out)
