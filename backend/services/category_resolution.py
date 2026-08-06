"""Резолв статьи СМР по структуре файла сметы (спека Ф3).

Ось паспорта — статья классификатора, но в файле она стоит только на строках-разделах
и не на всех. Принадлежность строки разделу задаёт ТОЛЬКО порядок строк файла:
нумерация «№ раздела» дублируется, и у дублей бывают РАЗНЫЕ статьи, поэтому join по
номеру раздела в денежной логике запрещён (спека §1.1).

Модуль чистый: ни ORM-объектов, ни `Session`, ни записи в БД — единственное место,
которое читает справочник, это `from_db`. Предупреждения и счётчики возвращаются
результатом, а не копятся в состоянии, поэтому один экземпляр безопасно обслуживает
все предложения сметы.
"""
from __future__ import annotations

import enum
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import WorkCategory
from parser.constants import (
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_IS_CHAPTER,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_NUMBER,
)

#: Единственный источник привязки в v1; ck_position_items_category_source это
#: закрепляет. Расширение делает та фича, которая вводит новый источник.
CATEGORY_SOURCE_FILE = "file"

#: Номер раздела и код статьи: цифры через точки, завершающая точка допустима.
_CODE_RE = re.compile(r"^\d+(\.\d+)*\.?$")

#: Сколько примеров показывать в агрегированном предупреждении (как в
#: `_long_title_warning`): смета на 2,5 тыс. строк иначе утопит остальные.
MAX_WARNING_EXAMPLES = 5


class CategoryResolutionContractError(Exception):
    """Вход не является результатом парсера.

    Тихого фолбэка здесь нет намеренно: восстановить порядок строк файла из
    неканоничных ключей нельзя, а догадка дала бы правдоподобную, но чужую
    структуру — тот же класс ошибки, что «вложить раздел в текущую вершину».
    """


class RowKind(str, enum.Enum):
    CHAPTER = "chapter"
    POSITION = "position"
    OUTSIDE_STRUCTURE = "outside_structure"


@dataclass(frozen=True)
class CategoryRef:
    id: int
    title: str


@dataclass(frozen=True)
class RowResolution:
    position_key: str
    kind: RowKind
    parent_position_key: str | None = None
    smr_article_raw: str | None = None
    work_category_id: int | None = None
    category_source: str | None = None


@dataclass(frozen=True)
class ResolutionCounters:
    chapters_own: int = 0
    chapters_inherited: int = 0
    chapters_unassigned: int = 0
    positions_unassigned: int = 0
    rows_outside_structure: int = 0


@dataclass(frozen=True)
class ProposalResolution:
    rows: dict[str, RowResolution]
    warnings: list[str]
    structure_disabled: bool
    counters: ResolutionCounters


@dataclass
class _StackEntry:
    position_key: str
    depth: int
    category: CategoryRef | None
    """Эффективная статья: своя либо унаследованная. Считается в момент помещения в
    стек, поэтому наследование стоит O(1) и не требует проходов вверх."""


def _norm(value: Any) -> str:
    """Предикат пустоты и сравнимая форма: None → '', пробельные последовательности схлопнуты."""
    return "" if value is None else " ".join(str(value).split())


def _trimmed(value: Any) -> str | None:
    """Значение ячейки для хранения: обрезано по краям, пустое → None (как `_text` импорта)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_kind(row: Mapping[str, Any]) -> RowKind:
    """Вид строки. Порядок правил задан спекой §2.3 и ничего не перекрывает молча.

    `CHAPTER` возвращается тогда и только тогда, когда истинен `is_chapter` парсера, —
    то есть гейт полей статьи по `kind` тождествен гейту по колонке БД `is_chapter`,
    на которую ссылается CHECK. Иначе импорт падал бы о констрейнт.
    """
    flag = bool(row.get(JSON_KEY_IS_CHAPTER))
    number_blank = _norm(row.get(JSON_KEY_NUMBER)) == ""
    chapter_blank = _norm(row.get(JSON_KEY_CHAPTER_NUMBER)) == ""
    if number_blank and chapter_blank and not flag:
        return RowKind.OUTSIDE_STRUCTURE
    if flag:
        return RowKind.CHAPTER
    return RowKind.POSITION


def _depth(number: str) -> int:
    """Глубина раздела = число сегментов его номера без завершающей точки."""
    return len(number.rstrip(".").split("."))


@dataclass
class _Warnings:
    """Копилка на один вызов. Тексты собираются в конце, в стабильном порядке."""

    unknown_code: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    title_mismatch: dict[str, tuple[str, str]] = field(default_factory=dict)
    unreadable_prefix: list[str] = field(default_factory=list)
    outside_structure: list[str] = field(default_factory=list)
    article_on_non_chapter: list[str] = field(default_factory=list)
    #: Разделы, оставшиеся без статьи, — по любой причине. Раздел с нечитаемым кодом
    #: попадёт и сюда, и в своё предупреждение: то называет причину, это — следствие,
    #: и счётчик обязан сходиться с тем, что легло в БД.
    unassigned_chapters: list[str] = field(default_factory=list)

    def messages(self, counters: ResolutionCounters) -> list[str]:
        out: list[str] = []
        for code, places in sorted(self.unknown_code.items()):
            out.append(
                f"Код статьи «{code}» не найден в классификаторе (строк: {len(places)}): "
                f"{_examples(places)}. Такие разделы и их позиции без своих кодов остались "
                "без статьи — исправьте код в файле либо добавьте статью в справочник. "
                "Статьи не создаются автоматически."
            )
        for code, (in_file, in_catalog) in sorted(self.title_mismatch.items()):
            out.append(
                f"Название статьи «{code}» в файле («{in_file}») не совпадает со "
                f"справочником («{in_catalog}»). Привязка сделана по коду — он "
                "авторитетен; расхождение стоит проверить глазами."
            )
        if self.unreadable_prefix:
            out.append(
                f"«Статья СМР» заполнена, но код из неё не читается (строк: "
                f"{len(self.unreadable_prefix)}): {_examples(self.unreadable_prefix)}. "
                "Такие разделы остались без статьи: файл называет статью, а какую именно "
                "— определить нельзя, и подставлять статью родителя здесь было бы подменой."
            )
        if self.outside_structure:
            out.append(
                f"Строк вне структуры файла — без номера позиции и без номера раздела: "
                f"{len(self.outside_structure)}: {_examples(self.outside_structure)}. "
                "Они не стоят в структуре файла, поэтому не привязаны ни к одному разделу "
                "и попадут в «Нераспределённое»."
            )
        if self.article_on_non_chapter:
            out.append(
                f"«Статья СМР» заполнена на строке, которая не раздел (строк: "
                f"{len(self.article_on_non_chapter)}): "
                f"{_examples(self.article_on_non_chapter)}. Значение не сохранено в смете: "
                "статья привязывается только через раздел. Полный текст остался в исходных "
                "данных загрузки."
            )
        if counters.chapters_unassigned:
            out.append(
                f"Разделов без статьи: {counters.chapters_unassigned}; позиций под ними: "
                f"{counters.positions_unassigned}: {_examples(self.unassigned_chapters)}. "
                "Их деньги попадут в «Нераспределённое» — это не ошибка импорта, если в "
                "файле статья действительно не проставлена."
            )
        return out


def _examples(places: list[str]) -> str:
    shown = "; ".join(places[:MAX_WARNING_EXAMPLES])
    hidden = len(places) - MAX_WARNING_EXAMPLES
    return f"{shown}{f'; …и ещё {hidden}' if hidden > 0 else ''}"


def _place(key: str, row: Mapping[str, Any], *, raw: str | None = None) -> str:
    """Где искать строку в файле. Номера строки листа импорт не знает (спека §2.9)."""
    number = _norm(row.get(JSON_KEY_CHAPTER_NUMBER)) or _norm(row.get(JSON_KEY_NUMBER)) or "—"
    title = _norm(row.get(JSON_KEY_JOB_TITLE))[:60] or "без названия"
    tail = f", значение «{raw}»" if raw is not None else ""
    return f"позиция {key} (№ раздела «{number}», «{title}»{tail})"


def _ordered_keys(positions: Mapping[str, Any]) -> list[str]:
    """Ключи в порядке файла. Контракт парсера — строго «1..N» без пропусков.

    Проверки «приводится к int» недостаточно: «1» и «01» дают одно число, а порядок
    строк восстанавливается именно из ключей (в `raw_data` это JSONB, где порядок не
    сохраняется).
    """
    expected = [str(i) for i in range(1, len(positions) + 1)]
    if set(positions) != set(expected):
        raise CategoryResolutionContractError(
            "Ключи позиций не образуют последовательность «1..N»: получено "
            f"{sorted(positions)[:10]} (всего {len(positions)}). Порядок строк файла "
            "восстановить нельзя, а он определяет принадлежность строки разделу."
        )
    for key in expected:
        if not isinstance(positions[key], Mapping):
            raise CategoryResolutionContractError(
                f"Позиция «{key}» не является словарём "
                f"({type(positions[key]).__name__}) — это не вывод парсера."
            )
    return expected


class CategoryResolver:
    """Разрешает «Статью СМР» в статью классификатора по структуре файла.

    Карта читается один раз на импорт: справочник маленький (362 строки), а
    обращений — по числу разделов. Форма повторяет `UnitResolver`, но конструктор
    берёт готовую карту, а не `Session`: тогда алгоритм — единственная нетривиальная
    часть фичи — проверяется юнит-тестами без БД.
    """

    def __init__(self, by_code: Mapping[str, CategoryRef]) -> None:
        self._by_code = dict(by_code)

    @classmethod
    def from_db(cls, db: Session) -> CategoryResolver:
        rows = db.execute(
            select(WorkCategory.code, WorkCategory.id, WorkCategory.title)
        ).all()
        return cls({code: CategoryRef(id=cid, title=title) for code, cid, title in rows})

    def resolve_proposal(self, positions: Mapping[str, Any]) -> ProposalResolution:
        keys = _ordered_keys(positions)
        return self._resolve_stack(positions, keys)

    def _resolve_stack(
        self, positions: Mapping[str, Any], keys: list[str]
    ) -> ProposalResolution:
        rows: dict[str, RowResolution] = {}
        warnings = _Warnings()
        stack: list[_StackEntry] = []
        own = inherited = unassigned = positions_unassigned = outside = 0

        for key in keys:
            row = positions[key]
            kind = _row_kind(row)

            if kind is RowKind.OUTSIDE_STRUCTURE:
                rows[key] = RowResolution(position_key=key, kind=kind)
                warnings.outside_structure.append(_place(key, row))
                outside += 1
                continue

            if kind is RowKind.POSITION:
                parent = stack[-1] if stack else None
                rows[key] = RowResolution(
                    position_key=key,
                    kind=kind,
                    parent_position_key=parent.position_key if parent else None,
                )
                if parent is None or parent.category is None:
                    positions_unassigned += 1
                raw = _norm(row.get(JSON_KEY_ARTICLE_SMR))
                if raw:
                    warnings.article_on_non_chapter.append(_place(key, row, raw=raw))
                continue

            number = _norm(row.get(JSON_KEY_CHAPTER_NUMBER))
            depth = _depth(number)
            while stack and stack[-1].depth >= depth:
                stack.pop()
            parent = stack[-1] if stack else None

            raw = _trimmed(row.get(JSON_KEY_ARTICLE_SMR))
            category, outcome = self._article_for(raw, parent, warnings, key, row)
            if outcome == "own":
                own += 1
            elif outcome == "inherited":
                inherited += 1
            else:
                unassigned += 1
                warnings.unassigned_chapters.append(_place(key, row))

            rows[key] = RowResolution(
                position_key=key,
                kind=kind,
                parent_position_key=parent.position_key if parent else None,
                smr_article_raw=raw,
                work_category_id=category.id if category else None,
                category_source=CATEGORY_SOURCE_FILE if category else None,
            )
            stack.append(_StackEntry(position_key=key, depth=depth, category=category))

        counters = ResolutionCounters(
            chapters_own=own,
            chapters_inherited=inherited,
            chapters_unassigned=unassigned,
            positions_unassigned=positions_unassigned,
            rows_outside_structure=outside,
        )
        return ProposalResolution(
            rows=rows,
            warnings=warnings.messages(counters),
            structure_disabled=False,
            counters=counters,
        )

    def _article_for(
        self,
        raw: str | None,
        parent: _StackEntry | None,
        warnings: _Warnings,
        key: str,
        row: Mapping[str, Any],
    ) -> tuple[CategoryRef | None, str]:
        """Эффективная статья раздела и итог для счётчика.

        **Утверждение файла сильнее наследования.** Наследование срабатывает только
        когда файл про статью молчит. Если «Статья СМР» заполнена, но прочитать её
        нельзя, статья предка НЕ подставляется: файл называет здесь другую статью, и
        подстановка отнесла бы деньги туда, куда файл их не относил.
        """
        if raw is None:
            inherited = parent.category if parent else None
            return inherited, "inherited" if inherited else "unassigned"

        collapsed = _norm(raw)
        prefix = collapsed.split(" ")[0]
        if not _CODE_RE.match(prefix):
            warnings.unreadable_prefix.append(_place(key, row, raw=collapsed))
            return None, "unassigned"

        code = prefix.rstrip(".")
        ref = self._by_code.get(code)
        if ref is None:
            warnings.unknown_code[code].append(_place(key, row))
            return None, "unassigned"

        in_file = collapsed[len(prefix):].strip()
        if in_file and in_file.casefold() != _norm(ref.title).casefold():
            warnings.title_mismatch.setdefault(code, (in_file, ref.title))
        return ref, "own"
