"""Алгоритм резолва статьи — без БД и без xlsx (спека Ф3 §4.1)."""
from __future__ import annotations

import pytest

from services.category_resolution import (
    CATEGORY_SOURCE_FILE,
    CategoryRef,
    CategoryResolutionContractError,
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
        """Деградация здесь ЛОКАЛЬНАЯ, а не D (спека §2.7).

        Без утверждения о `structure_disabled` тест не различал бы два исхода:
        при D остальные три утверждения тоже держатся — поля пусты по всему
        предложению, а предупреждение «не раздел» независимое и переживает D.
        То есть «лишняя ячейка C гасит всю смету» прошло бы незамеченным.
        """
        result = resolver.resolve_proposal(
            rows(chapter("1", article="1. Подготовительные работы"), work(article="4.1. Ж/Б конструкции"))
        )
        assert result.structure_disabled is False
        assert result.rows["2"].parent_position_key == "1"
        assert result.rows["2"].smr_article_raw is None
        assert result.rows["2"].work_category_id is None
        assert any("не раздел" in w for w in result.warnings)


class TestInstanceIsStateless:
    """Спека §2.2: предупреждения и счётчики возвращаются результатом, а не копятся."""

    def test_one_instance_serves_two_proposals_without_leaking(self, resolver):
        """Импорт создаёт РОВНО ОДИН резолвер на всю смету и зовёт его по лоту.

        Копись предупреждения в состоянии объекта — второй лот получил бы чужие,
        и найти это на однолотовой fixture было бы нечем.
        """
        first = resolver.resolve_proposal(rows(chapter("1", title="Лот №1 - Тестовый"), work()))
        second = resolver.resolve_proposal(
            rows(chapter("4", article="4. Возведение конструкций"), work())
        )
        assert len(first.warnings) == 1          # свой раздел без статьи
        assert second.warnings == []             # чужое не протекло
        assert second.counters.chapters_unassigned == 0
        assert second.counters.chapters_own == 1


class TestKeyOrder:
    def test_shuffled_mapping_gives_the_same_result(self, resolver):
        ordered = rows(chapter("1", article="1. Подготовительные работы"), work(), work(number="2"))
        shuffled = {k: ordered[k] for k in ("3", "1", "2")}
        assert resolver.resolve_proposal(shuffled).rows == resolver.resolve_proposal(ordered).rows


class TestStructureDisabled:
    """D: неразбираемая структура гасит привязку по ВСЕМУ предложению (спека §2.4)."""

    def test_unparsable_chapter_number_disables_the_whole_proposal(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                work(),
                chapter("прим.", article="4.1. Ж/Б конструкции", title="Примечание"),
                work(number="2"),
            )
        )
        assert result.structure_disabled is True
        for row in result.rows.values():
            assert row.parent_position_key is None
            assert row.work_category_id is None
            assert row.category_source is None
        # Аудит сохранён: сырое значение на разделах остаётся.
        assert result.rows["1"].smr_article_raw == "1. Подготовительные работы"
        assert result.rows["3"].smr_article_raw == "4.1. Ж/Б конструкции"
        assert result.rows["2"].smr_article_raw is None

    def test_disabled_structure_reports_the_reason_with_raw_values(self, resolver):
        """Проверяется ПРИЧИНА D, а не просто наличие текста.

        Без первых двух утверждений тест был зелёным ещё до валидатора: на том же
        входе выдавалось предупреждение «Разделов без статьи», и «прим.» с
        «Примечание» попадали в него из общего `_place` — то есть подстроки
        находились, а деградации не было вовсе (замерено красным прогоном Task 3
        Step 2, инсайт verifying-guards слой 7).
        """
        result = resolver.resolve_proposal(
            rows(chapter("1", article="1. Подготовительные работы"),
                 chapter("прим.", title="Примечание"))
        )
        assert result.structure_disabled is True
        assert "не определена" in result.warnings[0]
        assert len(result.warnings) == 1
        assert "прим." in result.warnings[0]
        assert "Примечание" in result.warnings[0]

    def test_both_causes_are_named_separately(self, resolver):
        """Спека §2.9: причины D перечисляются по отдельности, со своими счётчиками.

        Без этого деградация на числовом `0` была бы верной, а объяснение — неверным:
        предупреждение говорило бы только про неразбираемый номер.
        """
        result = resolver.resolve_proposal(
            rows(
                chapter("прим.", title="Примечание"),                       # причина 1
                position(job_title="Работа", number="2", chapter_number=0,
                         is_chapter=False),                                  # причина 2
            )
        )
        assert len(result.warnings) == 1
        message = result.warnings[0]
        assert "номер раздела не разбирается (1)" in message
        assert "структурный конфликт (1)" in message
        # A, B и is_chapter — по отдельности у КАЖДОГО примера: причина отказа именно
        # в расхождении между ними, и одного «номера» для разбора не хватает.
        assert 'B=«прим.»' in message and "is_chapter=True" in message
        assert 'A=«2»' in message and 'B=«0»' in message and "is_chapter=False" in message
        assert "Примечание" in message

    def test_resolution_warnings_are_suppressed_when_structure_is_disabled(self, resolver):
        """Они описывали бы резолв, которого не было (спека §2.9)."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="99.5. Неизвестная"),          # неизвестный код
                chapter("2", article="Прочее по смете"),            # нечитаемый префикс
                chapter("3", article="7.2. Внутреняя отделка"),     # расхождение названия
                chapter("прим.", title="Примечание"),               # причина D
            )
        )
        assert result.structure_disabled is True
        assert len(result.warnings) == 1
        assert "99.5" not in result.warnings[0]
        assert "Внутреняя" not in result.warnings[0]
        assert result.counters.chapters_unassigned == 0

    def test_independent_warnings_survive_disabled_structure(self, resolver):
        """Строка вне структуры и статья на не-разделе — не следствия резолва."""
        result = resolver.resolve_proposal(
            rows(
                chapter("прим.", title="Примечание"),
                work(article="4.1. Ж/Б конструкции"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=False),
            )
        )
        assert result.structure_disabled is True
        assert len(result.warnings) == 3
        assert any("прим." in w for w in result.warnings)
        assert any("вне структуры" in w or "без номера" in w for w in result.warnings)
        assert any("не раздел" in w for w in result.warnings)


class TestStructuralConflicts:
    """Расхождение is_chapter с номером — тоже D (спека §2.3)."""

    def test_blank_a_and_b_with_chapter_flag_disables_structure(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=True),
            )
        )
        assert result.structure_disabled is True
        assert "is_chapter" in result.warnings[0]

    def test_numeric_zero_in_the_chapter_number_disables_structure(self, resolver):
        """bool(0) ложно, а _norm(0) = «0» непусто — расхождение предикатов."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Работа", number="2", chapter_number=0, is_chapter=False),
            )
        )
        assert result.structure_disabled is True

    def test_chapter_flag_without_a_number_disables_structure(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Раздел без номера", number="2",
                         chapter_number="   ", is_chapter=True),
            )
        )
        assert result.structure_disabled is True

    def test_real_aggregate_row_is_consistent_and_does_not_disable(self, resolver):
        """A и B пусты, is_chapter=false — так и приходит настоящая агрегатная строка."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=False),
            )
        )
        assert result.structure_disabled is False


class TestKeyContract:
    def test_non_canonical_keys_are_rejected(self, resolver):
        with pytest.raises(CategoryResolutionContractError, match="1..N"):
            resolver.resolve_proposal({"01": work(), "2": work()})

    def test_a_gap_in_the_keys_is_rejected(self, resolver):
        with pytest.raises(CategoryResolutionContractError):
            resolver.resolve_proposal({"1": work(), "3": work()})

    def test_a_non_dict_row_is_rejected(self, resolver):
        with pytest.raises(CategoryResolutionContractError, match="не является словарём"):
            resolver.resolve_proposal({"1": "не словарь"})

    def test_empty_proposal_is_valid(self, resolver):
        result = resolver.resolve_proposal({})
        assert result.rows == {}
        assert result.warnings == []
        assert result.structure_disabled is False
