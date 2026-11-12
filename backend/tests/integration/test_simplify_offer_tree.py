"""Страница участника и панель строки на сервере: дерево статей, цепочки разделов,
итоги узлов, «где строка» у строки КП и у аналогов (спека упрощения каталога §2.10, §2.11).

Названия, номера и числа выдуманы. Эталоны — числа, посчитанные руками. Ставка КП —
20 %, ставка истории — 0 %, поэтому у истории нетто равно валовой цене. Каждое
правило имеет обе стороны: вход, где оно срабатывает, и соседний, где не должно.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import EstimateAdditionalWork, SemanticJob
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import (
    _get,
    _line_of,
    _ok,
    _panel,
    _panel_get,
)
from tests.integration.test_our_price_round_api import Stage, count_statements

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


def _chapter(stage_, proposal, title, number, *, parent=None, cat=None):
    """Глава сметы: вложенная, если задан `parent`; статья — по имени из `stage_.cats`."""
    return stage_.f.PositionItemFactory.create(
        proposal=proposal,
        is_chapter=True,
        job_title_in_proposal=title,
        item_number_in_proposal=number,
        chapter_item_id=None if parent is None else parent.id,
        unit_cost_total=None,
        total_cost_total=None,
        quantity=None,
        suggested_quantity=None,
        work_category_id=None if cat is None else stage_.cats[cat].id,
        category_source=None if cat is None else "manual",
    )


def _line_in(stage_, proposal, chapter, total, *, unit_cost=D("12"), qty=D("10")):
    """Строка варианта сцены в главе `chapter` (или без главы) с заданной суммой."""
    item = stage_.world.line(
        proposal,
        chapter=chapter,
        unit_cost=unit_cost,
        total=total,
        suggested=qty,
        quantity=qty,
        family=stage_.family,
        variant=stage_.variant,
    )
    stage_.db.execute(sa.delete(SemanticJob).where(SemanticJob.context_id == item.context_id))
    return item


def _history_contract(stage_):
    contract = stage_.world.contract(area=D("1000"), rate_class=stage_.rate_class)
    estimate, proposal = stage_.world.estimate(contract, vat_rate=D("0"))
    return contract, estimate, proposal


def _steps(*chapters):
    return [
        {"chapter_item_id": chapter.id, "title": chapter.job_title_in_proposal, "number": chapter.item_number_in_proposal}
        for chapter in chapters
    ]


def _tree_scene(stage_):
    """КП с НДС 20 %: смета «1» → раздел «1.2» → подраздел «1.2.3» со статьёй G (R → C → G);
    в подразделе строки с валовыми суммами 120 и −24."""
    rnd = stage_.round()
    part = stage_.participant(rnd, "Подрядчик А", vat=D("20"))
    root = _chapter(stage_, part.proposal, "Смета Т", "1")
    section = _chapter(stage_, part.proposal, "Раздел А", "1.2", parent=root)
    leaf = _chapter(stage_, part.proposal, "Подраздел Б", "1.2.3", parent=section, cat="G")
    plus = _line_in(stage_, part.proposal, leaf, D("120"))
    minus = _line_in(stage_, part.proposal, leaf, D("-24"))
    return rnd, part, (root, section, leaf), (plus, minus)


# ---------------------------------------------------------------------------
#  Статьи на путях строк и ссылки строки на дерево
# ---------------------------------------------------------------------------


class TestOfferCategories:
    def test_categories_are_the_nodes_on_the_paths_of_the_lines_and_no_others(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("100"), cat="G")  # путь R -> C -> G
        stage.kp(part, D("100"), cat="R2")  # отдельный корень

        body = _ok(member_client, stage.tender, rnd, part)

        cats = stage.cats
        assert sorted(entry["id"] for entry in body["categories"]) == sorted(
            [cats["R"].id, cats["C"].id, cats["G"].id, cats["R2"].id]
        )
        by_id = {entry["id"]: entry for entry in body["categories"]}
        assert by_id[cats["C"].id] == {
            "id": cats["C"].id,
            "parent_id": cats["R"].id,
            "code": "8801.1",
            "title": "Статья C",
            "sort_order": cats["C"].sort_order,
        }
        assert by_id[cats["R"].id]["parent_id"] is None

    def test_a_category_off_every_path_is_not_listed(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("100"), cat="C")  # путь R -> C, внук G и корень R2 не нужны

        body = _ok(member_client, stage.tender, rnd, part)

        listed = {entry["id"] for entry in body["categories"]}
        assert listed == {stage.cats["R"].id, stage.cats["C"].id}

    def test_a_line_carries_the_category_of_its_chapter_not_its_root(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        deep = stage.kp(part, D("100"), cat="G")
        loose = stage.kp(part, D("100"), cat=None)

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, deep)["category_id"] == stage.cats["G"].id
        assert _line_of(body, deep)["article_id"] == stage.cats["R"].id
        assert _line_of(body, loose)["category_id"] is None

    def test_lines_without_a_category_or_a_chapter_build_the_tree(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        no_chapter = _line_in(stage, part.proposal, None, D("10"))
        no_category = _line_in(stage, part.proposal, _chapter(stage, part.proposal, "Раздел Ф", "5"), D("10"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert body["categories"] == []
        assert _line_of(body, no_chapter)["category_id"] is None
        assert _line_of(body, no_chapter)["chapter_path"] == []
        assert _line_of(body, no_category)["category_id"] is None
        assert [step["number"] for step in _line_of(body, no_category)["chapter_path"]] == ["5"]


# ---------------------------------------------------------------------------
#  Путь по разделам сметы
# ---------------------------------------------------------------------------


class TestChapterPath:
    def test_the_path_runs_from_the_root_of_the_estimate_with_titles_and_numbers(self, member_client, stage):
        rnd, part, chapters, (plus, _) = _tree_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, plus)["chapter_path"] == _steps(*chapters)

    def test_a_line_in_a_middle_chapter_stops_at_that_chapter(self, member_client, stage):
        rnd, part, (root, section, _), _ = _tree_scene(stage)
        middle = _line_in(stage, part.proposal, section, D("1"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, middle)["chapter_path"] == _steps(root, section)

    def test_the_number_of_a_chapter_without_one_is_null(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        bare = _chapter(stage, part.proposal, "Раздел без номера", None)
        item = _line_in(stage, part.proposal, bare, D("1"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, item)["chapter_path"] == [
            {"chapter_item_id": bare.id, "title": "Раздел без номера", "number": None}
        ]


# ---------------------------------------------------------------------------
#  Итоги узлов
# ---------------------------------------------------------------------------


class TestNodeTotals:
    def test_plus_and_minus_lines_of_one_article_add_up_on_every_level(self, member_client, stage):
        rnd, part, chapters, _ = _tree_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)

        cats = stage.cats
        expected = {"lines": 2, "no_price": 0, "kp_total": {"net": "80.00", "shown": "96.00"}}
        for name in ("R", "C", "G"):
            assert body["node_totals"]["categories"][str(cats[name].id)] == expected, name
        for chapter in chapters:
            leaf_chapters = body["node_totals"]["chapters"][str(cats["G"].id)]
            assert leaf_chapters[str(chapter.id)] == expected, chapter.job_title_in_proposal
        assert set(body["node_totals"]["categories"]) == {str(cats[name].id) for name in ("R", "C", "G")}

    def test_a_chapter_total_covers_its_subtree_and_not_its_siblings(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        root = _chapter(stage, part.proposal, "Смета Т", "1")
        left = _chapter(stage, part.proposal, "Раздел Л", "1.1", parent=root)
        right = _chapter(stage, part.proposal, "Раздел П", "1.2", parent=root)
        deep = _chapter(stage, part.proposal, "Раздел Г", "1.1.1", parent=left)
        _line_in(stage, part.proposal, left, D("10"))
        _line_in(stage, part.proposal, deep, D("3"))
        _line_in(stage, part.proposal, right, D("100"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert set(body["node_totals"]["chapters"]) == {"null"}
        totals = body["node_totals"]["chapters"]["null"]
        assert {key: (node["lines"], node["kp_total"]["net"]) for key, node in totals.items()} == {
            str(root.id): (3, "113.00"),
            str(left.id): (2, "13.00"),
            str(deep.id): (1, "3.00"),
            str(right.id): (1, "100.00"),
        }

    def test_a_parent_chapter_gives_a_separate_entry_per_category_of_its_children(self, member_client, stage):
        """Раздел «1» держит подразделы двух статей: под каждой статьёй он есть отдельно и равен
        только её строкам; общая сумма раздела по всем статьям нигде не показана."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        parent = _chapter(stage, part.proposal, "Раздел Р", "1")
        child_g = _chapter(stage, part.proposal, "Подраздел Г", "1.1", parent=parent, cat="G")
        child_r2 = _chapter(stage, part.proposal, "Подраздел Д", "1.2", parent=parent, cat="R2")
        _line_in(stage, part.proposal, child_g, D("10"))
        _line_in(stage, part.proposal, child_g, D("1"))
        _line_in(stage, part.proposal, child_r2, D("200"))

        body = _ok(member_client, stage.tender, rnd, part)

        def brief(node):
            return node["lines"], node["kp_total"]["net"]

        chapters = body["node_totals"]["chapters"]
        g, r2 = str(stage.cats["G"].id), str(stage.cats["R2"].id)
        assert set(chapters) == {g, r2}
        assert {k: brief(n) for k, n in chapters[g].items()} == {
            str(parent.id): (2, "11.00"),
            str(child_g.id): (2, "11.00"),
        }
        assert {k: brief(n) for k, n in chapters[r2].items()} == {
            str(parent.id): (1, "200.00"),
            str(child_r2.id): (1, "200.00"),
        }

    def test_chapter_totals_under_a_leaf_category_never_include_another_category(self, member_client, stage):
        """Раздел, где лежат строки двух статей (свои строки в главе другой статьи): итог раздела
        под статьёй G не видит строки R2, а под R2 — строки G."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        shared = _chapter(stage, part.proposal, "Раздел Ш", "1", cat="G")
        other = _chapter(stage, part.proposal, "Подраздел Я", "1.1", parent=shared, cat="R2")
        _line_in(stage, part.proposal, shared, D("7"))
        _line_in(stage, part.proposal, other, D("500"))

        body = _ok(member_client, stage.tender, rnd, part)

        chapters = body["node_totals"]["chapters"]
        under_g = chapters[str(stage.cats["G"].id)]
        under_r2 = chapters[str(stage.cats["R2"].id)]
        assert under_g == {str(shared.id): {"lines": 1, "no_price": 0, "kp_total": {"net": "7.00", "shown": "7.00"}}}
        assert set(under_r2) == {str(shared.id), str(other.id)}
        assert all(node["kp_total"]["net"] == "500.00" for node in under_r2.values())

    def test_the_root_total_equals_the_article_of_the_page_with_a_negative_and_an_unpriced_line(
        self, member_client, stage
    ):
        """Сцена по правилу сумм статей: +120, −24 и строка без цены с суммой 36 дают валовые 132,
        нетто 110; голый предикат цены дал бы 156 / 1,2 — сумма корня перестала бы совпадать со статьёй."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        in_r = stage.chapter(part.proposal, "R")
        in_none = stage.chapter(part.proposal, None)
        _line_in(stage, part.proposal, in_r, D("120"))
        _line_in(stage, part.proposal, in_r, D("-24"))
        _line_in(stage, part.proposal, in_r, D("36"), unit_cost=D("0"))
        _line_in(stage, part.proposal, in_r, None, unit_cost=D("0"))
        _line_in(stage, part.proposal, in_none, D("60"))

        body = _ok(member_client, stage.tender, rnd, part)

        area = D("1000")
        categories = body["node_totals"]["categories"]
        for article in body["articles"]:
            root = article["work_category_id"]
            node = categories["null" if root is None else str(root)]
            from_articles = (D(article["per_sqm"]["net"]) * area).quantize(D("0.01"))
            assert D(node["kp_total"]["net"]) == from_articles, root
        assert categories[str(stage.cats["R"].id)] == {
            "lines": 4,
            "no_price": 2,
            "kp_total": {"net": "110.00", "shown": "132.00"},
        }
        assert categories["null"] == {"lines": 1, "no_price": 0, "kp_total": {"net": "50.00", "shown": "60.00"}}

    def test_an_additional_work_feeds_its_article_and_every_ancestor_like_the_root_of_articles(
        self, member_client, stage, db_session
    ):
        """Допработы — второй источник сумм статей (`v_category_totals`, `source =
        'additional_works'`, money-axes §7: «отрицательные и допработы тоже»). Позиция 120 в
        разделе статьи G и допработа 24 в G при НДС 20 %: корневая `articles` R = (120 + 24) / 1,2
        = 120 нетто; узлы G, C и R обязаны совпасть с ней (спека §2.10)."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        _line_in(stage, part.proposal, stage.chapter(part.proposal, "G"), D("120"))
        db_session.add(
            EstimateAdditionalWork(
                proposal_id=part.proposal.id,
                ordinal=1,
                title="Допработа Т",
                total_amount=D("24.00"),
                chapter_ref_raw="1",
                raw_line="строка сведений",
                work_category_id=stage.cats["G"].id,
            )
        )
        db_session.flush()

        body = _ok(member_client, stage.tender, rnd, part)

        (article,) = (a for a in body["articles"] if a["work_category_id"] == stage.cats["R"].id)
        assert D(article["per_sqm"]["net"]) * D("1000") == D("120")  # корень articles видит допработу
        categories = body["node_totals"]["categories"]
        for name in ("R", "C", "G"):
            assert categories[str(stage.cats[name].id)]["kp_total"] == {"net": "120.00", "shown": "144.00"}, name

    def test_an_unallocated_additional_work_feeds_the_unallocated_node(self, member_client, stage, db_session):
        """Нераспределённая запись допработ (`work_category_id IS NULL`) идёт в корень
        «Нераспределённое» у `articles`; тот же ключ `null` у узлов: (60 + 12) / 1,2 = 60 нетто."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        _line_in(stage, part.proposal, stage.chapter(part.proposal, None), D("60"))
        db_session.add(
            EstimateAdditionalWork(
                proposal_id=part.proposal.id, ordinal=1, title="Остаток Т", total_amount=D("12.00")
            )
        )
        db_session.flush()

        body = _ok(member_client, stage.tender, rnd, part)

        (article,) = (a for a in body["articles"] if a["work_category_id"] is None)
        assert D(article["per_sqm"]["net"]) * D("1000") == D("60")
        assert body["node_totals"]["categories"]["null"]["kp_total"] == {"net": "60.00", "shown": "72.00"}

    def test_an_article_with_only_additional_works_is_listed_with_its_ancestors(
        self, member_client, stage, db_session
    ):
        """Статья, где есть только допработа (строк нет): она и её предки есть и в `categories`,
        и в `node_totals`; допработа не считается строкой и в раздел не входит."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        _line_in(stage, part.proposal, stage.chapter(part.proposal, "R2"), D("12"))
        db_session.add(
            EstimateAdditionalWork(
                proposal_id=part.proposal.id,
                ordinal=1,
                title="Допработа Т",
                total_amount=D("24.00"),
                chapter_ref_raw="1",
                raw_line="строка сведений",
                work_category_id=stage.cats["G"].id,
            )
        )
        db_session.flush()

        body = _ok(member_client, stage.tender, rnd, part)

        cats = stage.cats
        assert {c["id"] for c in body["categories"]} == {cats[n].id for n in ("R", "C", "G", "R2")}
        for name in ("R", "C", "G"):
            assert body["node_totals"]["categories"][str(cats[name].id)] == {
                "lines": 0,
                "no_price": 0,
                "kp_total": {"net": "20.00", "shown": "24.00"},
            }, name
        assert all(
            node["lines"] == 1 for chapters in body["node_totals"]["chapters"].values() for node in chapters.values()
        )

    def test_a_node_without_a_finite_amount_has_no_total(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        chapter = stage.chapter(part.proposal, "R")
        _line_in(stage, part.proposal, chapter, None, unit_cost=D("0"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert body["node_totals"]["categories"][str(stage.cats["R"].id)] == {
            "lines": 1,
            "no_price": 1,
            "kp_total": None,
        }

    def test_a_zero_sum_is_zero_not_null(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        chapter = stage.chapter(part.proposal, "R")
        _line_in(stage, part.proposal, chapter, D("50"))
        _line_in(stage, part.proposal, chapter, D("-50"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert body["node_totals"]["categories"][str(stage.cats["R"].id)]["kp_total"] == {
            "net": "0.00",
            "shown": "0.00",
        }

    def test_an_unknown_vat_base_leaves_counts_and_no_total(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=None)
        chapter = stage.chapter(part.proposal, "R")
        _line_in(stage, part.proposal, chapter, D("120"))
        _line_in(stage, part.proposal, chapter, D("-24"))

        body = _ok(member_client, stage.tender, rnd, part)

        assert body["node_totals"]["categories"][str(stage.cats["R"].id)] == {
            "lines": 2,
            "no_price": 0,
            "kp_total": None,
        }
        assert body["node_totals"]["chapters"][str(stage.cats["R"].id)][str(chapter.id)]["kp_total"] is None

    def test_an_in_progress_line_is_summed_and_not_counted_as_unpriced(self, member_client, stage, db_session):
        """Счётчик «без цены» — состояние строки `no_price`: строка, которую ещё разбирает каталог,
        входит в сумму и в число строк, но не в счётчик."""
        from tests.integration.test_our_price_signal import _job

        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        chapter = stage.chapter(part.proposal, "R")
        busy = _line_in(stage, part.proposal, chapter, D("40"), unit_cost=D("0"))
        _job(db_session, "family_suggestion", "pending", context_id=busy.context_id)

        body = _ok(member_client, stage.tender, rnd, part)

        assert _line_of(body, busy)["state"] == "in_progress"
        assert body["node_totals"]["categories"][str(stage.cats["R"].id)] == {
            "lines": 1,
            "no_price": 0,
            "kp_total": {"net": "40.00", "shown": "40.00"},
        }

    def test_the_roots_of_articles_are_not_changed_by_the_tree(self, member_client, stage):
        rnd, part, _, _ = _tree_scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)

        (article,) = (a for a in body["articles"] if a["work_category_id"] == stage.cats["R"].id)
        assert set(article) == {
            "work_category_id",
            "code",
            "title",
            "sort_order",
            "per_sqm",
            "whole",
            "unit_mismatch_lines",
            "ours_class_per_sqm",
            "lines_totals",
        }


# ---------------------------------------------------------------------------
#  Панель: где строка
# ---------------------------------------------------------------------------


class TestPanelWhere:
    def test_the_kp_line_names_its_article_with_the_chain_from_the_root(self, member_client, stage):
        rnd, part, chapters, (plus, _) = _tree_scene(stage)

        panel = _panel(member_client, stage.tender, rnd, part, plus)

        cats = stage.cats
        assert panel["line"]["article"] == {
            "id": cats["G"].id,
            "code": "8801.1.1",
            "title": "Статья G",
            "chain": [
                {"id": cats["R"].id, "code": "8801", "title": "Статья R"},
                {"id": cats["C"].id, "code": "8801.1", "title": "Статья C"},
                {"id": cats["G"].id, "code": "8801.1.1", "title": "Статья G"},
            ],
        }
        assert panel["line"]["chapter_path"] == _steps(*chapters)
        assert panel["line"]["category_id"] == cats["G"].id

    def test_a_root_article_chain_is_itself_alone(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"), cat="R")

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert [step["id"] for step in panel["line"]["article"]["chain"]] == [stage.cats["R"].id]

    def test_a_line_without_an_article_has_none_and_an_empty_path(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = _line_in(stage, part.proposal, None, D("10"))

        panel = _panel(member_client, stage.tender, rnd, part, item)

        assert panel["line"]["article"] is None
        assert panel["line"]["chapter_path"] == []

    def test_the_analog_carries_the_article_and_the_path_of_its_own_contract_line(self, member_client, stage):
        """Строка КП — в статье R, аналог — в статье G другого раздела другой сметы: у аналога своя."""
        _, _, history = _history_contract(stage)
        root = _chapter(stage, history, "Смета Д", "1")
        section = _chapter(stage, history, "Раздел Д", "1.4", parent=root, cat="G")
        _line_in(stage, history, section, D("100"), unit_cost=D("10"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        item = stage.kp(part, D("144"), cat="R")

        panel = _panel(member_client, stage.tender, rnd, part, item)

        (analog,) = panel["analogs"]
        cats = stage.cats
        assert panel["line"]["article"]["id"] == cats["R"].id
        assert analog["article"] == {
            "id": cats["G"].id,
            "code": "8801.1.1",
            "title": "Статья G",
            "chain": [
                {"id": cats["R"].id, "code": "8801", "title": "Статья R"},
                {"id": cats["C"].id, "code": "8801.1", "title": "Статья C"},
                {"id": cats["G"].id, "code": "8801.1.1", "title": "Статья G"},
            ],
        }
        assert analog["chapter_path"] == _steps(root, section)

    def test_an_analog_without_a_chapter_has_no_article_and_an_empty_path(self, member_client, stage):
        _, _, history = _history_contract(stage)
        _line_in(stage, history, None, D("100"), unit_cost=D("10"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        item = stage.kp(part, D("144"), cat="R")

        panel = _panel(member_client, stage.tender, rnd, part, item)

        (analog,) = panel["analogs"]
        assert analog["article"] is None
        assert analog["chapter_path"] == []

    def test_the_panel_line_carries_the_same_path_as_the_offer(self, member_client, stage):
        rnd, part, _, (plus, _) = _tree_scene(stage)

        panel = _panel(member_client, stage.tender, rnd, part, plus)
        body = _ok(member_client, stage.tender, rnd, part)

        assert panel["line"]["chapter_path"] == _line_of(body, plus)["chapter_path"]
        assert panel["line"]["category_id"] == _line_of(body, plus)["category_id"]

    def test_group_lines_of_the_panel_carry_the_path_too(self, member_client, stage):
        """`group_lines` бывают только у систем. Строки группы лежат в РАЗНЫХ разделах: путь
        второй строки панель может взять, только если её раздел вошёл в пакетный запрос путей."""
        from tests.integration.test_our_price_offer_api import _history

        for total in (100000, 140000):
            _history(stage, D("1"), total=D(total), kind="SYSTEM")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        root = _chapter(stage, part.proposal, "Смета Т", "1")
        left = _chapter(stage, part.proposal, "Раздел Л", "1.1", parent=root, cat="G")
        right = _chapter(stage, part.proposal, "Раздел П", "1.2", parent=root, cat="C")
        mine, mate = (
            stage.world.line(
                part.proposal,
                chapter=chapter,
                unit_cost=D("1"),
                total=D(total),
                suggested=D("10"),
                quantity=D("10"),
                family=stage.family,
                variant=stage.variant,
                kind="SYSTEM",
            )
            for chapter, total in ((left, 30000), (right, 90000))
        )
        stage.db.execute(sa.delete(SemanticJob).where(SemanticJob.context_id.in_([mine.context_id, mate.context_id])))

        panel = _panel(member_client, stage.tender, rnd, part, mine)
        body = _ok(member_client, stage.tender, rnd, part)

        by_id = {line["position_item_id"]: line for line in panel["group_lines"]}
        assert set(by_id) == {mine.id, mate.id}
        assert by_id[mate.id]["chapter_path"] == _steps(root, right) == _line_of(body, mate)["chapter_path"]
        assert by_id[mate.id]["category_id"] == stage.cats["C"].id
        assert by_id[mine.id]["chapter_path"] == _steps(root, left)
        assert all("article" not in line for line in panel["group_lines"])


# ---------------------------------------------------------------------------
#  Чтение не пишет и не растёт числом запросов
# ---------------------------------------------------------------------------


class TestReadOnlyAndQueryCount:
    def test_the_offer_and_the_panel_write_nothing(self, member_client, stage, db_session):
        rnd, part, _, (plus, _) = _tree_scene(stage)
        with count_statements(db_session) as seen:
            assert _get(member_client, stage.tender, rnd, part).status_code == 200
            assert _panel_get(member_client, stage.tender, rnd, part, plus).status_code == 200

        writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert seen and writes == []

    def test_statements_do_not_grow_with_the_lines_chapters_and_analogs(self, member_client, stage, db_session):
        rnd, part, (root, section, leaf), (plus, _) = _tree_scene(stage)
        _, _, first_history = _history_contract(stage)
        first_root = _chapter(stage, first_history, "Смета Д", "1")
        _line_in(stage, first_history, _chapter(stage, first_history, "Раздел Д", "1.1", parent=first_root, cat="G"), D("100"), unit_cost=D("10"))
        with count_statements(db_session) as small:
            assert _get(member_client, stage.tender, rnd, part).status_code == 200
        with count_statements(db_session) as small_panel:
            assert _panel_get(member_client, stage.tender, rnd, part, plus).status_code == 200

        for index in range(6):
            sub = _chapter(stage, part.proposal, f"Раздел {index}", f"1.2.{index}", parent=section, cat="C")
            _line_in(stage, part.proposal, sub, D("10"))
            _, _, history = _history_contract(stage)
            hist_root = _chapter(stage, history, "Смета Д", "1")
            hist_leaf = _chapter(stage, history, f"Раздел Д{index}", f"1.{index}", parent=hist_root, cat="R2")
            _line_in(stage, history, hist_leaf, D("100"), unit_cost=D(10 + index))
        with count_statements(db_session) as large:
            body = _ok(member_client, stage.tender, rnd, part)
        with count_statements(db_session) as large_panel:
            panel = _panel(member_client, stage.tender, rnd, part, plus)

        assert len(body["lines"]) == 8
        assert len(panel["analogs"]) == 7
        assert len(small) == len(large)
        assert len(small_panel) == len(large_panel)
