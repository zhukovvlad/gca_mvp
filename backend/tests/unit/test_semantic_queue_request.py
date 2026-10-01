"""Тесты чистых функций `services/semantic_request.py` (задача 2 фичи
«Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 2.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.2, §2.7, §2.10, §2.11.

Здесь — `top_path`, `is_applicable`, `render_context_request`, `family_line`
и настройки очереди в `config.py`: всё, что не ходит в базу. Пакетная
загрузка (`load_request_material`) — интеграционным
`test_semantic_queue_material.py` (нужна БД).
"""
from __future__ import annotations

import hashlib
import itertools
import json
from decimal import Decimal

import pytest

from config import Settings
from services import semantic_request
from services.semantic_request import (
    CACHE_CONTROL_BLOCK_INDEX,
    REASONING_ENABLED,
    CandidateFamily,
    ContextRequestMaterial,
    RequestHasher,
    family_line,
    is_applicable,
    render_context_request,
    top_path,
)

# ---------------------------------------------------------------------------
#  Материал-заготовка — общий баланс для тестов is_applicable/render
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
        path_counts=(("Секция 1 / Этаж 2", 5), ("Секция 2 / Этаж 1", 3)),
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


# ---------------------------------------------------------------------------
#  top_path (спека §2.2, вариант A)
# ---------------------------------------------------------------------------

class TestTopPath:
    def test_picks_highest_count(self):
        assert top_path((("а", 1), ("б", 5), ("в", 2))) == "б"

    def test_ties_broken_lexicographically(self):
        assert top_path((("яблоко", 3), ("апельсин", 3))) == "апельсин"

    def test_independent_of_input_order(self):
        a = (("а", 1), ("б", 5), ("в", 2))
        b = (("в", 2), ("а", 1), ("б", 5))
        assert top_path(a) == top_path(b)

    def test_every_permutation_with_a_tie_gives_the_same_path(self):
        """Порядок входа важен именно при равенстве: без ничьей любой выбор
        максимума независим от порядка. Все перестановки набора, где два пути
        делят наибольшее число, — один и тот же лексикографически меньший
        путь; «последний из равных» или «первый из равных» здесь разойдутся."""
        items = (("Секция 2", 4), ("Секция 1", 4), ("", 1), ("Секция 3", 2))
        assert {top_path(p) for p in itertools.permutations(items)} == {"Секция 1"}

    def test_empty_path_wins_a_tie_as_lexicographically_smallest(self):
        """Пустой путь (членство без раздела) — обычный путь для порядка:
        при равенстве он меньше любого непустого."""
        items = (("Секция 1", 2), ("", 2))
        assert {top_path(p) for p in itertools.permutations(items)} == {""}


# ---------------------------------------------------------------------------
#  is_applicable (спека §2.7) — каждый ложный вход отличается от базового
#  РОВНО одним свойством (docs/insights/claimed-property-needs-its-own-input.md)
# ---------------------------------------------------------------------------

class TestIsApplicable:
    def test_baseline_is_applicable(self):
        assert is_applicable(_material()) is True

    def test_false_when_archived(self):
        assert is_applicable(_material(archived=True)) is False

    def test_false_when_chapter_path_is_broken(self):
        assert is_applicable(_material(path_broken=True, path_counts=())) is False

    def test_false_when_no_members(self):
        assert is_applicable(_material(member_count=0)) is False

    def test_false_when_not_applicable_state(self):
        assert is_applicable(_material(semantic_state="NOT_APPLICABLE")) is False

    def test_false_when_system_kind(self):
        assert is_applicable(_material(semantic_kind="SYSTEM")) is False

    def test_false_when_has_family(self):
        assert is_applicable(_material(work_family_id=42)) is False

    def test_false_when_no_active_candidates(self):
        assert is_applicable(_material(candidates=())) is False

    def test_confirmed_state_is_still_applicable(self):
        """`CONFIRMED` — не `NOT_APPLICABLE`, предикат не запрещает
        переспрос уже решённого контекста (решает `reconcile`, не этот
        предикат)."""
        assert is_applicable(_material(semantic_state="CONFIRMED")) is True


# ---------------------------------------------------------------------------
#  family_line — используется в рендере и задачей 4
# ---------------------------------------------------------------------------

class TestFamilyLine:
    def test_basic_shape(self):
        line = family_line(_candidate(id_=7, title="Устройство пола", unit_code="M2", definition="Пол по грунту"))
        assert line == "7. Устройство пола [M2] — Пол по грунту"

    def test_no_unit_writes_bez_edinitsy(self):
        line = family_line(_candidate(unit_code=None))
        assert "[без единицы]" in line

    def test_collapses_internal_whitespace_and_newlines_to_one_line(self):
        candidate = _candidate(
            title="Устройство\nпола   промышленного", definition="Пол   по\nгрунту  "
        )
        line = family_line(candidate)
        assert "\n" not in line
        assert line == "1. Устройство пола промышленного [M2] — Пол по грунту"


# ---------------------------------------------------------------------------
#  render_context_request — детерминизм, оси хэша, деньги/ПДн отсутствуют
# ---------------------------------------------------------------------------

class TestRenderDeterminism:
    def test_same_material_and_settings_give_same_hashes(self):
        r1 = render_context_request(_material(), settings=_settings())
        r2 = render_context_request(_material(), settings=_settings())
        assert r1.request_hash == r2.request_hash
        assert r1.prefix_hash == r2.prefix_hash
        assert r1.candidates_hash == r2.candidates_hash
        assert r1.input_hash == r2.input_hash

    def test_candidate_order_does_not_change_any_hash(self):
        c1, c2 = _candidate(id_=1), _candidate(id_=2, title="Устройство стен")
        forward = _material(candidates=(c1, c2))
        backward = _material(candidates=(c2, c1))
        rf = render_context_request(forward, settings=_settings())
        rb = render_context_request(backward, settings=_settings())
        assert rf.request_hash == rb.request_hash
        assert rf.prefix_hash == rb.prefix_hash
        assert rf.candidates_hash == rb.candidates_hash


class TestRequestHashAxes:
    """Каждый тест меняет РОВНО одну ось тела относительно базового материала
    и настроек — `request_hash` обязан измениться (спека §2.2)."""

    def _baseline_hash(self, settings=None) -> str:
        return render_context_request(_material(), settings=settings or _settings()).request_hash

    def test_changes_with_path(self):
        base = self._baseline_hash()
        changed = render_context_request(
            _material(path_counts=(("Другой путь", 9),)), settings=_settings()
        ).request_hash
        assert base != changed

    def test_changes_with_article(self):
        base = self._baseline_hash()
        changed = render_context_request(
            _material(article="99.99 Другая статья"), settings=_settings()
        ).request_hash
        assert base != changed

    def test_changes_with_title(self):
        base = self._baseline_hash()
        changed = render_context_request(
            _material(title="Совсем другое наименование"), settings=_settings()
        ).request_hash
        assert base != changed

    def test_changes_with_unit(self):
        base = self._baseline_hash()
        changed = render_context_request(
            _material(unit_code="PCS"), settings=_settings()
        ).request_hash
        assert base != changed

    def test_changes_with_candidates_snapshot(self):
        base = self._baseline_hash()
        changed = render_context_request(
            _material(candidates=(_candidate(id_=1, title="Другая семья"),)), settings=_settings()
        ).request_hash
        assert base != changed

    def test_changes_with_model(self):
        base = self._baseline_hash()
        changed = self._baseline_hash(settings=_settings(SEMANTIC_MODEL="other/model"))
        assert base != changed

    def test_changes_with_max_tokens(self):
        base = self._baseline_hash()
        changed = self._baseline_hash(settings=_settings(SEMANTIC_MAX_TOKENS=999))
        assert base != changed

    def test_changes_with_reasoning_flag(self, monkeypatch):
        base = self._baseline_hash()
        monkeypatch.setattr(semantic_request, "REASONING_ENABLED", not REASONING_ENABLED)
        changed = self._baseline_hash()
        assert base != changed

    def test_changes_with_cache_control_block_index(self, monkeypatch):
        base = self._baseline_hash()
        monkeypatch.setattr(
            semantic_request, "CACHE_CONTROL_BLOCK_INDEX", 1 - CACHE_CONTROL_BLOCK_INDEX
        )
        changed = self._baseline_hash()
        assert base != changed

    def test_changes_with_prompt_text(self, monkeypatch):
        """Ось «PROMPT_VERSION» (спека §2.2): версия сопровождает ТЕКСТ
        промпта, поэтому наблюдаемый вход, меняющий отпечаток, — сам текст
        `SEMANTIC_PROMPT`, а не голое целое `PROMPT_VERSION`, которое нигде
        в тело не попадает."""
        base = self._baseline_hash()
        monkeypatch.setattr(semantic_request, "SEMANTIC_PROMPT", "Другой промпт версии 2")
        changed = self._baseline_hash()
        assert base != changed


class TestPrefixHash:
    def test_independent_of_user_string(self):
        """Два контекста ОДНОЙ единицы с одинаковыми кандидатами, но разным
        путём/статьёй/наименованием — один `prefix_hash`, разные `request_hash`
        и `input_hash` (спека §2.2: префикс общий для всех контекстов единицы)."""
        m1 = _material(title="Работа А", article="1 Статья А", path_counts=(("Путь А", 1),))
        m2 = _material(title="Работа Б", article="2 Статья Б", path_counts=(("Путь Б", 2),))
        r1 = render_context_request(m1, settings=_settings())
        r2 = render_context_request(m2, settings=_settings())
        assert r1.prefix_hash == r2.prefix_hash
        assert r1.request_hash != r2.request_hash
        assert r1.input_hash != r2.input_hash

    def test_changes_with_candidates(self):
        m1 = _material(candidates=(_candidate(id_=1),))
        m2 = _material(candidates=(_candidate(id_=1), _candidate(id_=2, title="Другая семья")))
        r1 = render_context_request(m1, settings=_settings())
        r2 = render_context_request(m2, settings=_settings())
        assert r1.prefix_hash != r2.prefix_hash

    def test_changes_with_model(self):
        r1 = render_context_request(_material(), settings=_settings())
        r2 = render_context_request(_material(), settings=_settings(SEMANTIC_MODEL="other/model"))
        assert r1.prefix_hash != r2.prefix_hash

    def test_changes_with_prompt_text(self, monkeypatch):
        r1 = render_context_request(_material(), settings=_settings())
        monkeypatch.setattr(semantic_request, "SEMANTIC_PROMPT", "Другой промпт")
        r2 = render_context_request(_material(), settings=_settings())
        assert r1.prefix_hash != r2.prefix_hash


class TestPlaceDictionaryVersionExcluded:
    def test_not_in_request_or_prefix_hash_but_in_result(self, monkeypatch):
        base = render_context_request(_material(), settings=_settings())
        monkeypatch.setattr(semantic_request, "PLACE_DICTIONARY_VERSION", base.place_dictionary_version + 1)
        changed = render_context_request(_material(), settings=_settings())
        assert changed.request_hash == base.request_hash
        assert changed.prefix_hash == base.prefix_hash
        assert changed.place_dictionary_version == base.place_dictionary_version + 1


class TestBodyShapeAndPrivacy:
    def test_fixed_params_and_cache_control(self):
        rendered = render_context_request(_material(), settings=_settings())
        body = rendered.body
        assert body["temperature"] == 0
        assert body["reasoning"] == {"enabled": False}
        assert body["max_tokens"] == 600
        assert "response_format" not in body
        system_blocks = body["messages"][0]["content"]
        assert "cache_control" not in system_blocks[0]
        assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}

    def test_response_schema_is_not_sent_to_provider(self):
        """Спека §2.2: JSON-схема ответа провайдеру не отправляется — контракт
        задан текстом промпта, `response_format` в теле нет вовсе (см. также
        `test_fixed_params_and_cache_control`). Реальная проверка «тело не
        содержит цену/объём/количество/подрядчика/договор/объект» — на
        позициях с такими данными, интеграционный
        `test_semantic_queue_material.py::TestPrivacyExclusion` (материал этих
        полей вообще не несёт, здесь их негде было бы подставить)."""
        rendered = render_context_request(_material(), settings=_settings())
        assert "response_format" not in str(rendered.body)


class TestPromptAndSerializationVersionConstants:
    def test_prompt_version_and_serialization_version_are_ints(self):
        assert isinstance(semantic_request.PROMPT_VERSION, int)
        assert isinstance(semantic_request.SERIALIZATION_VERSION, int)

    def test_prompt_text_is_pinned_to_its_version(self):
        """Текст промпта закреплён за `PROMPT_VERSION = 1` отпечатком текста
        `SYSTEM` пилота (`tasks/catalog-pilot-2026-09-18/exp_family_assign.py`,
        значение литерала, посчитанное отдельно от модуля). Правка текста без
        инкремента версии — красный тест; правка с инкрементом обязана дописать
        сюда новую пару, а не заменить старую молча."""
        pinned = {1: "04428e4a605c8a735f282b54d9dbb65076ec195b660bf9a478e04c2402294f39"}
        digest = hashlib.sha256(semantic_request.SEMANTIC_PROMPT.encode("utf-8")).hexdigest()
        assert pinned.get(semantic_request.PROMPT_VERSION) == digest


def _canonical_sha(obj) -> str:
    """Каноническая сериализация брифа задачи 2, записанная в тесте
    независимо от модуля: sha256 hex от `json.dumps(ensure_ascii=False,
    sort_keys=True, separators=(",", ":"))` в UTF-8."""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestCanonicalBodyGolden:
    """Тело и все отпечатки на одном вручную собранном материале — сверка с
    эталоном, записанным литералом по раскладке брифа (а не с тем, что вернул
    модуль в другом вызове). Кандидаты подаются НЕ по `id`, самый частый путь
    стоит НЕ первым, а в имени и определении семьи есть перевод строки и
    хвостовые пробелы."""

    MODEL = "anthropic/claude-sonnet-5"
    FAMILY_BLOCK = (
        "СПИСОК СЕМЕЙ:\n"
        "3. Устройство пола [M2] — Пол по грунту\n"
        "9. Стяжка пола [M2] — Выравнивающий слой"
    )
    USER = (
        "СТРОКА\nРазделы: Секция 1 / Этаж 2\nСтатья: 12.03 Полы\n"
        "Наименование: Устройство стяжки пола\nЕдиница: M2"
    )

    def _render(self):
        material = _material(
            path_counts=(("Секция 2 / Этаж 1", 3), ("Секция 1 / Этаж 2", 5)),
            candidates=(
                _candidate(id_=9, title="Стяжка\n пола", definition="Выравнивающий слой  "),
                _candidate(id_=3, title="Устройство пола", definition="Пол по грунту"),
            ),
        )
        return render_context_request(
            material, settings=_settings(SEMANTIC_MODEL=self.MODEL, SEMANTIC_MAX_TOKENS=600)
        )

    def _expected_body(self) -> dict:
        return {
            "model": self.MODEL,
            "temperature": 0,
            "max_tokens": 600,
            "reasoning": {"enabled": False},
            "usage": {"include": True},
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": semantic_request.SEMANTIC_PROMPT},
                        {
                            "type": "text",
                            "text": self.FAMILY_BLOCK,
                            "cache_control": {"type": "ephemeral"},
                        },
                    ],
                },
                {"role": "user", "content": self.USER},
            ],
        }

    def test_body_equals_brief_layout(self):
        assert self._render().body == self._expected_body()

    def test_each_family_is_exactly_one_line_starting_with_its_id(self):
        block = self._render().body["messages"][0]["content"][1]["text"]
        lines = block.split("\n")
        assert lines[0] == "СПИСОК СЕМЕЙ:"
        assert [line.split(". ", 1)[0] for line in lines[1:]] == ["3", "9"]

    def test_hashes_equal_independent_canonical_sha(self):
        rendered = self._render()
        body = self._expected_body()
        assert rendered.request_hash == _canonical_sha({"serialization_version": 1, "body": body})
        assert rendered.prefix_hash == _canonical_sha(
            {"serialization_version": 1, "model": self.MODEL, "system": body["messages"][0]["content"]}
        )
        assert rendered.candidates_hash == _canonical_sha(
            [
                {"id": 3, "title": "Устройство пола", "unit_code": "M2", "definition": "Пол по грунту"},
                {"id": 9, "title": "Стяжка\n пола", "unit_code": "M2", "definition": "Выравнивающий слой  "},
            ]
        )
        assert rendered.input_hash == _canonical_sha({"serialization_version": 1, "user": self.USER})

    def test_byte_counts_are_utf8_bytes_not_characters(self):
        rendered = self._render()
        prompt = semantic_request.SEMANTIC_PROMPT
        assert rendered.prefix_bytes == len(prompt.encode("utf-8")) + len(self.FAMILY_BLOCK.encode("utf-8"))
        assert rendered.user_bytes == len(self.USER.encode("utf-8"))


class TestUserStringPlaceholders:
    """Заглушки строки контекста (бриф задачи 2): нет пути, нет статьи, нет
    единицы. Два входа пути — два разных механизма: пустой `path_counts`
    (контекст без членств — `top_path` не вызывается) и выигравший путь `""`
    (членства без раздела)."""

    def _user(self, material) -> str:
        return render_context_request(material, settings=_settings()).body["messages"][1]["content"]

    def test_no_path_counts_no_article_no_unit(self):
        material = _material(
            path_counts=(), article=None, unit_code=None, unit_id=None,
            candidates=(_candidate(unit_code=None),),
        )
        assert self._user(material) == (
            "СТРОКА\nРазделы: (разделы не указаны)\nСтатья: (не определена)\n"
            "Наименование: Устройство стяжки пола\nЕдиница: без единицы"
        )

    def test_empty_top_path_is_shown_as_no_sections(self):
        material = _material(path_counts=(("", 4), ("Секция 1", 1)))
        assert "\nРазделы: (разделы не указаны)\n" in self._user(material)


class TestCandidatesHash:
    def test_follows_snapshot_and_ignores_context_row(self):
        base = render_context_request(_material(), settings=_settings())
        other_row = render_context_request(
            _material(title="Другое наименование", article=None, path_counts=(("Путь", 1),)),
            settings=_settings(),
        )
        other_definition = render_context_request(
            _material(candidates=(_candidate(definition="Другое определение"),)),
            settings=_settings(),
        )
        assert other_row.candidates_hash == base.candidates_hash
        assert other_definition.candidates_hash != base.candidates_hash


class TestRequestHasher:
    """Быстрый `request_hash` контекстов единицы обязан совпадать с полным рендером
    побайтно — на входах, где экранирование JSON и состав строки разные."""

    @pytest.mark.parametrize(
        "overrides",
        [
            {},
            {"title": 'Кровля "ТехноНИКОЛЬ" \\ слэш\nперенос строки'},
            {"title": "Emoji \U0001F600 и \u2028 разделитель строки"},
            {"article": None, "path_counts": ()},
            {"unit_code": None, "unit_id": None},
            {"path_counts": (("Раздел \"кавычки\" / Подраздел", 2),)},
        ],
    )
    def test_equals_full_render(self, overrides):
        template = _material(candidates=(_candidate(1), _candidate(2, title="Другая")))
        hasher = RequestHasher(template, settings=_settings())
        material = _material(candidates=template.candidates, **overrides)

        assert hasher.request_hash(material) == (
            render_context_request(material, settings=_settings()).request_hash
        )

    def test_depends_on_the_context_row_not_only_on_the_unit(self):
        template = _material()
        hasher = RequestHasher(template, settings=_settings())

        first = hasher.request_hash(_material(title="Первая"))
        second = hasher.request_hash(_material(title="Вторая"))

        assert first != second

    def test_follows_the_settings_the_hasher_was_built_with(self):
        template = _material()
        material = _material()

        default = RequestHasher(template, settings=_settings()).request_hash(material)
        other = RequestHasher(template, settings=_settings(SEMANTIC_MAX_TOKENS=601)).request_hash(
            material
        )

        assert default != other
        assert other == render_context_request(
            material, settings=_settings(SEMANTIC_MAX_TOKENS=601)
        ).request_hash

    def test_sentinel_text_inside_a_family_does_not_shift_the_split(self):
        """Метка в тексте семьи не совпадает с меткой в теле: там она — целое
        JSON-значение в кавычках, а в блоке семей — часть строки. Разрез идёт по
        месту сообщения, хэш равен полному рендеру."""
        clashing = _candidate(3, definition=f"Текст {semantic_request._USER_TEXT_SENTINEL} внутри")
        template = _material(candidates=(clashing,))
        hasher = RequestHasher(template, settings=_settings())
        material = _material(candidates=(clashing,), title="Любая")

        assert hasher.request_hash(material) == (
            render_context_request(material, settings=_settings()).request_hash
        )


    def test_ordinary_input_is_hashed_without_a_full_render_per_context(self, monkeypatch):
        """Смысл хэшера — не рендерить список семей заново на каждый контекст:
        на обычном входе полный рендер не зовётся ни разу."""
        template = _material(candidates=(_candidate(1), _candidate(2, title="Другая")))
        hasher = RequestHasher(template, settings=_settings())
        renders = []
        real_render = semantic_request.render_context_request

        def _counting_render(material, *, settings):
            renders.append(material.title)
            return real_render(material, settings=settings)

        monkeypatch.setattr(semantic_request, "render_context_request", _counting_render)
        hasher.request_hash(_material(candidates=template.candidates, title="Первая"))
        hasher.request_hash(_material(candidates=template.candidates, title="Вторая"))

        assert renders == []

    def test_template_candidates_in_any_order_give_the_full_render_hash(self):
        """Кандидаты шаблона упорядочиваются по `id`, как в полном рендере."""
        reversed_candidates = (_candidate(2, title="Другая"), _candidate(1))
        hasher = RequestHasher(_material(candidates=reversed_candidates), settings=_settings())
        material = _material(candidates=reversed_candidates, title="Любая")

        assert hasher.request_hash(material) == (
            render_context_request(material, settings=_settings()).request_hash
        )

# ---------------------------------------------------------------------------
#  Settings: тарифы — Decimal из строки .env, без прохода через float
# ---------------------------------------------------------------------------

class TestSemanticSettingsDecimalTariffs:
    def test_defaults_are_decimal(self):
        # OPENROUTER_API_KEY не сверяется с "" здесь: локальный backend/.env
        # несёт настоящий секрет (решение 09.09.2026), и подставлять его в
        # тест нельзя — умолчание поля проверяется независимо от .env.
        settings = _settings(OPENROUTER_API_KEY="")
        assert Decimal("2") == settings.SEMANTIC_PRICE_INPUT_PER_M
        assert Decimal("2.5") == settings.SEMANTIC_PRICE_CACHE_WRITE_PER_M
        assert Decimal("0.2") == settings.SEMANTIC_PRICE_CACHE_READ_PER_M
        assert Decimal("10") == settings.SEMANTIC_PRICE_OUTPUT_PER_M
        assert Decimal("30") == settings.SEMANTIC_DAILY_BUDGET_USD
        assert Decimal("15") == settings.SEMANTIC_EVENT_MAX_RESERVE_USD
        assert settings.RUN_SEMANTIC_WORKER is False
        assert settings.OPENROUTER_API_KEY == ""

    def test_all_queue_defaults_without_env(self, monkeypatch):
        """Умолчания всех настроек очереди — без `backend/.env` и без
        переменных окружения, чтобы локальная конфигурация не подменяла
        проверяемое значение."""
        names = (
            "OPENROUTER_API_KEY", "RUN_SEMANTIC_WORKER", "SEMANTIC_MODEL", "SEMANTIC_MAX_TOKENS",
            "SEMANTIC_CONCURRENCY", "SEMANTIC_CALL_TIMEOUT_S", "SEMANTIC_SHUTDOWN_WAIT_S",
            "SEMANTIC_MAX_ATTEMPTS", "SEMANTIC_PRICE_INPUT_PER_M", "SEMANTIC_PRICE_CACHE_WRITE_PER_M",
            "SEMANTIC_PRICE_CACHE_READ_PER_M", "SEMANTIC_PRICE_OUTPUT_PER_M",
            "SEMANTIC_DAILY_BUDGET_USD", "SEMANTIC_EVENT_MAX_CONTEXTS",
            "SEMANTIC_EVENT_MAX_RESERVE_USD",
        )
        for name in names:
            monkeypatch.delenv(name, raising=False)
        s = Settings(_env_file=None, SECRET_KEY="x" * 32)
        assert (
            s.OPENROUTER_API_KEY, s.RUN_SEMANTIC_WORKER, s.SEMANTIC_MODEL, s.SEMANTIC_MAX_TOKENS,
            s.SEMANTIC_CONCURRENCY, s.SEMANTIC_CALL_TIMEOUT_S, s.SEMANTIC_SHUTDOWN_WAIT_S,
            s.SEMANTIC_MAX_ATTEMPTS, s.SEMANTIC_EVENT_MAX_CONTEXTS,
        ) == ("", False, "anthropic/claude-sonnet-5", 600, 4, 120, 15, 3, 3000)
        tariffs = (
            s.SEMANTIC_PRICE_INPUT_PER_M, s.SEMANTIC_PRICE_CACHE_WRITE_PER_M,
            s.SEMANTIC_PRICE_CACHE_READ_PER_M, s.SEMANTIC_PRICE_OUTPUT_PER_M,
            s.SEMANTIC_DAILY_BUDGET_USD, s.SEMANTIC_EVENT_MAX_RESERVE_USD,
        )
        assert all(type(value) is Decimal for value in tariffs)
        assert tariffs == (
            Decimal("2"), Decimal("2.5"), Decimal("0.2"), Decimal("10"), Decimal("30"), Decimal("15"),
        )

    def test_env_string_becomes_exact_decimal_not_float(self, monkeypatch):
        """`Decimal("0.1") != Decimal(0.1)` — конструктор `Decimal` из
        `float` даёт неточное двоичное приближение
        (`0.1000000000000000055511151231257827021181583404541015625`), а из
        строки — ровно `0.1`. Тест ставит значение переменной окружения
        СТРОКОЙ, как оно приходит из `.env`, и сверяет результат с ОБОИМИ
        независимыми литералами — если бы pydantic проходило через
        `float(str)`, результат совпал бы со вторым, а не с первым."""
        monkeypatch.setenv("SEMANTIC_PRICE_CACHE_READ_PER_M", "0.1")
        settings = _settings()
        assert isinstance(settings.SEMANTIC_PRICE_CACHE_READ_PER_M, Decimal)
        assert Decimal("0.1") == settings.SEMANTIC_PRICE_CACHE_READ_PER_M
        assert Decimal(0.1) != settings.SEMANTIC_PRICE_CACHE_READ_PER_M
