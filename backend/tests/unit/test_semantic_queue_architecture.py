"""Архитектурный и структурный тесты инварианта сверки очереди семантических
предложений (задача 9 фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 9.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.7, «Проверка инварианта».

AST-анализ полноты не доказывает: он ловит только НОВЫЙ модуль, который пишет
в защищённые данные контекстов, не значась в `RECONCILE_ALLOWLIST`. Защищены:

- создание, удаление и смена `context_id`/`bucket_id` строк `context_members`;
- поля `catalog_contexts.semantic_kind`, `semantic_state`, `work_family_id`,
  `archived_at`;
- удаление `position_items` — прямое и каскадом от родителей (смета, лот,
  предложение, договор, тендер, раунд, участник).

Смена `membership_state` и `conflict_at` защищённой записью НЕ является: вход
запроса она не меняет (спека §2.7, решение спеки 5). Приёмы поиска — эвристики
по именам (получатель присваивания `context_id`/`bucket_id` называется
`member…`; `db.delete(x)` с `member`/`position_item` в имени аргумента) — они
узкие намеренно: лишний срабатывающий модуль требует осознанного решения
о включении в список, а не молчаливого пропуска. Формы записи: присваивание
атрибута, `setattr` с именем поля литералом, конструктор модели,
`insert`/`update`/`delete` Core, `db.query(Model)….update()/.delete()`,
`bulk_*_mappings`, `db.delete(obj)`, сырой SQL. Не видны: `setattr` с именем
поля переменной, SQL, собранный f-строкой, ORM-удаление родителя по имени без
`member`/`position_item` (`db.delete(contract)`).

Сканируются `services`, `crud`, `routers` — не тесты, не миграции, не скрипты.
Структурный тест требует, чтобы у каждого модуля списка был тест точки в
`tests/integration/test_semantic_queue_hooks_*.py`.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from services.semantic_reconcile import RECONCILE_ALLOWLIST

BACKEND = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("services", "crud", "routers")
HOOKS_TESTS_GLOB = "test_semantic_queue_hooks_*.py"

_CONTEXT_FIELDS = frozenset(
    {"semantic_kind", "semantic_state", "work_family_id", "archived_at", "pending_family_id"}
)
_MEMBER_MOVE_FIELDS = frozenset({"context_id", "bucket_id"})
_MEMBER_RECEIVER = re.compile(r"member", re.IGNORECASE)
_SESSION_NAMES = frozenset({"db", "session", "self.db", "self.session"})
_MEMBER_OBJECT_NAME = re.compile(r"member|position_item", re.IGNORECASE)
#: Модели, чьё удаление удаляет `position_items` (сам `PositionItem`, а также
#: каскадом: `ContextMember` следует за позицией, `Proposal` → `Lot` →
#: `Estimate` → `Contract`/`Offer`/`TenderRound` → `Tender`).
_DELETE_REMOVES_POSITIONS = frozenset(
    {
        "PositionItem",
        "ContextMember",
        "Proposal",
        "Lot",
        "Estimate",
        "Contract",
        "Offer",
        "OfferPackage",
        "TenderRound",
        "Tender",
    }
)
_RAW_SQL = re.compile(
    r"\b(delete\s+from|insert\s+into|update)\s+(position_items|context_members)\b"
    r"|\bupdate\s+catalog_contexts\s+set\b[^;]*\b(semantic_kind|semantic_state|work_family_id|archived_at)\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class Finding:
    line: int
    what: str


# ---------------------------------------------------------------------------
#  Сканер
# ---------------------------------------------------------------------------

def _callee_name(func: ast.expr) -> str:
    """Последнее имя вызываемого: `sa.update` → `update`, `Model` → `Model`."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_core_call(func: ast.expr) -> bool:
    """`delete(...)` / `sa.delete(...)` — конструкция SQLAlchemy Core, а не метод
    сессии `db.delete(obj)`."""
    if isinstance(func, ast.Name):
        return True
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in ("sa", "sqlalchemy")
    )


def _model_name(arg: ast.expr | None) -> str:
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute):
        return arg.attr
    return ""


def _values_keys(call: ast.Call, parents: Mapping[ast.AST, ast.AST]) -> tuple[set[str], bool]:
    """Ключи цепочки `.values(...)`, продолжающей вызов `update(Model)`, и
    признак «состав ключей неизвестен» (позиционный аргумент или `**kwargs`)."""
    keys: set[str] = set()
    unknown = False
    current: ast.AST = call
    while True:
        attribute = parents.get(current)
        if not (isinstance(attribute, ast.Attribute) and attribute.value is current):
            break
        chained = parents.get(attribute)
        if not (isinstance(chained, ast.Call) and chained.func is attribute):
            break
        if attribute.attr == "values":
            keys |= {keyword.arg for keyword in chained.keywords if keyword.arg}
            if chained.args or any(keyword.arg is None for keyword in chained.keywords):
                unknown = True
        current = chained
    return keys, unknown


def _query_model(expr: ast.expr) -> str:
    """Модель `db.query(Model)` в начале цепочки `….filter(…).delete()` —
    пустая строка, если цепочка начинается не с `query`."""
    while True:
        if isinstance(expr, ast.Call):
            if _callee_name(expr.func) == "query":
                return _model_name(expr.args[0] if expr.args else None)
            expr = expr.func
        elif isinstance(expr, ast.Attribute):
            expr = expr.value
        else:
            return ""


def _dict_keys(arg: ast.expr | None) -> tuple[set[str], bool]:
    """Ключи словаря `query(...).update({...})` (строки либо `Model.поле`) и
    признак «состав ключей неизвестен»."""
    if not isinstance(arg, ast.Dict):
        return set(), True
    keys: set[str] = set()
    unknown = False
    for key in arg.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        elif isinstance(key, ast.Attribute):
            keys.add(key.attr)
        else:
            unknown = True
    return keys, unknown


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                nodes.add(id(body[0].value))
    return nodes


def find_protected_writes(source: str) -> list[Finding]:
    """Защищённые записи в исходнике модуля (правила — в докстроке модуля тестов)."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    docstrings = _docstring_nodes(tree)
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Attribute):
                    continue
                receiver = ast.unparse(target.value)
                if target.attr in _CONTEXT_FIELDS:
                    findings.append(Finding(node.lineno, f"запись поля контекста {target.attr}"))
                elif target.attr in _MEMBER_MOVE_FIELDS and _MEMBER_RECEIVER.search(receiver):
                    findings.append(Finding(node.lineno, f"смена {target.attr} членства"))
        elif isinstance(node, ast.Call):
            callee = _callee_name(node.func)
            first = node.args[0] if node.args else None
            model = _model_name(first)
            if callee == "ContextMember":
                findings.append(Finding(node.lineno, "создание строки context_members"))
            elif callee == "CatalogContext" and any(
                keyword.arg in _CONTEXT_FIELDS for keyword in node.keywords
            ):
                findings.append(Finding(node.lineno, "создание контекста с защищённым полем"))
            elif callee in ("insert", "pg_insert") and model == "ContextMember":
                findings.append(Finding(node.lineno, "вставка в context_members"))
            elif callee == "delete" and _is_core_call(node.func) and model in _DELETE_REMOVES_POSITIONS:
                findings.append(Finding(node.lineno, f"удаление {model} (удаляет position_items)"))
            elif (
                callee == "delete"
                and isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func.value) in _SESSION_NAMES
                and isinstance(first, ast.Name | ast.Attribute)
                and _MEMBER_OBJECT_NAME.search(ast.unparse(first))
            ):
                findings.append(Finding(node.lineno, "ORM-удаление позиции или членства"))
            elif callee == "update" and model in ("ContextMember", "CatalogContext"):
                keys, unknown = _values_keys(node, parents)
                protected = _MEMBER_MOVE_FIELDS if model == "ContextMember" else _CONTEXT_FIELDS
                if unknown or keys & protected:
                    findings.append(Finding(node.lineno, f"update({model}) защищённых полей"))
            elif (
                callee == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and (
                    node.args[1].value in _CONTEXT_FIELDS
                    or (
                        node.args[1].value in _MEMBER_MOVE_FIELDS
                        and _MEMBER_RECEIVER.search(ast.unparse(node.args[0]))
                    )
                )
            ):
                findings.append(Finding(node.lineno, f"setattr поля {node.args[1].value}"))
            elif callee in ("bulk_update_mappings", "bulk_insert_mappings") and model in (
                "ContextMember",
                "CatalogContext",
            ):
                findings.append(Finding(node.lineno, f"{callee}({model})"))
            elif callee in ("delete", "update") and isinstance(node.func, ast.Attribute):
                queried = _query_model(node.func.value)
                if callee == "delete" and queried in _DELETE_REMOVES_POSITIONS:
                    findings.append(Finding(node.lineno, f"query({queried}).delete()"))
                elif callee == "update" and queried in ("ContextMember", "CatalogContext"):
                    keys, unknown = _dict_keys(first)
                    protected = _MEMBER_MOVE_FIELDS if queried == "ContextMember" else _CONTEXT_FIELDS
                    if unknown or keys & protected:
                        findings.append(Finding(node.lineno, f"query({queried}).update() защищённых полей"))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and _RAW_SQL.search(node.value)
        ):
            findings.append(Finding(node.lineno, "сырой SQL по защищённым таблицам"))

    return sorted(set(findings), key=lambda finding: (finding.line, finding.what))


def module_name(path: Path) -> str:
    relative = path.relative_to(BACKEND).with_suffix("")
    return ".".join(relative.parts)


def scan_tree(root: Path = BACKEND) -> dict[str, list[Finding]]:
    """Модуль → его защищённые записи, только модули с записями."""
    found: dict[str, list[Finding]] = {}
    for directory in SCANNED_DIRS:
        for path in sorted((root / directory).rglob("*.py")):
            findings = find_protected_writes(path.read_text(encoding="utf-8"))
            if findings:
                found[module_name(path)] = findings
    return found


def violations(found: Mapping[str, list[Finding]], allowlist: Collection[str]) -> list[str]:
    """Сообщения о записях вне списка: модуль и строка."""
    return [
        f"{module}:{finding.line} — {finding.what}"
        for module, findings in sorted(found.items())
        if module not in allowlist
        for finding in findings
    ]


def modules_without_hooks_test(allowlist: Collection[str], hooks_texts: Collection[str]) -> list[str]:
    """Модули списка, чьё имя (последний компонент) ни разу не названо в тексте
    тестов точек."""
    missing = []
    for module in sorted(allowlist):
        short = module.rsplit(".", 1)[-1]
        pattern = re.compile(rf"\b{re.escape(short)}\b")
        if not any(pattern.search(text) for text in hooks_texts):
            missing.append(module)
    return missing


def _hooks_texts() -> list[str]:
    files = sorted((BACKEND / "tests" / "integration").glob(HOOKS_TESTS_GLOB))
    return [path.read_text(encoding="utf-8") for path in files]


# ---------------------------------------------------------------------------
#  Дерево проекта
# ---------------------------------------------------------------------------

class TestProtectedWritesLiveOnlyInTheAllowlist:
    def test_no_module_outside_the_allowlist_writes_protected_data(self):
        problems = violations(scan_tree(), RECONCILE_ALLOWLIST)

        assert problems == [], (
            "запись в защищённые данные контекстов вне RECONCILE_ALLOWLIST — вызовите "
            "reconcile_semantic_jobs в этой точке и внесите модуль в список:\n" + "\n".join(problems)
        )

    def test_every_allowlisted_module_really_writes_protected_data(self):
        found = scan_tree()

        stale = sorted(module for module in RECONCILE_ALLOWLIST if module not in found)

        assert stale == [], f"модули списка без защищённых записей (список шире, чем нужно): {stale}"

    def test_the_scan_sees_the_modules_the_spec_names(self):
        """Сканер не пуст: перечень спеки §2.7 целиком найден в дереве."""
        found = scan_tree()

        for module in (
            "services.context_operations",
            "services.work_families",
            "services.review",
            "services.estimate_import",
            "services.round_import",
            "crud.contracts",
            "crud.tenders",
        ):
            assert module in found, module


class TestEveryAllowlistedModuleHasAHooksTest:
    def test_each_module_is_named_in_a_hooks_test_file(self):
        texts = _hooks_texts()
        assert texts, f"не найдено ни одного {HOOKS_TESTS_GLOB}"

        missing = modules_without_hooks_test(RECONCILE_ALLOWLIST, texts)

        assert missing == [], f"у модулей списка нет теста точки в {HOOKS_TESTS_GLOB}: {missing}"


# ---------------------------------------------------------------------------
#  Сам сканер: каждое утверждение — свой вход, рядом отрицательный
# ---------------------------------------------------------------------------

_POSITIVE = {
    "context_field_kind": "ctx.semantic_kind = 'SYSTEM'",
    "context_field_state": "ctx.semantic_state = 'CONFIRMED'",
    "context_field_family": "context.work_family_id = 7",
    "context_field_archived": "context.archived_at = now",
    "context_field_pending_family": "context.pending_family_id = 7",
    "member_context_id": "member.context_id = 5",
    "member_bucket_id": "member_row.bucket_id = 5",
    "member_created": "db.add(ContextMember(position_item_id=1, context_id=2))",
    "member_inserted": "db.execute(pg_insert(ContextMember).values(rows))",
    "member_deleted": "db.execute(sa.delete(ContextMember).where(x))",
    "position_deleted": "db.execute(sa.delete(PositionItem).where(x))",
    "estimate_deleted": "db.execute(delete(Estimate).where(x))",
    "orm_delete_member": "db.delete(member)",
    "orm_delete_position": "db.delete(position_item)",
    "update_member_bucket": "db.execute(sa.update(ContextMember).values(bucket_id=1))",
    "update_member_unknown_values": "db.execute(sa.update(ContextMember).values(**changes))",
    "update_context_kind": "db.execute(update(CatalogContext).where(x).values(semantic_kind='W'))",
    "context_created_with_kind": "CatalogContext(bucket_id=1, semantic_kind='WORK')",
    "raw_delete_positions": "db.execute(text('DELETE FROM position_items WHERE id = 1'))",
    "raw_update_member": "db.execute(text('update context_members set context_id = 2'))",
    "raw_update_context": "db.execute(text('UPDATE catalog_contexts SET archived_at = now()'))",
    "setattr_context_field": "setattr(ctx, 'work_family_id', 7)",
    "setattr_member_context_id": "setattr(member, 'context_id', 5)",
    "bulk_update_members": "db.bulk_update_mappings(ContextMember, rows)",
    "bulk_insert_members": "db.bulk_insert_mappings(ContextMember, rows)",
    "query_delete_positions": "db.query(PositionItem).filter(x).delete()",
    "query_update_context_field": "db.query(CatalogContext).filter(x).update({CatalogContext.archived_at: now})",
    "query_update_member_unknown": "db.query(ContextMember).filter(x).update(changes)",
}

_NEGATIVE = {
    "membership_state": "member.membership_state = 'STALE'",
    "context_field_pending_source": "context.pending_family_source = 'manual'",
    "conflict": "member.conflict_at = None",
    "update_member_state_only": "db.execute(sa.update(ContextMember).values(membership_state='STALE'))",
    "update_context_name_role": "db.execute(update(CatalogContext).values(name_role='WORK'))",
    "other_receiver_context_id": "rule_row.context_id = 3",
    "job_context_id": "job.context_id = 3",
    "other_model_created": "SemanticJob(context_id=1, request_hash='h')",
    "context_created_without_protected": "CatalogContext(bucket_id=1, is_default=True)",
    "bucket_deleted": "db.execute(sa.delete(ContextBucket).where(x))",
    "job_updated": "db.execute(sa.update(SemanticJob).values(status='cancelled'))",
    "orm_delete_other": "db.delete(existing)",
    "route_path_named_like_a_position": "router.delete('/{position_item_id}')",
    "docstring_sql": "def f():\n    '''UPDATE position_items и DELETE FROM position_items — в описании.'''\n",
    "select_from_members": "db.execute(text('SELECT * FROM context_members'))",
    "setattr_by_variable": "setattr(self, key, value)",
    "setattr_other_receiver_context_id": "setattr(rule_row, 'context_id', 3)",
    "bulk_update_jobs": "db.bulk_update_mappings(SemanticJob, rows)",
    "query_delete_other": "db.query(RefreshToken).filter(x).delete()",
    "query_update_context_name_role": "db.query(CatalogContext).filter(x).update({'name_role': 'WORK'})",
}


class TestScanner:
    @pytest.mark.parametrize("name", sorted(_POSITIVE))
    def test_finds_the_write(self, name):
        assert find_protected_writes(_POSITIVE[name]), name

    @pytest.mark.parametrize("name", sorted(_NEGATIVE))
    def test_ignores_the_neighbour_that_is_not_protected(self, name):
        assert find_protected_writes(_NEGATIVE[name]) == [], name


class TestReports:
    def test_violation_message_names_the_module_and_the_line(self):
        found = {"services.rogue": [Finding(12, "запись поля контекста semantic_kind")]}

        messages = violations(found, allowlist={"services.review"})

        assert messages == ["services.rogue:12 — запись поля контекста semantic_kind"]

    def test_allowlisted_module_gives_no_violation(self):
        found = {"services.review": [Finding(3, "смена context_id членства")]}

        assert violations(found, allowlist={"services.review"}) == []

    def test_module_without_a_test_is_reported(self):
        texts = ["from services.review import set_kind", "context_operations.split_context()"]

        missing = modules_without_hooks_test(
            {"services.review", "services.context_operations", "services.matching"}, texts
        )

        assert missing == ["services.matching"]

    def test_module_name_is_matched_as_a_whole_word(self):
        """`review` не засчитывается по `reviewer`."""
        assert modules_without_hooks_test({"services.review"}, ["reviewer_only"]) == ["services.review"]
