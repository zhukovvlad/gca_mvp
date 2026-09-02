"""Чистый агрегатор этапного разноса (спека 2026-09-01-round-unallocated-design.md §2.2).
Без БД: литералы на входе и выходе."""
from __future__ import annotations

import datetime as dt
import itertools

import pytest

from services import round_unallocated as ru

T1 = dt.datetime(2026, 8, 29, 10, 0, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 8, 30, 10, 0, tzinfo=dt.UTC)


def vec(category=20, note=None, by=1, at=T1) -> ru.Vector:
    return ru.Vector(work_category_id=category, note=note, assigned_by=by, assigned_at=at)


class TestPendingStates:
    def test_pending_states_is_exactly_the_three_non_resolved_states(self):
        """Основа счётчика карточки `unallocated_pending_sections` (спека
        §2.6): добавление `STATE_RESOLVED` в `PENDING_STATES` проходило все
        прежние тесты зелёным, а бейдж этапа тогда никогда не обнулился бы
        после полного разноса."""
        assert ru.PENDING_STATES == (ru.STATE_UNASSIGNED, ru.STATE_PARTIAL, ru.STATE_CONFLICT)
        assert ru.STATE_RESOLVED not in ru.PENDING_STATES

    def test_state_literals_are_the_wire_contract_values(self):
        """Это контракт GET-ответа (спека §2.3: `'unassigned' | 'partial' |
        'conflict'` перечислены дословно), а не внутренние имена. Все
        остальные тесты файла ссылаются на константы символьно, и
        `TestPendingStates` выше сравнивает символ с символом — переименование
        `STATE_CONFLICT` в `"conflicts"` (и так же для трёх остальных)
        проходило все 26 тестов зелёными. Литералы пришпилены здесь явно,
        чтобы опечатка на проводе не всплыла только в тесте фронтенда через
        задачи — правило «проверять на уровне, где живёт дефект»."""
        assert ru.STATE_UNASSIGNED == "unassigned"
        assert ru.STATE_PARTIAL == "partial"
        assert ru.STATE_CONFLICT == "conflict"
        assert ru.STATE_RESOLVED == "resolved"


class TestClassify:
    def test_no_decisions_is_unassigned(self):
        c = ru.classify([None, None, None])
        assert (c.state, c.assigned, c.total) == (ru.STATE_UNASSIGNED, 0, 3)

    def test_some_decisions_is_partial_with_distinct_notes(self):
        c = ru.classify([vec(note="а"), None, vec(note="а"), vec(note=None)])
        assert (c.state, c.assigned, c.total) == (ru.STATE_PARTIAL, 3, 4)
        assert c.notes == ("а", None)

    def test_identical_vectors_everywhere_is_resolved(self):
        c = ru.classify([vec(), vec(), vec()])
        assert c.state == ru.STATE_RESOLVED
        assert c.categories == (20,) and c.audit_differs is False

    # Конфликт — по КАЖДОЙ компоненте вектора отдельно (§4.1): негативные к
    # «конфликт = разные статьи». Вход каждого теста нарушает ровно одну компоненту.
    def test_conflict_by_category_lists_both_categories_in_first_seen_order(self):
        c = ru.classify([vec(category=11), vec(category=10), vec(category=11)])
        assert c.state == ru.STATE_CONFLICT
        assert c.categories == (11, 10)
        assert c.notes == (None,) and c.audit_differs is False

    def test_conflict_by_note_only(self):
        c = ru.classify([vec(note="раз"), vec(note="два")])
        assert c.state == ru.STATE_CONFLICT
        assert c.categories == (20,) and c.notes == ("раз", "два") and c.audit_differs is False

    def test_conflict_by_author_only(self):
        c = ru.classify([vec(by=1), vec(by=2)])
        assert c.state == ru.STATE_CONFLICT and c.audit_differs is True
        assert c.categories == (20,) and c.notes == (None,)

    def test_conflict_by_time_only(self):
        c = ru.classify([vec(at=T1), vec(at=T2)])
        assert c.state == ru.STATE_CONFLICT and c.audit_differs is True

    def test_conflict_by_category_and_author_together_audit_still_differs(self):
        """Guard против вывода `audit_differs` из ОТСУТСТВИЯ прочих
        расхождений: `audit_differs = state==CONFLICT and len(categories)==1
        and len(notes)==1` проходил все 19 прежних утверждений файла, потому
        что ни один вектор не нарушал две компоненты полного вектора разом.
        Здесь статья И автор различаются одновременно — частичная регрессия
        находки ревью гейта 1 (§4.1: «конфликт по каждой компоненте вектора
        ОТДЕЛЬНО»), которая и завела правило полного вектора."""
        c = ru.classify([vec(category=11, by=1), vec(category=10, by=2)])
        assert c.state == ru.STATE_CONFLICT
        assert c.categories == (11, 10)
        assert c.audit_differs is True

    def test_single_estimate_round_has_only_two_states(self):
        assert ru.classify([None]).state == ru.STATE_UNASSIGNED
        assert ru.classify([vec()]).state == ru.STATE_RESOLVED

    def test_empty_radius_is_a_contract_error(self):
        with pytest.raises(ValueError):
            ru.classify([])


class TestPartitionProperty:
    """Разбиение исчерпывающее и непересекающееся — свойство на ВСЕХ векторах
    из малого домена (урок «сходимость не доказывает разбиения»: состав
    проверяется отдельно, не через один пример на состояние).

    Сверх состояния сдвиг проверяет и остальные поля по всему домену, но не
    все оракулы одной природы. `categories`/`notes` — НЕЗАВИСИМАЯ формулировка
    (множество и его мощность), а не транскрипция `_distinct`: они проверяли
    бы различимость, даже если `_distinct` считала бы иначе. `audit_differs`
    — не такой: `len({(v.assigned_by, v.assigned_at) for v in present}) > 1`
    дословно та же строка, что и в реализации. Это оракул-ДЕТЕКТОР ИЗМЕНЕНИЯ:
    он ловит находку B (`audit_differs`, выведенный из ОТСУТСТВИЯ прочих
    расхождений, а не из аудита) по всему домену, а не в одной точке, но не
    может поймать сам закон, если тот неверен по существу — оракул неверен
    вместе с ним. Ветвь `expected` (state cascade) — тоже транскрипция
    каскада `classify` в том же порядке; она оставлена ради покрытия домена
    сгенерированными векторами, а не ради независимости — с одним
    возвращаемым состоянием «непересекающееся» неопровержимо самим
    построением. Мембершип-проверка состояния переписана так, чтобы её можно
    было провалить (была: `state in (*PENDING, RESOLVED)` — верно для любого
    состояния из четырёх, то есть всегда)."""

    DOMAIN = [None, vec(), vec(category=21), vec(note="з"), vec(by=2), vec(at=T2)]

    @pytest.mark.parametrize("n", [1, 2, 3])
    def test_every_vector_tuple_lands_in_exactly_one_state(self, n):
        for vectors in itertools.product(self.DOMAIN, repeat=n):
            c = ru.classify(list(vectors))
            present = [v for v in vectors if v is not None]
            expected = (
                ru.STATE_UNASSIGNED if not present
                else ru.STATE_PARTIAL if len(present) < n
                else ru.STATE_RESOLVED if len(set(present)) == 1
                else ru.STATE_CONFLICT
            )
            assert c.state == expected, vectors
            assert (c.state in ru.PENDING_STATES) == (c.state != ru.STATE_RESOLVED), vectors
            assert c.assigned == len(present) and c.total == n

            assert set(c.categories) == {v.work_category_id for v in present}, vectors
            assert len(c.categories) == len(set(c.categories)), vectors
            assert set(c.notes) == {v.note for v in present}, vectors
            assert len(c.notes) == len(set(c.notes)), vectors
            assert c.audit_differs == (
                len({(v.assigned_by, v.assigned_at) for v in present}) > 1
            ), vectors


def node(key, parent=None, raw=None, rows=1, own_rows=None, number=None, title="р") -> ru.ChapterNode:
    """`own_rows` по умолчанию равен `rows` — на плоском узле (без детей) это одно
    и то же число, и прежние фикстуры файла остаются валидными. Тесты
    достижимости §2.2 задают оба поля явно: там расхождение и есть предмет."""
    return ru.ChapterNode(key=("lot_1", key), file_parent=None if parent is None else ("lot_1", parent),
                          number=number or key, title=title, smr_article_raw=raw, rows=rows,
                          own_rows=rows if own_rows is None else own_rows)


NODES = [node("1"), node("14"), node("14.1", parent="14"), node("14.3", parent="14"), node("15")]


class TestInputSet:
    def test_chapter_with_file_article_and_no_override_is_excluded(self):
        missing = {n.key: n.key[1] != "1" for n in NODES}          # у «1» статья есть
        vectors = {n.key: [None, None] for n in NODES}
        keys = [n.key[1] for n in ru.input_set(NODES, missing, vectors)]
        assert keys == ["14", "14.1", "14.3", "15"]                 # файловый порядок сохранён

    def test_chapter_with_file_article_but_a_partial_override_is_included(self):
        missing = {n.key: n.key[1] != "1" for n in NODES}
        vectors = {n.key: [None, None] for n in NODES}
        vectors[("lot_1", "1")] = [vec(), None]
        assert ("lot_1", "1") in {n.key for n in ru.input_set(NODES, missing, vectors)}

    def test_empty_statement_is_empty_answer(self):
        assert ru.input_set([], {}, {}) == []

    def test_missing_article_and_full_override_both_present_is_still_included(self):
        """Оба дизъюнкта §2.2 истинны разом — раздел без статьи файла,
        разнесённый вручную во всех сметах (обычное состояние сразу после
        успешного PUT). Отличается от
        `test_chapter_with_file_article_and_no_override_is_excluded` только
        тем, что у «14» (там статьи файла тоже нет) стоит override в обеих
        сметах: убивает `!=` вместо `or` — XOR даёт False, когда оба
        предиката истинны, и раздел ошибочно пропадает из входного множества."""
        missing = {n.key: n.key[1] != "1" for n in NODES}   # у «14» статьи файла нет
        vectors = {n.key: [None, None] for n in NODES}
        vectors[("lot_1", "14")] = [vec(), vec()]            # ...и override есть во всех сметах
        keys = {n.key for n in ru.input_set(NODES, missing, vectors)}
        assert ("lot_1", "14") in keys

    def test_override_at_a_non_first_estimate_still_included(self):
        """Override стоит НЕ в представительной (первой по `estimate_id ASC`)
        смете радиуса — реальный случай: раздел завели по-сметным маршрутом
        паспорта на второй смете. Отличается от
        `test_chapter_with_file_article_but_a_partial_override_is_included`
        только позицией override в векторе (индекс 1 вместо 0): убивает
        реализацию, которая смотрит только на первый слот вектора, а не на
        `any` по всему радиусу."""
        missing = {n.key: n.key[1] != "1" for n in NODES}
        vectors = {n.key: [None, None] for n in NODES}
        vectors[("lot_1", "1")] = [None, vec()]              # override во ВТОРОЙ смете, не в первой
        assert ("lot_1", "1") in {n.key for n in ru.input_set(NODES, missing, vectors)}


class TestReachability:
    """Граница §2.2 «решение до чего-нибудь дойдёт» — правило паспорта
    (`_unallocated_sections`, граница §5.2 спеки разноса), потерянное первой
    редакцией спеки этапного разноса. Замер стенда, ради которого класс
    существует: раздел «10 Инженерные системы» тендера 159-ТУ несёт 161 строку
    файлового поддерева и НОЛЬ достижимых, потому что все его дети имеют свои
    файловые статьи; правило паспорта на той же смете отдавало ноль разделов,
    прежний отбор — три."""

    def test_heading_without_children_and_without_rows_is_not_a_candidate(self):
        """Заглавная строка сметы («Лот №1 - МИRА_Генподряд» на стенде):
        раздел без статьи, без детей и без единой своей строки. Разносить
        нечего, и паспорт его не показывает."""
        nodes = [node("1", rows=0, own_rows=0)]
        assert ru.input_set(nodes, {nodes[0].key: True}, {nodes[0].key: [None]}) == []

    def test_parent_whose_only_child_carries_its_own_article_is_not_a_candidate(self):
        """Форма «10 Инженерные системы» → «10.1» со своей валидной статьёй.
        У родителя своих строк нет, а до строк ребёнка решение не дойдёт
        (правило Ф3), поэтому кандидатом он не является — при том что ПОЛНОЕ
        файловое поддерево у него ненулевое (`rows=5`): подмена достижимых
        строк на `rows` оставила бы узел во множестве."""
        nodes = [node("10", rows=5, own_rows=0), node("10.1", parent="10", rows=5, own_rows=5, raw="11.12")]
        missing = {("lot_1", "10"): True, ("lot_1", "10.1"): False}   # у «10.1» статья есть
        vectors = {n.key: [None] for n in nodes}
        assert ru.input_set(nodes, missing, vectors) == []

    def test_parent_whose_child_inherits_is_a_candidate(self):
        """Негативный к предыдущему — вход отличается РОВНО одним
        ограничением: у «10.1» нет ни статьи, ни своего утверждения, значит он
        наследует, и решение на «10» дойдёт до его пяти строк."""
        nodes = [node("10", rows=5, own_rows=0), node("10.1", parent="10", rows=5, own_rows=5)]
        missing = {n.key: True for n in nodes}
        vectors = {n.key: [None] for n in nodes}
        out = {a.node.key[1]: a for a in ru.aggregate(nodes, missing, vectors)}
        assert set(out) == {"10", "10.1"}
        assert out["10"].reachable_rows == 5 and out["10.1"].reachable_rows == 5

    def test_own_rows_count_even_under_the_nodes_own_raw_statement(self):
        """Решение на узле сильнее его СОБСТВЕННОГО файлового утверждения (то
        же правило, что у паспорта: `_unallocated_fold` берёт `own_rows`
        безусловно). Узел с нечитаемым `smr_article_raw` и своими строками —
        кандидат, хотя предок до него не дойдёт."""
        nodes = [node("14.1", raw="9999", rows=2, own_rows=2)]
        out = ru.aggregate(nodes, {nodes[0].key: True}, {nodes[0].key: [None]})
        assert [a.reachable_rows for a in out] == [2]

    #: Блокирующий узел в смысле паспорта — это НЕ «узел со статьёй»: статьи у
    #: него как раз нет, а есть СВОЁ файловое утверждение, которое статьи не
    #: дало (нечитаемый префикс, код вне справочника). Именно на такой форме
    #: правило Ф3 наблюдаемо: у узла со статьёй его и без Ф3 отсекает первый
    #: конъюнкт `_inherits`. Пара тестов ниже отличается РОВНО наличием
    #: `smr_article_raw` у «10.1» — снятие Ф3 из предиката оставляло первую
    #: редакцию этого класса зелёной.
    BLOCKING_RAW = "9999"

    def _chain(self, *, raw):
        """«10» → «10.1» → «10.1.1»; все девять строк лежат под внуком, у «10»
        и «10.1» своих строк нет. Эффективной статьи нет ни у одного — то есть
        `missing_article` истинен всюду, и отличие только в `raw` у «10.1»."""
        nodes = [
            node("10", rows=9, own_rows=0),
            node("10.1", parent="10", rows=9, own_rows=0, raw=raw),
            node("10.1.1", parent="10.1", rows=9, own_rows=9),
        ]
        missing = {n.key: True for n in nodes}
        return nodes, missing, {n.key: [None] for n in nodes}

    def test_a_blocking_child_cuts_its_whole_subtree_not_only_itself(self):
        """Свёртка обрывается НА блокирующем ребёнке: внук наследовал бы от
        «10.1», но до него решение «10» не дойдёт — цепочка прервана выше.
        Убивает и реализацию без Ф3 вовсе, и ту, что исключает блокирующего
        ребёнка, но продолжает обход в его поддерево."""
        nodes, missing, vectors = self._chain(raw=self.BLOCKING_RAW)
        out = {a.node.key[1]: a.reachable_rows for a in ru.aggregate(nodes, missing, vectors)}
        assert out == {"10.1": 9, "10.1.1": 9}      # «10» отпал: достижимых строк ноль

    def test_the_same_chain_without_the_raw_statement_reaches_through(self):
        """Положительная пара к предыдущему — вход отличается ТОЛЬКО пустым
        `smr_article_raw` у «10.1»: цепочка наследует целиком, и «10»
        достигает всех девяти строк."""
        nodes, missing, vectors = self._chain(raw=None)
        out = {a.node.key[1]: a.reachable_rows for a in ru.aggregate(nodes, missing, vectors)}
        assert out == {"10": 9, "10.1": 9, "10.1.1": 9}

    def test_reach_sums_through_a_chain_of_inheriting_descendants(self):
        """Положительная пара к предыдущему: та же тройка, но «10.1» без своего
        утверждения — достижимость складывается через всю цепочку."""
        nodes = [
            node("10", rows=9, own_rows=1),
            node("10.1", parent="10", rows=8, own_rows=2),
            node("10.1.1", parent="10.1", rows=6, own_rows=6),
        ]
        missing = {n.key: True for n in nodes}
        out = {a.node.key[1]: a.reachable_rows for a in ru.aggregate(nodes, missing, {n.key: [None] for n in nodes})}
        assert out == {"10": 9, "10.1": 8, "10.1.1": 6}

    def test_unreachable_node_with_an_override_stays_in_the_set(self):
        """Второй дизъюнкт §2.2 фильтру достижимости НЕ подчиняется: решение,
        стоящее на узле с нулевой достижимостью, обязано остаться видимым —
        иначе его нельзя снять. Так же устроен паспорт: `_manual_assignments`
        границу §5.2 не применяет. Вход отличается от
        `test_heading_without_children_and_without_rows_is_not_a_candidate`
        РОВНО наличием override."""
        nodes = [node("1", rows=0, own_rows=0)]
        out = ru.aggregate(nodes, {nodes[0].key: True}, {nodes[0].key: [vec()]})
        assert [a.node.key[1] for a in out] == ["1"]
        assert out[0].classification.state == ru.STATE_RESOLVED and out[0].reachable_rows == 0

    def test_reach_is_not_the_full_file_subtree(self):
        """Пришпиливает РАСХОЖДЕНИЕ двух метрик на одном узле: `node.rows`
        (полное файловое поддерево — подпись `manual[]`) и `reachable_rows`
        (охват решения — подпись `sections[]`). Без этого теста реализация,
        вернувшая `node.rows` из обеих, была бы зелёной всюду, где формы
        совпадают."""
        nodes = [node("10", rows=7, own_rows=2), node("10.1", parent="10", rows=5, own_rows=5, raw="11.12")]
        missing = {("lot_1", "10"): True, ("lot_1", "10.1"): False}
        out = ru.aggregate(nodes, missing, {n.key: [None] for n in nodes})
        assert [(a.node.rows, a.reachable_rows) for a in out] == [(7, 2)]


class TestRehang:
    def test_parent_is_the_nearest_file_ancestor_inside_the_set(self):
        """«14.1» классифицирована файлом и не тронута — вне множества; «14.1.1»
        обязана повиснуть на «14», а не стать корнем и не потеряться."""
        deep = [node("14"), node("14.1", parent="14"), node("14.1.1", parent="14.1")]
        missing = {deep[0].key: True, deep[1].key: False, deep[2].key: True}
        out = {a.node.key[1]: a for a in ru.aggregate(deep, missing, {n.key: [None] for n in deep})}
        assert set(out) == {"14", "14.1.1"}
        assert out["14"].parent_key is None
        assert out["14.1.1"].parent_key == ("lot_1", "14") and out["14.1.1"].depth == 1

    def test_parent_is_the_nearest_ancestor_when_the_child_has_no_raw_statement(self):
        """Явный baseline для следующего теста: два узла, оба в множестве, у
        ребёнка нет собственного `smr_article_raw` — родитель поднимается до
        ближайшего предка из множества (тот же предикат на паре узлов, что и
        `test_parent_is_the_nearest_file_ancestor_inside_the_set` на тройке)."""
        nodes = [node("14"), node("14.1", parent="14")]
        missing = {n.key: True for n in nodes}
        out = {a.node.key[1]: a for a in ru.aggregate(nodes, missing, {n.key: [None] for n in nodes})}
        assert out["14.1"].parent_key == ("lot_1", "14") and out["14.1"].depth == 1

    def test_node_with_its_own_raw_statement_is_always_a_root(self):
        """Тот же предикат, что у `_unallocated_sections` паспорта: решение на
        предке до узла со своим утверждением не дойдёт (правило Ф3), и
        вложенность обещала бы неправду. Негативный к
        `test_parent_is_the_nearest_ancestor_when_the_child_has_no_raw_statement`
        (правило AGENTS.md §12 verifying-guards: вход негативного теста
        обязан нарушать РОВНО одно ограничение) — те же два узла, тот же
        `missing_article`, вход отличается ТОЛЬКО непустым `smr_article_raw`
        у ребёнка."""
        nodes = [node("14"), node("14.1", parent="14", raw="9999")]
        missing = {n.key: True for n in nodes}
        out = {a.node.key[1]: a for a in ru.aggregate(nodes, missing, {n.key: [None] for n in nodes})}
        assert out["14.1"].parent_key is None and out["14.1"].depth == 0

    def test_parent_walks_a_gap_of_two_out_of_set_ancestors(self):
        """Цепочка из ЧЕТЫРЁХ узлов, построенная явно (не вариация существующих
        фикстур): "20" в множестве; "20.1" и "20.1.1" классифицированы файлом
        и не тронуты — вне множества, ОБЕ подряд; "20.1.1.1" снова в
        множестве. Промежуток длиной в ДВА узла — обещание докстрока
        `aggregate` («предок ВНЕ множества — законный промежуток цепочки»),
        и такое ordinary в разделах с артикулом на средних уровнях.

        Убивает реализацию, которая делает ОДИН шаг наверх вместо обхода
        (`if p is not None and p not in in_set` вместо `while`): её отказ —
        не косой родитель, а `KeyError` в `_depth`, потому что после одного
        шага код попадает на ключ "20.1" — он вне множества и не имеет записи
        в `parent_of` (там есть записи только для узлов входного множества).
        На реальном GET-маршруте задачи 4 это 500, а не косо нарисованное
        дерево."""
        n20 = node("20")
        n20_1 = node("20.1", parent="20")
        n20_1_1 = node("20.1.1", parent="20.1")
        n20_1_1_1 = node("20.1.1.1", parent="20.1.1")
        nodes = [n20, n20_1, n20_1_1, n20_1_1_1]
        missing = {n20.key: True, n20_1.key: False, n20_1_1.key: False, n20_1_1_1.key: True}
        vectors = {n.key: [None] for n in nodes}
        out = {a.node.key[1]: a for a in ru.aggregate(nodes, missing, vectors)}
        assert set(out) == {"20", "20.1.1.1"}
        assert out["20.1.1.1"].parent_key == ("lot_1", "20") and out["20.1.1.1"].depth == 1


class TestAggregate:
    def test_depth_counts_from_rehung_root_and_order_is_the_input_order(self):
        missing = {n.key: True for n in NODES}
        vectors = {n.key: [None] for n in NODES}
        out = ru.aggregate(NODES, missing, vectors)
        assert [a.node.key[1] for a in out] == ["1", "14", "14.1", "14.3", "15"]
        assert [a.depth for a in out] == [0, 0, 1, 1, 0]

    def test_rows_is_the_node_rows_regardless_of_vectors(self):
        """`rows` — полный размер файлового поддерева: конфликт при нуле
        нераспределённых строк несёт ненулевой `rows` (§4.1)."""
        n14 = node("14", rows=3)
        out = ru.aggregate([n14], {n14.key: False}, {n14.key: [vec(category=10), vec(category=11)]})
        assert out[0].classification.state == ru.STATE_CONFLICT
        assert out[0].node.rows == 3

    def test_depth_of_a_three_level_chain(self):
        """`depth[k] = 0 if p is None else 1` даёт [0, 1, 1] вместо [0, 1, 2]
        на цепочке длиннее одного шага — ни один прежний тест не строил
        цепочку глубже двух уровней."""
        chain = [node("14"), node("14.1", parent="14"), node("14.1.1", parent="14.1")]
        missing = {n.key: True for n in chain}
        vectors = {n.key: [None] for n in chain}
        out = ru.aggregate(chain, missing, vectors)
        assert [a.depth for a in out] == [0, 1, 2]

    def test_vectors_field_carries_the_full_radius_including_a_none_slot(self):
        """Задаче 2 нужен этот срез, чтобы построить блок «Разнесено вручную»
        GET (статья/заметка/автор/момент по каждой смете, §2.3 `manual`) —
        `SectionAggregate.vectors=()` проходил все прежние тесты. `None` в
        СЕРЕДИНЕ радиуса обязан остаться на своём месте, не пропасть и не
        сместить соседей."""
        n14 = node("14")
        v0, v2 = vec(category=10, note="a"), vec(category=10, note="a", by=2)
        out = ru.aggregate([n14], {n14.key: True}, {n14.key: [v0, None, v2]})
        assert out[0].vectors == (v0, None, v2)
