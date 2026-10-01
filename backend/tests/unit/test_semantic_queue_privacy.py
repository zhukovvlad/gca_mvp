"""Тесты чистых функций `services/semantic_privacy.py` (задача 4 фичи
«Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 4.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.3, §1.7.

Здесь — `normalize_org_name` и `find_privacy_matches` на вручную собранном
`RenderedRequest` (через `render_context_request`, задача 2) и вручную
собранном `PrivacyDictionary`: всё, что не ходит в базу. Сбор словаря из базы
(`build_privacy_dictionary`) — интеграционным
`test_semantic_queue_privacy_dict.py` (нужна БД).
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from config import Settings
from services import semantic_request
from services.semantic_privacy import (
    PrivacyDictionary,
    PrivacyEntry,
    PrivacyMatch,
    find_privacy_matches,
    normalize_org_name,
)
from services.semantic_request import (
    CandidateFamily,
    ContextRequestMaterial,
    render_context_request,
)

# ---------------------------------------------------------------------------
#  normalize_org_name (спека §2.3) — каждый вход отличается от базового
#  РОВНО одним свойством (docs/insights/claimed-property-needs-its-own-input.md)
# ---------------------------------------------------------------------------

class TestNormalizeOrgName:
    def test_strips_form_and_quotes_together(self):
        assert normalize_org_name("ООО «Каркас Монолит»") == "каркас монолит"

    @pytest.mark.parametrize(
        "form",
        ["ООО", "АО", "ПАО", "ЗАО", "ОАО", "ИП", "ТОО", "LLP", "ГК", "СЗ"],
    )
    def test_strips_each_recognized_form(self, form):
        assert normalize_org_name(f"{form} Ромашка") == "ромашка"

    def test_strips_form_case_insensitively(self):
        assert normalize_org_name("ооо Ромашка") == "ромашка"

    def test_form_nested_in_a_word_is_not_stripped(self):
        """`АОРТА` содержит подстроку `АО`, но целым словом не является —
        форма не снимается (спека §2.3, решение задачи 4)."""
        assert normalize_org_name("АОРТА") == "аорта"

    @pytest.mark.parametrize("quote", list("«»“”„\"'‘’"))
    def test_strips_each_quote_kind(self, quote):
        assert normalize_org_name(f"{quote}Ромашка{quote}") == "ромашка"

    def test_collapses_internal_whitespace(self):
        assert normalize_org_name("ООО   Каркас\nМонолит") == "каркас монолит"

    def test_trims_edges(self):
        assert normalize_org_name("  ООО Ромашка  ") == "ромашка"

    def test_casefold_applied(self):
        assert normalize_org_name("РОМАШКА") == "ромашка"

    def test_empty_after_stripping_form_and_quotes(self):
        """Запись, ставшая пустой после нормализации, — уже свойство самой
        функции, не только словаря: интеграционный тест проверяет, что такая
        запись не попадает в словарь."""
        assert normalize_org_name("«ООО»") == ""

    # -----------------------------------------------------------------------
    #  Форма снимается только ЦЕЛЫМ пробельным токеном —
    #  приклеенная дефисом или цифрой форма НЕ снимается (иначе запись теряет
    #  часть имени и никогда не совпадает с тем, что реально написано).
    # -----------------------------------------------------------------------

    def test_form_glued_by_hyphen_is_a_separate_word_and_stripped(self):
        assert normalize_org_name("ГК-Строй") == "строй"

    def test_form_glued_by_digit_is_kept_whole(self):
        assert normalize_org_name("АО1") == "ао1"

    def test_hyphenated_word_is_split_into_two_words(self):
        """Дефис, как и любой не-словесный символ, разделяет слова."""
        assert normalize_org_name("АО Строй-Инвест") == "строй инвест"

    @pytest.mark.parametrize(
        "title",
        ["Ромашка, ТОО", "(ТОО) Ромашка", "ТОО «Ромашка».", "Ромашка (ООО)"],
    )
    def test_punctuation_glued_to_a_form_does_not_survive(self, title):
        assert normalize_org_name(title) == "ромашка"

    # -----------------------------------------------------------------------
    #  Кавычка/апостроф заменяются ПРОБЕЛОМ, а не удаляются —
    #  форма и слово не склеиваются, даже если в исходном имени между ними нет
    #  пробела.
    # -----------------------------------------------------------------------

    def test_quote_glued_to_form_without_space_still_strips_the_form(self):
        assert normalize_org_name("ООО«Ромашка»") == "ромашка"

    def test_apostrophe_splits_name_into_two_words(self):
        assert normalize_org_name("O'Brien Build") == "o brien build"


# ---------------------------------------------------------------------------
#  find_privacy_matches — на РЕНДЕРЕ реального тела (задача 2)
# ---------------------------------------------------------------------------

def _candidate(id_=1, title="Устройство пола", unit_code="M2", definition="Пол по грунту") -> CandidateFamily:
    return CandidateFamily(id=id_, title=title, unit_code=unit_code, definition=definition)


def _material(**overrides) -> ContextRequestMaterial:
    defaults = dict(
        context_id=1,
        unit_id=10,
        unit_code="M2",
        title="Устройство стяжки пола",
        article="12.03 Полы",
        path_counts=(("Секция 1 / Этаж 2", 5),),
        member_count=8,
        archived=False,
        semantic_state="SUGGESTED",
        semantic_kind="WORK",
        work_family_id=None,
        candidates=(_candidate(),),
    )
    defaults.update(overrides)
    return ContextRequestMaterial(**defaults)


def _settings(**overrides) -> Settings:
    base = dict(SECRET_KEY="x" * 32)
    base.update(overrides)
    return Settings(**base)


def _dictionary(*pairs: tuple[str, str]) -> PrivacyDictionary:
    entries = tuple(PrivacyEntry(kind=k, text=t) for k, t in sorted(pairs))
    return PrivacyDictionary(entries=entries, digest="test-digest")


class TestFindPrivacyMatchesPlaces:
    def test_match_in_context_string_has_where_context(self):
        material = _material(title="Каркас Монолит на этаже")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", "каркас монолит"))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="каркас монолит", kind="contractor", where="context"),)

    def test_match_in_family_line_has_where_family_with_id(self):
        material = _material(
            candidates=(_candidate(id_=7, title="Каркас Монолит", definition="Пол по грунту"),)
        )
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("object", "каркас монолит"))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="каркас монолит", kind="object", where="family:7"),)

    def test_match_in_both_context_and_family_gives_two_matches(self):
        material = _material(
            title="Каркас Монолит на этаже",
            candidates=(_candidate(id_=3, title="Каркас Монолит", definition="Пол по грунту"),),
        )
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("object", "каркас монолит"))

        matches = find_privacy_matches(dictionary, rendered)

        assert set(matches) == {
            PrivacyMatch(text="каркас монолит", kind="object", where="context"),
            PrivacyMatch(text="каркас монолит", kind="object", where="family:3"),
        }
        assert len(matches) == 2

    def test_match_in_prompt_text_has_where_prompt(self, monkeypatch):
        """Слово, попавшее в текст промпта (а не в строку контекста и не в
        строку семьи), приписывается `"prompt"` (решение задачи 4) — здесь оно
        не в определении никакой семьи и не в user-строке."""
        monkeypatch.setattr(
            semantic_request, "SEMANTIC_PROMPT", "Ты работаешь на Ромашка Строй каждый день."
        )
        material = _material()
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", "ромашка строй"))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="ромашка строй", kind="contractor", where="prompt"),)

    def test_match_in_family_block_header_has_where_prompt_not_family(self, monkeypatch):
        """Заголовок блока семей — часть `system`, но не строка КОНКРЕТНОЙ
        семьи: совпадение в нём — `"prompt"` (решение задачи 4), даже если
        рядом есть настоящие семьи."""
        monkeypatch.setattr(
            semantic_request, "FAMILY_BLOCK_HEADER", "СПИСОК СЕМЕЙ РОМАШКА СТРОЙ:\n"
        )
        material = _material(candidates=(_candidate(id_=1, title="Прочая семья"),))
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", "ромашка строй"))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="ромашка строй", kind="contractor", where="prompt"),)

    def test_no_match_without_word_boundary_glued_words(self):
        """`каркасмонолит` слитно — не совпадение (спека §2.3, §1.7:
        производных слов нет)."""
        material = _material(title="Отделка каркасмонолит серии")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("object", "каркас монолит"))

        assert find_privacy_matches(dictionary, rendered) == ()

    def test_no_match_for_a_single_word_of_a_two_word_entry(self):
        """Одно слово `Каркас` без `Монолит» — не совпадение записи из двух
        слов (спека §2.3)."""
        material = _material(title="Каркас в проекте")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("object", "каркас монолит"))

        assert find_privacy_matches(dictionary, rendered) == ()

    def test_quotes_around_the_phrase_do_not_block_the_match(self):
        material = _material(title="Поставка «Каркас Монолит» на объект")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("object", "каркас монолит"))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="каркас монолит", kind="object", where="context"),)

    def test_match_is_case_insensitive(self):
        material = _material(title="поставка КАРКАС МОНОЛИТ на объект")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("object", "каркас монолит"))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="каркас монолит", kind="object", where="context"),)

    def test_repeated_occurrence_in_one_text_yields_one_match_not_two(self):
        """Слово, встретившееся в строке контекста ДВАЖДЫ, — одна запись
        словаря, одно совпадение (без повторов, спека §2.3)."""
        material = _material(title="Ромашка и снова Ромашка")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", "ромашка"))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="ромашка", kind="contractor", where="context"),)

    def test_empty_dictionary_gives_no_matches(self):
        rendered = render_context_request(_material(), settings=_settings())
        dictionary = PrivacyDictionary(entries=(), digest="empty")

        assert find_privacy_matches(dictionary, rendered) == ()

    def test_result_is_deterministic_across_calls(self):
        material = _material(
            title="Ромашка на объекте",
            candidates=(
                _candidate(id_=5, title="Альфа-семья"),
                _candidate(id_=2, title="Ромашка-семья"),
            ),
        )
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", "ромашка"), ("object", "альфа"))

        first = find_privacy_matches(dictionary, rendered)
        second = find_privacy_matches(dictionary, rendered)

        assert first == second

    def test_order_is_context_then_prompt_then_families_by_ascending_id_then_kind_text(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            semantic_request, "SEMANTIC_PROMPT", "Промпт с упоминанием Гамма и Бета."
        )
        material = _material(
            title="Альфа в контексте",
            candidates=(
                _candidate(id_=9, title="Дельта-семья"),
                _candidate(id_=2, title="Гамма-семья"),
            ),
        )
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(
            ("contractor", "альфа"),
            ("object", "бета"),
            ("contractor", "гамма"),
            ("object", "дельта"),
        )

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (
            PrivacyMatch(text="альфа", kind="contractor", where="context"),
            PrivacyMatch(text="гамма", kind="contractor", where="prompt"),
            PrivacyMatch(text="бета", kind="object", where="prompt"),
            PrivacyMatch(text="гамма", kind="contractor", where="family:2"),
            PrivacyMatch(text="дельта", kind="object", where="family:9"),
        )

    def test_family_line_without_leading_id_is_a_value_error_not_an_assertion(self):
        """Строка семьи без `id.` в начале — нарушение контракта рендера; это
        обычное исключение, а не `assert`, который исчез бы под `python -O`."""
        rendered = render_context_request(_material(), settings=_settings())
        broken_body = copy.deepcopy(rendered.body)
        family_block = broken_body["messages"][0]["content"][1]
        family_block["text"] += "\nстрока без номера семьи [M2] — определение"
        broken = dataclasses.replace(rendered, body=broken_body)

        with pytest.raises(ValueError, match="id"):
            find_privacy_matches(_dictionary(("object", "ромашка")), broken)


# ---------------------------------------------------------------------------
#  Запись, построенная из имени с приклеенной формой или
#  апострофом/кавычкой без пробела, обязана находить ТО ЖЕ имя, написанное в
#  тексте буквально как в базе (иначе запись словаря «мертва» —
#  нормализация не фиктивная, а действительно ловит реальное имя).
# ---------------------------------------------------------------------------

class TestFindPrivacyMatchesAfterNormalizationFix:
    def test_name_with_a_hyphen_glued_form_is_found_written_as_in_the_db(self):
        """`normalize_org_name("ГК-Строй") == "строй"` — эта же запись
        обязана совпасть, когда текст содержит подрядчика буквально как в
        базе, а не терять его молча."""
        entry_text = normalize_org_name("ГК-Строй")
        material = _material(title="Работы выполняет ГК-Строй на объекте")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", entry_text))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="строй", kind="contractor", where="context"),)

    def test_name_with_a_digit_glued_form_is_found_written_as_in_the_db(self):
        entry_text = normalize_org_name("АО1")
        material = _material(title="Поставщик АО1 давно работает")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", entry_text))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="ао1", kind="contractor", where="context"),)

    def test_name_with_apostrophe_glued_in_text_still_matches(self):
        """Запись из `O'Brien Build` — два слова (`o brien build`); текст,
        где апостроф стоит БЕЗ пробелов (как обычно и пишут), обязан пройти
        через ту же замену кавычки на пробел, что и сама запись."""
        entry_text = normalize_org_name("O'Brien Build")
        material = _material(title="Подрядчик O'Brien Build работал на объекте")
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", entry_text))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="o brien build", kind="contractor", where="context"),)

    def test_name_with_quote_glued_to_form_in_text_still_matches(self):
        """Запись из `ООО«Ромашка»` (без пробела перед кавычкой) —
        `ромашка`; в тексте то же имя может стоять с кавычками другого вида —
        совпадение держится через ту же замену."""
        entry_text = normalize_org_name("ООО«Ромашка»")
        material = _material(title='Поставка от "Ромашка" на объект')
        rendered = render_context_request(material, settings=_settings())
        dictionary = _dictionary(("contractor", entry_text))

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="ромашка", kind="contractor", where="context"),)


# ---------------------------------------------------------------------------
#  Граница слова для кириллицы с КАЖДОЙ стороны,
#  произвольные пробелы, метасимволы записи, поиск блока семей по заголовку,
#  повторы через несколько текстов промпта, числовой порядок семей.
# ---------------------------------------------------------------------------

class TestFindPrivacyMatchesWordBoundary:
    def test_entry_followed_by_more_letters_is_not_a_match(self):
        """Граница СПРАВА: `выбор` внутри `Выборочный` — производное слово, не
        совпадение (спека §1.7). Буквы кириллические: ASCII-граница слова
        (`re.ASCII`, `[A-Za-z0-9_]`) эту букву за границу бы не приняла."""
        rendered = render_context_request(_material(title="Выборочный ремонт"), settings=_settings())

        assert find_privacy_matches(_dictionary(("contractor", "выбор")), rendered) == ()

    def test_entry_preceded_by_more_letters_is_not_a_match(self):
        """Граница СЛЕВА: `выбор` внутри `Перевыбор`."""
        rendered = render_context_request(_material(title="Перевыбор решения"), settings=_settings())

        assert find_privacy_matches(_dictionary(("contractor", "выбор")), rendered) == ()

    def test_whole_word_between_punctuation_is_a_match(self):
        """Положительная пара к двум тестам выше: то же слово, отделённое
        знаками, — совпадение (иначе они зелены и у поиска, не находящего
        ничего)."""
        rendered = render_context_request(_material(title="Итог: выбор, по образцу"), settings=_settings())

        assert find_privacy_matches(_dictionary(("contractor", "выбор")), rendered) == (
            PrivacyMatch(text="выбор", kind="contractor", where="context"),
        )

    @pytest.mark.parametrize("gap", ["   ", "\t", " \n "])
    def test_arbitrary_whitespace_between_words_in_the_text_matches(self, gap):
        """Строка контекста идёт в `user` без свёртки пробелов (свёртка — только
        у строк семей): запись из двух слов обязана найтись и через серию
        пробелов, табуляцию, перенос."""
        rendered = render_context_request(
            _material(title=f"Поставка Каркас{gap}Монолит на объект"), settings=_settings()
        )

        assert find_privacy_matches(_dictionary(("object", "каркас монолит")), rendered) == (
            PrivacyMatch(text="каркас монолит", kind="object", where="context"),
        )

    def test_entry_with_regex_metacharacters_matches_literally(self):
        """Запись из словаря — текст, а не шаблон: скобки и точка в ней
        совпадают сами с собой."""
        rendered = render_context_request(
            _material(title="Поставка Альфа (Казахстан) и Иванов И.И."), settings=_settings()
        )
        dictionary = _dictionary(("contractor", "альфа (казахстан)"), ("contractor", "иванов и.и."))

        assert find_privacy_matches(dictionary, rendered) == (
            PrivacyMatch(text="альфа (казахстан)", kind="contractor", where="context"),
            PrivacyMatch(text="иванов и.и.", kind="contractor", where="context"),
        )


class TestFindPrivacyMatchesOrgFormInsideName:
    """Запись словаря не хранит организационную форму и знаки, а текст хранит их
    где угодно: форма или знак посередине имени не должны прятать имя от поиска."""

    @pytest.mark.parametrize(
        "title",
        [
            "Поставка Ромашка ООО Сервис на объект",
            "Поставка Ромашка ООО ТОО Сервис на объект",
            "Поставка Ромашка «ООО» Сервис на объект",
            "Поставка Ромашка (ООО) Сервис на объект",
            "Поставка Ромашка ООО, Сервис на объект",
            "Поставка Ромашка-Сервис на объект",
        ],
    )
    def test_form_or_punctuation_between_words_does_not_hide_the_name(self, title):
        rendered = render_context_request(_material(title=title), settings=_settings())

        assert find_privacy_matches(_dictionary(("contractor", "ромашка сервис")), rendered) == (
            PrivacyMatch(text="ромашка сервис", kind="contractor", where="context"),
        )

    @pytest.mark.parametrize(
        "title",
        [
            "Поставка Ромашка Плюс Сервис на объект",
            "Поставка Ромашка ООО1 Сервис на объект",
            "Поставка Ромашкасервис на объект",
        ],
    )
    def test_other_words_or_glued_letters_between_words_are_not_a_match(self, title):
        rendered = render_context_request(_material(title=title), settings=_settings())

        assert find_privacy_matches(_dictionary(("contractor", "ромашка сервис")), rendered) == ()

    def test_number_entries_keep_their_literal_pattern(self):
        """Промежуток с формами и знаками — только для имён: номер, записанный
        через дефис, не совпадает с текстом, где вместо дефиса другой знак."""
        rendered = render_context_request(_material(title="Договор 12/Б по плану"), settings=_settings())

        assert find_privacy_matches(_dictionary(("contract", "12-б")), rendered) == ()

    def test_tender_number_keeps_its_literal_pattern_too(self):
        rendered = render_context_request(_material(title="Тендер 12/Б по плану"), settings=_settings())

        assert find_privacy_matches(_dictionary(("tender", "12-б")), rendered) == ()

    def test_object_name_is_searched_by_the_name_rule(self):
        """Правило имён — для объектов так же, как для подрядчиков."""
        rendered = render_context_request(
            _material(title="Поставка Ромашка (ООО) Сервис на объект"), settings=_settings()
        )

        assert find_privacy_matches(_dictionary(("object", "ромашка сервис")), rendered) == (
            PrivacyMatch(text="ромашка сервис", kind="object", where="context"),
        )


class TestFindPrivacyMatchesBlockLayout:
    def test_family_block_is_found_by_header_not_by_position(self):
        """Блок семей опознаётся заголовком, а не индексом в `system` (бриф
        задачи 4): лишний блок, вставленный ПЕРЕД блоком семей, — текст
        промпта, а строка семьи по-прежнему приписана своей семье."""
        rendered = render_context_request(
            _material(candidates=(_candidate(id_=7, title="Каркас Монолит"),)), settings=_settings()
        )
        rendered.body["messages"][0]["content"].insert(1, {"type": "text", "text": "Вставка Ромашка"})
        dictionary = _dictionary(("object", "каркас монолит"), ("contractor", "ромашка"))

        assert find_privacy_matches(dictionary, rendered) == (
            PrivacyMatch(text="ромашка", kind="contractor", where="prompt"),
            PrivacyMatch(text="каркас монолит", kind="object", where="family:7"),
        )

    def test_patched_header_is_seen_by_both_render_and_check(self, monkeypatch):
        """Проверка читает заголовок из модуля рендера, а не копию, снятую при
        импорте: с подменённым заголовком строка семьи всё равно приписана
        семье, а не промпту."""
        monkeypatch.setattr(semantic_request, "FAMILY_BLOCK_HEADER", "ДРУГОЙ ЗАГОЛОВОК:\n")
        rendered = render_context_request(
            _material(candidates=(_candidate(id_=4, title="Каркас Монолит"),)), settings=_settings()
        )

        assert find_privacy_matches(_dictionary(("object", "каркас монолит")), rendered) == (
            PrivacyMatch(text="каркас монолит", kind="object", where="family:4"),
        )

    def test_word_in_prompt_and_in_header_gives_one_prompt_match(self, monkeypatch):
        """Два текста одного места (`prompt`: промпт и заголовок блока семей) —
        одно совпадение, без повтора."""
        monkeypatch.setattr(semantic_request, "SEMANTIC_PROMPT", "Промпт: Ромашка.")
        monkeypatch.setattr(semantic_request, "FAMILY_BLOCK_HEADER", "СЕМЬИ РОМАШКА:\n")
        rendered = render_context_request(_material(), settings=_settings())

        assert find_privacy_matches(_dictionary(("contractor", "ромашка")), rendered) == (
            PrivacyMatch(text="ромашка", kind="contractor", where="prompt"),
        )

    def test_families_are_ordered_by_numeric_id_not_by_string(self):
        """`family:9` раньше `family:10`: порядок числовой, строковый поставил
        бы `10` первым."""
        rendered = render_context_request(
            _material(
                candidates=(
                    _candidate(id_=10, title="Ромашка десять"),
                    _candidate(id_=9, title="Ромашка девять"),
                )
            ),
            settings=_settings(),
        )

        assert find_privacy_matches(_dictionary(("contractor", "ромашка")), rendered) == (
            PrivacyMatch(text="ромашка", kind="contractor", where="family:9"),
            PrivacyMatch(text="ромашка", kind="contractor", where="family:10"),
        )

    def test_families_are_ordered_by_id_even_if_the_block_lists_them_out_of_order(self):
        """Порядок результата — собственное обещание проверки, а не отражение
        порядка строк в теле: строки блока, переставленные вручную, дают тот
        же порядок по возрастанию id."""
        rendered = render_context_request(
            _material(candidates=(_candidate(id_=2, title="Ромашка два"), _candidate(id_=5, title="Ромашка пять"))),
            settings=_settings(),
        )
        block = rendered.body["messages"][0]["content"][1]
        header = semantic_request.FAMILY_BLOCK_HEADER
        lines = block["text"][len(header):].split("\n")
        block["text"] = header + "\n".join(reversed(lines))

        assert find_privacy_matches(_dictionary(("contractor", "ромашка")), rendered) == (
            PrivacyMatch(text="ромашка", kind="contractor", where="family:2"),
            PrivacyMatch(text="ромашка", kind="contractor", where="family:5"),
        )
