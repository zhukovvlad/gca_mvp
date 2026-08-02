"""Тесты очистки и нормализации текста.

Перенос `app/tests/excel_parser/test_sanitize_text.py` из
`parser_tender_xlsx@0e178c0`.

Тесты исходника, проверявшие фолбэк «spaCy недоступен → отдаём
нелемматизированный текст», переписаны: фолбэка больше нет (см. docstring
`parser/sanitize_text.py`). То, что они проверяли по существу — базовую очистку —
теперь проверяется напрямую на `basic_clean_job_title`, без подмены флагов.
Добавлены тесты детерминизма (AGENTS.md §11) и громкого отказа при
недоступной модели.
"""
from __future__ import annotations

import pytest

from parser import sanitize_text as st
from parser.sanitize_text import (
    NormalizationUnavailableError,
    basic_clean_job_title,
    normalize_job_title_with_lemmatization,
    prepare_for_fts_query,
    sanitize_object_and_address_text,
    sanitize_text,
)


class TestSanitizeText:
    """Тесты функции sanitize_text."""

    def test_sanitize_text_with_string(self):
        """Очистка строки с переводами строк и пробелами."""
        # После замены \n на пробел и удаления \r остаётся двойной пробел
        assert sanitize_text("  Пример\nтекста\r\n с пробелами  ") == "Пример текста  с пробелами"

    def test_sanitize_text_with_quotes(self):
        """Кавычки сохраняются."""
        assert sanitize_text('Текст с "кавычками"') == 'Текст с "кавычками"'

    def test_sanitize_text_with_none(self):
        assert sanitize_text(None) is None

    def test_sanitize_text_with_number(self):
        assert sanitize_text(123) == 123

    def test_sanitize_text_with_empty_string(self):
        assert sanitize_text("   ") == ""

    def test_sanitize_text_with_only_newlines(self):
        assert sanitize_text("\n\r\n") == ""

    def test_sanitize_text_with_mixed_whitespace(self):
        assert sanitize_text("\t  Текст\n\r с табами \t\r\n  ") == "Текст  с табами"


class TestSanitizeObjectAndAddressText:
    """Тесты функции sanitize_object_and_address_text."""

    def test_basic(self):
        assert sanitize_object_and_address_text("Ул. Ленина, д. 5, КОРП. 1А.") == "ул ленина, д 5, корп 1а"

    def test_with_quotes(self):
        assert sanitize_object_and_address_text('  Объект "Капитель" с Большими Буквами.  ') == (
            'объект "капитель" с большими буквами'
        )

    def test_with_none(self):
        assert sanitize_object_and_address_text(None) is None

    def test_with_number(self):
        assert sanitize_object_and_address_text(123) == 123

    def test_multiple_dots(self):
        assert sanitize_object_and_address_text("А.Б.В.Г...Д.") == "абвгд"

    def test_empty_string(self):
        assert sanitize_object_and_address_text("   ") == ""

    def test_with_newlines(self):
        """Функция НЕ трогает \\n и \\r — только точки и регистр."""
        assert sanitize_object_and_address_text("Объект.\nНа двух\rстроках.") == "объект\nна двух\rстроках"


class TestBasicCleanJobTitle:
    """Тесты базовой очистки наименования — слоя до лемматизации.

    Соответствуют тестам исходника, где spaCy подменялась выключенным флагом.
    """

    def test_none(self):
        assert basic_clean_job_title(None) is None

    def test_empty_string(self):
        assert basic_clean_job_title("") is None

    def test_whitespace_only_gives_none(self):
        assert basic_clean_job_title("   \n\t ") is None

    def test_punctuation_only_gives_none(self):
        """Строка из одной пунктуации схлопывается в пустоту, а не в пробелы."""
        assert basic_clean_job_title("!!! ... ???") is None

    def test_basic_cleanup(self):
        assert basic_clean_job_title("**Старший** разработчик! (Python)") == "старший разработчик python"

    def test_with_numbers(self):
        """Точка попадает под [^\\w\\s-] и заменяется пробелом."""
        assert basic_clean_job_title("Менеджер 1С версия 8.3") == "менеджер 1с версия 8 3"

    def test_markdown_cleanup(self):
        assert basic_clean_job_title("# Заголовок\n**жирный текст** и *курсив*") == (
            "заголовок жирный текст и курсив"
        )

    def test_special_characters(self):
        """Дефис внутри слова сохраняется, остальные символы — пробелы."""
        assert basic_clean_job_title("Java-разработчик @company #senior $$$") == "java-разработчик company senior"

    def test_whitespace_normalization(self):
        assert basic_clean_job_title("   Senior    Developer   \n\t  ") == "senior developer"


class TestNormalizeJobTitleWithLemmatization:
    """Тесты нормализации с реальной моделью spaCy."""

    def test_none(self):
        assert normalize_job_title_with_lemmatization(None) is None

    def test_empty_string(self):
        assert normalize_job_title_with_lemmatization("") is None

    def test_punctuation_only(self):
        assert normalize_job_title_with_lemmatization("!!! ... ???") is None

    def test_lemmatizes_russian(self):
        """Разные грамматические формы сводятся к одной нормальной."""
        assert normalize_job_title_with_lemmatization("Устройство монолитных стен") == (
            normalize_job_title_with_lemmatization("устройства монолитных стен")
        )

    def test_lemma_is_normal_form(self):
        result = normalize_job_title_with_lemmatization("Монтаж кабелей силовых")
        assert result == "монтаж кабель силовой"

    def test_case_insensitive(self):
        assert normalize_job_title_with_lemmatization("МОНТАЖ КАБЕЛЕЙ") == (
            normalize_job_title_with_lemmatization("монтаж кабелей")
        )


class TestNormalizationDeterminism:
    """AGENTS.md §11: одна строка → всегда один результат.

    Иначе разъедутся `catalog_positions.normalized_job_title`, `matching_cache`
    и матчер: одна и та же работа окажется двумя разными позициями каталога.
    """

    SAMPLES = [
        "Устройство монолитных железобетонных стен толщиной 200 мм",
        "Разработка рабочей документации",
        "Монтаж кабельных лотков, м2",
        "Обеспечение финансовых условий контракта",
        "Кирпичная кладка наружных стен (с расшивкой швов)",
    ]

    @pytest.mark.parametrize("text", SAMPLES)
    def test_repeated_calls_give_same_result(self, text):
        results = {normalize_job_title_with_lemmatization(text) for _ in range(25)}
        assert len(results) == 1

    @pytest.mark.parametrize("text", SAMPLES)
    def test_result_is_independent_of_lemmatization_cache(self, text):
        """Результат не зависит от того, прогрет ли кэш лемматизации.

        Кэш — чистая оптимизация; если он когда-нибудь начнёт влиять на ответ,
        детерминизм сломается незаметно, поэтому проверяем явно.
        """
        st._lemmatize.cache_clear()
        cold = normalize_job_title_with_lemmatization(text)
        warm = normalize_job_title_with_lemmatization(text)
        st._lemmatize.cache_clear()
        cold_again = normalize_job_title_with_lemmatization(text)

        assert cold == warm == cold_again

    def test_no_leading_or_trailing_whitespace(self):
        for text in self.SAMPLES:
            result = normalize_job_title_with_lemmatization(text)
            assert result == result.strip()
            assert "  " not in result


class TestNormalizationUnavailable:
    """Недоступная модель — громкая ошибка, а не тихий фолбэк."""

    def test_raises_when_model_missing(self, monkeypatch):
        """Если spacy.load падает, нормализация обязана отказать.

        Молчаливый возврат нелемматизированной строки записал бы в каталог
        другую идентичность работы — этого нельзя допускать.
        """
        import spacy

        monkeypatch.setattr(st, "_nlp", None)
        monkeypatch.setattr(spacy, "load", lambda *a, **kw: (_ for _ in ()).throw(OSError("нет модели")))
        st._lemmatize.cache_clear()

        with pytest.raises(NormalizationUnavailableError, match="ru_core_news_sm"):
            normalize_job_title_with_lemmatization("монтаж кабеля")

    def test_none_input_does_not_need_model(self, monkeypatch):
        """На None и пустой строке модель не трогается вовсе."""
        monkeypatch.setattr(st, "_nlp", None)
        monkeypatch.setattr(
            st, "get_nlp", lambda: pytest.fail("модель не должна загружаться для пустого входа")
        )

        assert normalize_job_title_with_lemmatization(None) is None
        assert normalize_job_title_with_lemmatization("   ") is None


class TestPrepareForFtsQuery:
    """Тесты подготовки строки для to_tsquery."""

    def test_joins_lemmas_with_ampersand(self):
        assert prepare_for_fts_query("Монтаж кабелей силовых") == "монтаж & кабель & силовой"

    def test_none_and_empty(self):
        assert prepare_for_fts_query(None) is None
        assert prepare_for_fts_query("") is None
