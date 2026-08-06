"""Алгоритм резолва статьи — без БД и без xlsx (спека Ф3 §4.1)."""
from __future__ import annotations

import pytest

from services.category_resolution import (
    CATEGORY_SOURCE_FILE,
    CategoryRef,
    CategoryResolver,
    RowKind,
)
from tests.payloads import position

#: Карта справочника: ровно те коды, что нужны тестам. Названия — как в шаблоне.
CATALOG = {
    "1": CategoryRef(id=101, title="Подготовительные работы"),
    "4": CategoryRef(id=104, title="Возведение конструкций"),
    "4.1": CategoryRef(id=141, title="Ж/Б конструкции"),
    "4.1.2": CategoryRef(id=1412, title="Корпус 1"),
    "4.1.3": CategoryRef(id=1413, title="Корпус 2"),
    "5.1": CategoryRef(id=151, title="Кровля"),
    "5.2": CategoryRef(id=152, title="Фасад"),
    "7.2": CategoryRef(id=172, title="Внутренняя отделка"),
    "7.3": CategoryRef(id=173, title="Инженерные системы"),
    "14.99": CategoryRef(id=1499, title="Прочее"),
}


@pytest.fixture
def resolver() -> CategoryResolver:
    return CategoryResolver(CATALOG)


def rows(*positions):
    """Позиции в форме парсера с каноническими ключами «1..N»."""
    return {str(i): pos for i, pos in enumerate(positions, start=1)}


def chapter(number, *, article=None, title="Раздел", key_number="1"):
    return position(
        job_title=title, number=key_number, chapter_number=number,
        article_smr=article, is_chapter=True,
    )


def work(title="Работа", *, number="1", article=None, **extra):
    return position(job_title=title, number=number, article_smr=article, is_chapter=False, **extra)


class TestStack:
    def test_position_takes_the_article_of_its_chapter(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("1", article="1. Подготовительные работы"), work()))
        assert result.structure_disabled is False
        assert result.rows["2"].parent_position_key == "1"
        assert result.rows["1"].work_category_id == 101
        assert result.rows["1"].category_source == CATEGORY_SOURCE_FILE
        # У позиции полей статьи нет — она их получает через родителя.
        assert result.rows["2"].work_category_id is None
        assert result.rows["2"].category_source is None
        assert result.rows["2"].smr_article_raw is None

    def test_chapter_without_its_own_code_inherits_from_the_stack(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("4", article="4. Возведение конструкций"), chapter("4.3", title="Металлоконструкции"), work())
        )
        assert result.rows["2"].work_category_id == 104
        assert result.rows["2"].smr_article_raw is None
        assert result.rows["2"].category_source == CATEGORY_SOURCE_FILE
        assert result.rows["3"].parent_position_key == "2"
        assert result.counters.chapters_own == 1
        assert result.counters.chapters_inherited == 1

    def test_chapter_of_depth_n_evicts_everything_at_depth_n_or_deeper(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("4", article="4. Возведение конструкций"),
                chapter("4.1", article="4.1. Ж/Б конструкции"),
                chapter("4.1.2", article="4.1.2. Корпус 1"),
                chapter("5.1", article="5.1. Кровля"),
                work(),
            )
        )
        # «5.1» глубины 2 вытесняет 4.1.2 (3) и 4.1 (2), но не 4 (1).
        assert result.rows["4"].parent_position_key == "1"
        assert result.rows["5"].parent_position_key == "4"

    def test_top_level_chapter_has_no_parent(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("1", article="1. Подготовительные работы")))
        assert result.rows["1"].parent_position_key is None

    def test_lot_row_is_a_chapter_without_an_article_and_is_evicted(self, resolver):
        """Дубль «1» во всех четырёх файлах — строка лота (спека §1.1)."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", title="Лот №1 - Тестовый"),
                chapter("1", article="1. Подготовительные работы"),
                work(),
            )
        )
        assert result.rows["1"].work_category_id is None
        assert result.rows["1"].parent_position_key is None
        assert result.rows["2"].parent_position_key is None      # лот вытеснен
        assert result.rows["2"].work_category_id == 101
        assert result.rows["3"].parent_position_key == "2"
        assert result.counters.chapters_unassigned == 1

    def test_duplicate_numbers_with_different_articles_do_not_collide(self, resolver):
        """Три реальных случая 159-ТУ (спека §1.1): номер один, статьи разные."""
        result = resolver.resolve_proposal(
            rows(
                chapter("4.1", article="4.1. Ж/Б конструкции"),
                chapter("4.1.2.2", article="4.1.2. Корпус 1"),
                chapter("4.1.2.2", article="4.1.3. Корпус 2"),
                chapter("5.1", article="5.1. Кровля"),
                chapter("5.1.2", article="5.1. Кровля"),
                chapter("5.1.2", article="5.2. Фасад"),
            )
        )
        assert result.rows["2"].work_category_id == 1412
        assert result.rows["3"].work_category_id == 1413
        assert result.rows["5"].work_category_id == 151
        assert result.rows["6"].work_category_id == 152

    def test_same_numbers_under_different_parents_inherit_differently(self, resolver):
        """«7.3.1»–«7.3.3» без своих кодов стоят под разными родителями."""
        result = resolver.resolve_proposal(
            rows(
                chapter("7.3", article="7.2. Внутренняя отделка"),
                chapter("7.3.1", title="Корпус 1"),
                chapter("7.3", article="7.3. Инженерные системы"),
                chapter("7.3.1", title="Корпус 1"),
            )
        )
        assert result.rows["2"].work_category_id == 172
        assert result.rows["4"].work_category_id == 173

    def test_row_outside_structure_is_not_attached(self, resolver):
        """Агрегатная строка допработ: A и B пусты (спека §2.6)."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                work(),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=False),
            )
        )
        assert result.rows["3"].kind is RowKind.OUTSIDE_STRUCTURE
        assert result.rows["3"].parent_position_key is None
        assert result.rows["3"].work_category_id is None
        assert result.counters.rows_outside_structure == 1
        assert any("вне структуры" in w for w in result.warnings)
        # Строку вне структуры стек не трогает: следующая позиция всё ещё под «1».
        assert result.rows["2"].parent_position_key == "1"


class TestCodeAndTitle:
    def test_trailing_dot_in_the_code_is_stripped(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("14", article="14.99. Прочее")))
        assert result.rows["1"].work_category_id == 1499
        assert result.rows["1"].smr_article_raw == "14.99. Прочее"

    def test_code_without_a_trailing_dot_resolves_too(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("14", article="14.99 Прочее")))
        assert result.rows["1"].work_category_id == 1499

    def test_code_only_value_resolves_and_does_not_warn_about_the_title(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("14", article="14.99.")))
        assert result.rows["1"].work_category_id == 1499
        assert result.warnings == []

    def test_title_mismatch_warns_but_keeps_the_link(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("7", article="7.2. Внутреняя отделка")))
        assert result.rows["1"].work_category_id == 172
        assert len(result.warnings) == 1
        # Оба текста в предупреждении: и файла, и справочника.
        assert "Внутреняя отделка" in result.warnings[0]
        assert "Внутренняя отделка" in result.warnings[0]

    def test_title_comparison_ignores_case_and_whitespace(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("7", article="7.2.   внутренняя\nотделка")))
        assert result.rows["1"].work_category_id == 172
        assert result.warnings == []

    def test_raw_is_stored_trimmed_but_not_collapsed(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("7", article="  7.2.  Внутренняя  отделка  ")))
        assert result.rows["1"].smr_article_raw == "7.2.  Внутренняя  отделка"


class TestFileAssertionBeatsInheritance:
    def test_unknown_code_gives_null_and_does_not_inherit(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("4", article="4. Возведение конструкций"), chapter("4.9", article="99.5. Неизвестная"))
        )
        assert result.rows["2"].work_category_id is None
        assert result.rows["2"].category_source is None
        assert result.rows["2"].smr_article_raw == "99.5. Неизвестная"
        assert any("99.5" in w for w in result.warnings)

    def test_subtree_of_an_unknown_code_stays_unassigned(self, resolver):
        """Ключевое следствие правила: дети НЕ получают статью предка."""
        result = resolver.resolve_proposal(
            rows(
                chapter("4", article="4. Возведение конструкций"),
                chapter("4.9", article="99.5. Неизвестная"),
                chapter("4.9.1", title="Корпус 1"),
                work(),
            )
        )
        assert result.rows["3"].work_category_id is None
        assert result.rows["4"].parent_position_key == "3"
        assert result.counters.positions_unassigned == 1

    def test_unreadable_prefix_gives_null_and_warns(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("4", article="4. Возведение конструкций"), chapter("4.9", article="Прочее по смете"))
        )
        assert result.rows["2"].work_category_id is None
        assert any("Прочее по смете" in w for w in result.warnings)

    def test_a_valid_child_code_restarts_a_resolved_subtree(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("4", article="99.5. Неизвестная"),
                chapter("4.1", article="4.1. Ж/Б конструкции"),
                work(),
            )
        )
        assert result.rows["1"].work_category_id is None
        assert result.rows["2"].work_category_id == 141


class TestUnassignedWarning:
    def test_unassigned_chapters_are_counted_and_shown_with_examples(self, resolver):
        """Спека §2.9 требует не только счётчики, но и примеры."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", title="Лот №1 - Тестовый"),                 # без статьи
                chapter("2", title="Корпус 1"),                          # без статьи
                work(number="1", total_cost_total="500.00"),
            )
        )
        message = next(w for w in result.warnings if "Разделов без статьи" in w)
        assert "Разделов без статьи: 2" in message
        assert "позиций под ними: 1" in message
        assert "Корпус 1" in message                 # пример назван, а не только счётчик
        assert result.counters.chapters_unassigned == 2
        assert result.counters.positions_unassigned == 1


class TestArticleOnNonChapter:
    def test_article_on_a_position_is_not_stored_and_warns(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("1", article="1. Подготовительные работы"), work(article="4.1. Ж/Б конструкции"))
        )
        assert result.rows["2"].smr_article_raw is None
        assert result.rows["2"].work_category_id is None
        assert any("не раздел" in w for w in result.warnings)


class TestKeyOrder:
    def test_shuffled_mapping_gives_the_same_result(self, resolver):
        ordered = rows(chapter("1", article="1. Подготовительные работы"), work(), work(number="2"))
        shuffled = {k: ordered[k] for k in ("3", "1", "2")}
        assert resolver.resolve_proposal(shuffled).rows == resolver.resolve_proposal(ordered).rows
