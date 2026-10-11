"""Синхронность закрытого списка событий журнала между фронтом и бэком.

Спека: `docs/superpowers/specs/2026-09-25-families-screen-design.md` §2.5
(«Журнал»: события словами по закрытому списку событий фичи 1).

`backend/models.py::SEMANTIC_EVENT_TYPES` — единственный источник истины
закрытого списка (докстрока `models.py`: не Python str-Enum намеренно, а
константа для CHECK). `frontend/src/pages/families/labels.ts` держит
`EVENT_LABEL` — Russian-язычную подпись на каждое событие — и обязан
перечислять РОВНО те же 15 кодов, не больше и не меньше: забытый на фронте
код печатал бы его же (см. `eventLabel` — неизвестный код возвращает сам
код), а лишний код на фронте был бы подписью для события, которого CHECK
базы не пропустит никогда.

Тест не поднимает БД и не импортирует TS — читает `labels.ts` ТЕКСТОМ и
извлекает ключи объекта `EVENT_LABEL` регэкспом (тот же приём, что
`test_changes_export_api.py::TestScreensDocRefersToButton` — читает
`screens.md` текстом, не рендерит его). Хрупкость этого приёма к формату
файла — намеренная плата: `labels.ts` документирует эту зависимость в
своей докстроке (стабильный литеральный стиль ключей, по одному на строку).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from models import SEMANTIC_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[3]
LABELS_TS_PATH = REPO_ROOT / "frontend" / "src" / "pages" / "families" / "labels.ts"

# Ключ — голый идентификатор или в кавычках, значение — строка в любых из трёх
# кавычек TS. Раньше регэксп требовал голый ключ и двойную кавычку значения, и
# лишний ключ, записанный `family_split: 'текст'` или `"family_split": "текст"`,
# проходил мимо сверки молча (проверено снятием на ревью задачи 6): extra-ключ
# на фронте — ровно тот дефект, который этот тест обязан ловить.
_KEY_RE = re.compile(
    r"""^\s*(["']?)([A-Za-z_][A-Za-z0-9_]*)\1\s*:\s*["'`]""", re.MULTILINE
)


def _extract_object_literal_block(source: str, marker: str) -> str:
    """Тело объекта `export const <marker>: ... = { ... };` — считая скобки,
    а не первым `}` (значения — строки, скобок внутри них нет, но защититься
    от совпадения с посторонним объектом того же имени дальше по файлу
    дешевле явным подсчётом глубины)."""
    start = source.find(marker)
    if start == -1:
        raise AssertionError(f"{marker!r} не найден в {LABELS_TS_PATH}")

    brace_start = source.index("{", start)
    depth = 0
    end = None
    for i in range(brace_start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        raise AssertionError(f"незакрытая скобка объекта {marker!r} в {LABELS_TS_PATH}")

    return source[brace_start + 1 : end]


def _read_event_label_keys() -> list[str]:
    source = LABELS_TS_PATH.read_text(encoding="utf-8")
    block = _extract_object_literal_block(source, "export const EVENT_LABEL")
    keys = [m.group(2) for m in _KEY_RE.finditer(block)]
    if not keys:
        raise AssertionError(
            "EVENT_LABEL найден, но регэксп не извлёк ни одного ключа — "
            "парсер молчит вместо честного отказа (docs/pitfalls/runs.md: "
            "«счётчик не найден» неотличим от «счётчик нулевой», если это не проверить явно)"
        )
    return keys


class TestEventLabelsSyncWithBackend:
    def test_labels_ts_is_found_and_non_empty(self):
        keys = _read_event_label_keys()
        assert len(keys) > 0

    def test_frontend_event_label_keys_equal_backend_semantic_event_types(self):
        frontend_keys = set(_read_event_label_keys())
        backend_keys = set(SEMANTIC_EVENT_TYPES)

        missing_on_frontend = backend_keys - frontend_keys
        extra_on_frontend = frontend_keys - backend_keys

        assert not missing_on_frontend, (
            f"события бэкенда без подписи на фронте (labels.ts::EVENT_LABEL): "
            f"{sorted(missing_on_frontend)}"
        )
        assert not extra_on_frontend, (
            f"подписи фронта без события в backend/models.py::SEMANTIC_EVENT_TYPES: "
            f"{sorted(extra_on_frontend)}"
        )

    def test_backend_list_has_exactly_22_events(self):
        # Пятнадцать из спеки §2.5, шесть из спеки вариантов §2.13 и `context_reopened`
        # из спеки открытия семей §2.14 — если число
        # сдвинулось, обе стороны обязаны сдвинуться СОГЛАСОВАННО, а не по одной.
        assert len(SEMANTIC_EVENT_TYPES) == 22

    def test_no_duplicate_keys_on_either_side(self):
        frontend_keys = _read_event_label_keys()
        assert len(frontend_keys) == len(set(frontend_keys)), "повтор ключа в EVENT_LABEL"
        assert len(SEMANTIC_EVENT_TYPES) == len(set(SEMANTIC_EVENT_TYPES)), (
            "повтор кода в SEMANTIC_EVENT_TYPES"
        )


@pytest.mark.parametrize(
    "line",
    [
        'family_split: "x",',
        "family_split: 'x',",
        '"family_split": "x",',
        "'family_split': 'x',",
        "family_split: `x`,",
    ],
)
def test_key_regex_reads_every_ts_quoting_style(line):
    """Независимый вход на каждую форму записи ключа/значения, которую TS
    допускает в объектном литерале: пропущенная форма = лишний ключ фронта,
    не видимый сверке."""
    assert [m.group(2) for m in _KEY_RE.finditer("  " + line)] == ["family_split"]


def test_extract_object_literal_block_fails_loudly_on_missing_marker():
    """Сверщик, деливший бы предикат с самим извлечением, не годится (memory:
    verifier-sharing-predicate-with-generator) — здесь независимый вход:
    маркер, которого заведомо нет в файле, обязан падать явно, а не молчать
    нулём ключей."""
    with pytest.raises(AssertionError):
        _extract_object_literal_block("export const OTHER = { a: 1 };", "export const EVENT_LABEL")
