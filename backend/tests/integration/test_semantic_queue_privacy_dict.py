"""Сбор словаря приватности из базы и совпадение на реальных данных (задача 4
фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 4.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.3, §1.7.

Чистые функции (`normalize_org_name`, `find_privacy_matches` на вручную
собранном материале) — в `tests/unit/test_semantic_queue_privacy.py`. Здесь —
то, что нельзя проверить без базы: `build_privacy_dictionary` (четыре
источника, слияние дублей, пустые записи, устойчивость `digest`) и сквозной
путь «подрядчик в базе → словарь → совпадение», включая обязательный сценарий
«ВЫБОР» (бриф задачи 4, спека §1.7).
"""
from __future__ import annotations

import pytest

from config import Settings
from services.semantic_privacy import PrivacyMatch, build_privacy_dictionary, find_privacy_matches
from services.semantic_request import ContextRequestMaterial, render_context_request

pytestmark = pytest.mark.integration


def _settings(**overrides) -> Settings:
    base = dict(SECRET_KEY="x" * 32)
    base.update(overrides)
    return Settings(**base)


def _material(**overrides) -> ContextRequestMaterial:
    defaults = dict(
        context_id=1,
        unit_id=None,
        unit_code=None,
        title="Работа по образцу",
        article=None,
        path_counts=(),
        member_count=1,
        archived=False,
        semantic_state="SUGGESTED",
        semantic_kind="WORK",
        work_family_id=None,
        candidates=(),
    )
    defaults.update(overrides)
    return ContextRequestMaterial(**defaults)


class TestBuildPrivacyDictionarySources:
    def test_entries_come_from_all_four_sources_normalized(self, db_session, factories):
        factories.ObjectFactory.create(title="ООО «Алматытауэнерго Маркер»")
        factories.ContractorFactory.create(title="АО «Ромашка Маркер»")
        factories.ContractFactory.create(contract_number="ГП-МАРКЕР-0042")
        factories.TenderFactory.create(tender_number="Т-МАРКЕР-0007")

        dictionary = build_privacy_dictionary(db_session)
        pairs = {(e.kind, e.text) for e in dictionary.entries}

        assert ("object", "алматытауэнерго маркер") in pairs
        assert ("contractor", "ромашка маркер") in pairs
        assert ("contract", "гп-маркер-0042") in pairs
        assert ("tender", "т-маркер-0007") in pairs

    def test_entries_sorted_by_kind_then_text(self, db_session, factories):
        factories.ContractorFactory.create(title="Яблоко Маркер Сорт")
        factories.ContractorFactory.create(title="Арбуз Маркер Сорт")
        factories.ObjectFactory.create(title="Ель Маркер Сорт")

        dictionary = build_privacy_dictionary(db_session)
        pairs = [(e.kind, e.text) for e in dictionary.entries]

        assert pairs == sorted(pairs)

    # `test_number_sources_are_not_run_through_org_name_normalization`
    # (исходный тест этой задачи, вход "ГК-0099-МАРКЕР") снят: после починки
    # `normalize_org_name` не режет форму, приклеенную
    # дефисом, поэтому дефисный номер даёт ОДИНАКОВЫЙ результат что через
    # `_normalize_plain`, что через `normalize_org_name`, — свойство «номер не
    # идёт через нормализацию имени» тест больше не отличал. Заменён ниже
    # `TestBuildPrivacyDictionaryPerSource::test_number_keeps_a_leading_form_word_and_its_quotes`
    # (вход через ПРОБЕЛ и с кавычками — различие держится при любой
    # токенизации формы).

    def test_entry_empty_after_normalization_is_excluded(self, db_session, factories):
        factories.ObjectFactory.create(title="«ООО»")

        dictionary = build_privacy_dictionary(db_session)

        assert "" not in {e.text for e in dictionary.entries}

    def test_duplicate_normalized_text_within_a_kind_is_merged(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Слитно Маркер»")
        factories.ContractorFactory.create(title="АО «Слитно Маркер»")

        dictionary = build_privacy_dictionary(db_session)
        matching = [e for e in dictionary.entries if e.kind == "contractor" and e.text == "слитно маркер"]

        assert len(matching) == 1

    def test_digest_changes_when_a_contractor_is_added(self, db_session, factories):
        factories.ContractorFactory.create(title="Опорный Маркер Подрядчик")
        before = build_privacy_dictionary(db_session).digest

        factories.ContractorFactory.create(title="Добавленный Маркер Подрядчик")
        after = build_privacy_dictionary(db_session).digest

        assert before != after

    def test_digest_reflects_sorted_entries_not_insertion_order(self, db_session, factories):
        """Вставка «сначала Я, потом А» (обратно алфавиту): записи отсортированы,
        и повторная сборка по ТЕМ ЖЕ строкам даёт тот же `digest`. Независимость
        `digest` от физического порядка строк этот тест НЕ доказывает (обе
        сборки видят один порядок) — её стережёт
        `TestPrivacyDigestAndQueries::test_digest_does_not_depend_on_physical_row_order`."""
        factories.ContractorFactory.create(title="Ярило Маркер Сорт")
        factories.ContractorFactory.create(title="Арка Маркер Сорт")

        first = build_privacy_dictionary(db_session)
        second = build_privacy_dictionary(db_session)

        assert first.digest == second.digest
        pairs = [(e.kind, e.text) for e in first.entries]
        assert pairs == sorted(pairs)


class TestFindPrivacyMatchesAgainstRealDictionary:
    def test_vybor_contractor_matches_context_string(self, db_session, factories):
        """Обязательный сценарий брифа задачи 4 (спека §1.7): подрядчик
        `ООО «ВЫБОР»` в базе, строка контекста «выбор по образцу» — ложное, но
        обнаруживаемое совпадение `where = "context"`."""
        factories.ContractorFactory.create(title="ООО «ВЫБОР»")

        dictionary = build_privacy_dictionary(db_session)
        material = _material(title="выбор по образцу")
        rendered = render_context_request(material, settings=_settings())

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="выбор", kind="contractor", where="context"),)

    def test_no_dictionary_entry_gives_no_match(self, db_session, factories):
        factories.ContractorFactory.create(title="Совершенно Другой Маркер")

        dictionary = build_privacy_dictionary(db_session)
        material = _material(title="выбор по образцу")
        rendered = render_context_request(material, settings=_settings())

        assert find_privacy_matches(dictionary, rendered) == ()

    def test_contract_number_with_a_quote_is_found_written_as_in_the_db(self, db_session, factories):
        """Номер договора с кавычкой в базе
        (`ГП «0099»`) обязан находиться, когда та же строка (буквально как в
        базе) встречается в теле запроса — до фикса `_normalize_plain` не
        заменял кавычку пробелом, и запись словаря (`гп «0099»`) не совпадала
        бы ни с чем, потому что искомый текст проходит ту же замену и видит
        `гп 0099`."""
        factories.ContractFactory.create(contract_number="ГП «0099»")

        dictionary = build_privacy_dictionary(db_session)
        material = _material(title="Работа по договору ГП «0099» выполнена")
        rendered = render_context_request(material, settings=_settings())

        matches = find_privacy_matches(dictionary, rendered)

        assert matches == (PrivacyMatch(text="гп 0099", kind="contract", where="context"),)


# ---------------------------------------------------------------------------
#  Каждый источник отдельным входом (номер тендера,
#  пустая запись у подрядчика и номеров, пробелы номера), независимость
#  `digest` от ФИЗИЧЕСКОГО порядка строк, число запросов.
# ---------------------------------------------------------------------------

def _create_number(factories, source: str, number: str) -> None:
    if source == "contract":
        factories.ContractFactory.create(contract_number=number)
    else:
        factories.TenderFactory.create(tender_number=number)


class TestBuildPrivacyDictionaryPerSource:
    @pytest.mark.parametrize("source", ["contract", "tender"])
    def test_number_keeps_a_leading_form_word_and_its_quotes(self, db_session, factories, source):
        """Номер — не имя организации (решение задачи 4): отдельное слово-форма
        в номере остаётся, снимаются только кавычки
        (та же замена кавычка→пробел, что и в `normalize_org_name`), лишние
        пробелы и регистр. Вход через пробел, а не через дефис, — чтобы
        различие не зависело от того, как `normalize_org_name` режет слова."""
        _create_number(factories, source, "ГК «0099» МАРКЕР")

        pairs = {(e.kind, e.text) for e in build_privacy_dictionary(db_session).entries}

        assert (source, "гк 0099 маркер") in pairs

    @pytest.mark.parametrize("source", ["contract", "tender"])
    def test_number_whitespace_is_collapsed_and_trimmed(self, db_session, factories, source):
        _create_number(factories, source, "  ГП   0042\tМАРКЕР  ")

        pairs = {(e.kind, e.text) for e in build_privacy_dictionary(db_session).entries}

        assert (source, "гп 0042 маркер") in pairs

    def test_contractor_empty_after_normalization_is_excluded(self, db_session, factories):
        factories.ContractorFactory.create(title="«ООО»")

        assert "" not in {e.text for e in build_privacy_dictionary(db_session).entries}

    @pytest.mark.parametrize("source", ["contract", "tender"])
    def test_blank_number_is_excluded(self, db_session, factories, source):
        """Табуляции, а не пробелы: `ck_tenders_number_not_blank`
        (`btrim(tender_number) <> ''`) снимает только пробелы, и номер из одних
        табуляций база принимает, а нормализация сворачивает его в пустую
        строку."""
        _create_number(factories, source, "		")

        assert "" not in {e.text for e in build_privacy_dictionary(db_session).entries}


class TestPrivacyDigestAndQueries:
    def test_digest_does_not_depend_on_physical_row_order(self, db_session, factories):
        """Те же подрядчики, вставленные в обратном порядке, — тот же `digest`.
        Второй набор вставлен ПОСЛЕ удаления первого: физический порядок строк
        в таблице (и порядок выдачи запроса без ORDER BY) у двух сборок разный."""
        first = [factories.ContractorFactory.create(title=t) for t in ("Ярило Маркер Физ", "Арка Маркер Физ")]
        digest_first = build_privacy_dictionary(db_session).digest
        for row in first:
            db_session.delete(row)
        db_session.flush()

        for title in ("Арка Маркер Физ", "Ярило Маркер Физ"):
            factories.ContractorFactory.create(title=title)
        digest_second = build_privacy_dictionary(db_session).digest

        assert digest_first == digest_second

    def test_query_count_does_not_grow_with_rows(self, db_session, factories):
        """Четыре запроса по источнику, не запрос на строку (бриф задачи 4)."""
        import sqlalchemy as sa

        def _count() -> int:
            counter = {"n": 0}
            bind = db_session.get_bind()

            def _tick(conn, cursor, statement, parameters, context, executemany):
                counter["n"] += 1

            sa.event.listen(bind, "before_cursor_execute", _tick)
            try:
                build_privacy_dictionary(db_session)
            finally:
                sa.event.remove(bind, "before_cursor_execute", _tick)
            return counter["n"]

        factories.ContractFactory.create()
        factories.TenderFactory.create()
        few = _count()
        for _ in range(4):
            factories.ContractFactory.create()
            factories.TenderFactory.create()
        many = _count()

        assert few == many
        assert many <= 4
