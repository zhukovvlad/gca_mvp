"""Архитектурный и структурный тесты инварианта сверки очереди семантических
предложений (задача 9 фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 9.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.7, «Проверка инварианта».

AST-анализ полноты не доказывает: он ловит только НОВЫЙ модуль, который пишет
в защищённые данные контекстов, не значась в `RECONCILE_ALLOWLIST`. Защищены:

- создание, удаление и смена `context_id`/`bucket_id` строк `context_members`;
- поля `catalog_contexts.semantic_kind`, `semantic_state`, `work_family_id`,
  `archived_at`, `pending_family_id`, `work_variant_id`;
- удаление `position_items` — прямое и каскадом от родителей (смета, лот,
  предложение, договор, тендер, раунд, участник);
- строки `work_variants`, `work_variant_values`, `context_parameter_values`,
  `family_parameter_schemas`, `family_parameters`, `family_parameter_values`
  (создание, изменение, удаление; спека вариантов §2.7);
- `catalog_positions.kind` — присваивание и `update`; создание строки каталога
  (`pg_insert(CatalogPosition)` при сопоставлении) смены вида не делает: у новой
  строки ещё нет контекстов.

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

Приёмы для таблиц вариантов и `kind` строки каталога так же узкие: модели
из `_VARIANT_MODELS` — конструктор, `insert`/`pg_insert`/`update`/`delete` Core,
`bulk_*_mappings`, `db.query(Model)….update()/.delete()`, сырой SQL;
присваивание атрибута — когда получатель называется `variant`/`schema`
(`target_variant`, `schema`), а для `kind` — `catalog`/`row`; `db.delete(obj)` —
когда имя аргумента содержит `variant`/`schema`. Не видны: `setattr` вариантов,
присваивание через переменную с другим именем.

Сама сверка (`services.semantic_reconcile`) в `RECONCILE_ALLOWLIST` не входит, но
вправе создавать версию схемы `building` вместе с её заданием — это единственная
запись, которую тест ей разрешает.

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
    {
        "semantic_kind",
        "semantic_state",
        "work_family_id",
        "archived_at",
        "pending_family_id",
        "work_variant_id",
    }
)
#: Таблицы вариантов и схем: любая запись в них меняет вход или предмет заданий.
_VARIANT_MODELS = frozenset(
    {
        "WorkVariant",
        "WorkVariantValue",
        "ContextParameterValue",
        "FamilyParameterSchema",
        "FamilyParameter",
        "FamilyParameterValue",
    }
)
_VARIANT_TABLE_PATTERN = re.compile(r"^(work_variants?|work_variant_values|context_parameter_values|family_parameter\w*)$")
_VARIANT_RECEIVER = re.compile(r"variant|schema", re.IGNORECASE)
_CATALOG_KIND_RECEIVER = re.compile(r"catalog|row", re.IGNORECASE)
#: Модуль самой сверки: единственная запись — версия схемы `building` с заданием.
RECONCILER_MODULE = "services.semantic_reconcile"
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
    r"|\bupdate\s+catalog_contexts\s+set\b[^;]*\b"
    r"(semantic_kind|semantic_state|work_family_id|archived_at|pending_family_id|work_variant_id)\b"
    r"|\b(delete\s+from|insert\s+into|update)\s+"
    r"(work_variants|work_variant_values|context_parameter_values|family_parameter\w*)\b"
    r"|\bupdate\s+catalog_positions\s+set\b[^;]*\bkind\b",
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
                elif target.attr == "kind" and _CATALOG_KIND_RECEIVER.search(receiver):
                    findings.append(Finding(node.lineno, "запись catalog_positions.kind"))
                elif _VARIANT_RECEIVER.search(receiver):
                    findings.append(
                        Finding(node.lineno, f"запись {target.attr} варианта или версии схемы")
                    )
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
            elif callee in _VARIANT_MODELS:
                findings.append(Finding(node.lineno, f"создание строки {callee}"))
            elif callee in ("insert", "pg_insert") and model in _VARIANT_MODELS:
                findings.append(Finding(node.lineno, f"вставка в {model}"))
            elif (
                callee in ("update", "delete")
                and _is_core_call(node.func)
                and model in _VARIANT_MODELS
            ):
                findings.append(Finding(node.lineno, f"{callee}({model})"))
            elif callee == "update" and _is_core_call(node.func) and model == "CatalogPosition":
                keys, unknown = _values_keys(node, parents)
                if unknown or "kind" in keys:
                    findings.append(Finding(node.lineno, "update(CatalogPosition) поля kind"))
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
            elif (
                callee == "delete"
                and isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func.value) in _SESSION_NAMES
                and isinstance(first, ast.Name | ast.Attribute)
                and _VARIANT_RECEIVER.search(ast.unparse(first))
            ):
                findings.append(Finding(node.lineno, "ORM-удаление варианта или версии схемы"))
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
                    or (
                        node.args[1].value == "kind"
                        and _CATALOG_KIND_RECEIVER.search(ast.unparse(node.args[0]))
                    )
                )
            ):
                findings.append(Finding(node.lineno, f"setattr поля {node.args[1].value}"))
            elif callee in ("bulk_update_mappings", "bulk_insert_mappings") and (
                model in ("ContextMember", "CatalogContext") or model in _VARIANT_MODELS
            ):
                findings.append(Finding(node.lineno, f"{callee}({model})"))
            elif callee in ("delete", "update") and isinstance(node.func, ast.Attribute):
                queried = _query_model(node.func.value)
                if callee == "delete" and queried in _DELETE_REMOVES_POSITIONS:
                    findings.append(Finding(node.lineno, f"query({queried}).delete()"))
                elif queried in _VARIANT_MODELS:
                    findings.append(Finding(node.lineno, f"query({queried}).{callee}()"))
                elif callee == "update" and queried == "CatalogPosition":
                    keys, unknown = _dict_keys(first)
                    if unknown or "kind" in keys:
                        findings.append(Finding(node.lineno, "query(CatalogPosition).update() поля kind"))
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
        problems = violations(scan_tree(), RECONCILE_ALLOWLIST | {RECONCILER_MODULE})

        assert problems == [], (
            "запись в защищённые данные контекстов вне RECONCILE_ALLOWLIST — вызовите "
            "reconcile_semantic_jobs в этой точке и внесите модуль в список:\n" + "\n".join(problems)
        )

    def test_the_reconciler_writes_nothing_but_the_building_version(self):
        findings = scan_tree().get(RECONCILER_MODULE, [])

        assert findings, "сверка перестала создавать версию building: исключение лишнее"
        assert all("FamilyParameterSchema" in finding.what for finding in findings), findings

    @pytest.mark.parametrize(
        "module",
        [
            "services.work_variants",
            "services.family_change",
            "services.work_families",
            "services.review",
        ],
    )
    def test_taking_a_writer_out_of_the_allowlist_makes_the_check_red(self, module):
        assert module in scan_tree(), module

        problems = violations(scan_tree(), (RECONCILE_ALLOWLIST - {module}) | {RECONCILER_MODULE})

        assert any(problem.startswith(f"{module}:") for problem in problems)

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
            "services.work_variants",
            "services.family_change",
        ):
            assert module in found, module

    def test_the_scan_finds_every_protected_field_of_the_variant_feature(self):
        """Каждое поле и таблица спеки вариантов §2.7 найдены хотя бы в одном
        модуле дерева: сканер не пропускает целый класс записей."""
        what = " ".join(finding.what for findings in scan_tree().values() for finding in findings)

        for needle in (
            "work_variant_id",
            "pending_family_id",
            "WorkVariant",
            "WorkVariantValue",
            "ContextParameterValue",
            "FamilyParameter",
            "FamilyParameterValue",
            "FamilyParameterSchema",
            "catalog_positions.kind",
        ):
            assert needle in what, needle

    def test_the_protected_models_are_exactly_the_variant_tables_of_the_schema(self):
        from models import Base

        tables = {
            table.name
            for table in Base.metadata.tables.values()
            if _VARIANT_TABLE_PATTERN.match(table.name)
        }
        mapped = {
            mapper.class_.__name__: mapper.local_table.name
            for mapper in Base.registry.mappers
            if mapper.class_.__name__ in _VARIANT_MODELS
        }

        assert set(mapped) == set(_VARIANT_MODELS)
        assert set(mapped.values()) == tables


class TestEveryAllowlistedModuleHasAHooksTest:
    def test_each_module_is_named_in_a_hooks_test_file(self):
        texts = _hooks_texts()
        assert texts, f"не найдено ни одного {HOOKS_TESTS_GLOB}"

        missing = modules_without_hooks_test(RECONCILE_ALLOWLIST, texts)

        assert missing == [], f"у модулей списка нет теста точки в {HOOKS_TESTS_GLOB}: {missing}"

    def test_the_matrix_of_the_variant_feature_is_one_of_the_hooks_files(self):
        """Матрица точек вариантов — в выборке структурного теста, а модуль
        `work_variants` назван именно в ней: в файлах остальных точек его нет."""
        matrix = BACKEND / "tests" / "integration" / "test_semantic_queue_hooks_work_variants.py"
        files = sorted((BACKEND / "tests" / "integration").glob(HOOKS_TESTS_GLOB))
        assert matrix in files
        others = [path.read_text(encoding="utf-8") for path in files if path != matrix]

        assert modules_without_hooks_test({"services.work_variants"}, [matrix.read_text(encoding="utf-8")]) == []
        assert modules_without_hooks_test({"services.work_variants"}, others) == ["services.work_variants"]


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
    "context_field_work_variant": "ctx.work_variant_id = 4",
    "context_created_with_variant": "CatalogContext(bucket_id=1, work_variant_id=2)",
    "update_context_variant": "db.execute(update(CatalogContext).where(x).values(work_variant_id=None))",
    "raw_update_context_variant": "db.execute(text('UPDATE catalog_contexts SET work_variant_id = NULL'))",
    "raw_update_context_pending": "db.execute(text('UPDATE catalog_contexts SET pending_family_id = NULL'))",
    "variant_created": "db.add(WorkVariant(family_id=1, schema_id=2, values_key='k'))",
    "variant_value_inserted": "db.execute(sa.insert(WorkVariantValue), rows)",
    "variant_pg_insert": "db.execute(pg_insert(WorkVariant).values(rows))",
    "variant_updated": "db.execute(sa.update(WorkVariant).where(x).values(status='archived'))",
    "variant_value_updated": "db.execute(sa.update(WorkVariantValue).values(value_id=1))",
    "context_values_deleted": "db.execute(sa.delete(ContextParameterValue).where(x))",
    "context_values_inserted": "db.execute(sa.insert(ContextParameterValue), rows)",
    "family_parameter_created": "db.add(FamilyParameter(schema_id=1, ordinal=1, name='x'))",
    "family_parameter_value_created": "FamilyParameterValue(parameter_id=1, value='a')",
    "family_value_pg_insert": "db.execute(pg_insert(FamilyParameterValue).values(rows))",
    "schema_created": "FamilyParameterSchema(family_id=1, version=1, status='building')",
    "schema_pg_insert": "db.execute(pg_insert(FamilyParameterSchema).values(family_id=1))",
    "variant_status_assigned": "variant.status = 'archived'",
    "target_variant_assigned": "target_variant.merged_into_id = 3",
    "schema_attribute_assigned": "schema.version = 3",
    "orm_delete_variant": "db.delete(variant)",
    "orm_delete_schema": "db.delete(old_schema)",
    "query_update_variant": "db.query(WorkVariant).filter(x).update({'status': 'archived'})",
    "query_delete_context_values": "db.query(ContextParameterValue).filter(x).delete()",
    "bulk_insert_variant_values": "db.bulk_insert_mappings(WorkVariantValue, rows)",
    "raw_update_variants": "db.execute(text('UPDATE work_variants SET status = 1'))",
    "raw_delete_context_values": "db.execute(text('DELETE FROM context_parameter_values WHERE context_id = 1'))",
    "raw_insert_family_parameters": "db.execute(text('INSERT INTO family_parameter_values (value) VALUES (1)'))",
    "catalog_kind_assigned": "row.kind = 'HEADER'",
    "catalog_kind_assigned_via_attribute": "locked.catalog.kind = 'POSITION'",
    "catalog_kind_updated": "db.execute(sa.update(CatalogPosition).where(x).values(kind='HEADER'))",
    "catalog_update_unknown_values": "db.execute(sa.update(CatalogPosition).values(**changes))",
    "catalog_kind_setattr": "setattr(row, 'kind', 'HEADER')",
    "query_update_catalog_kind": "db.query(CatalogPosition).filter(x).update({CatalogPosition.kind: 'HEADER'})",
    "raw_update_catalog_kind": "db.execute(text('UPDATE catalog_positions SET kind = 1'))",
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
    "context_variant_hint": "context.variant_split_hint = None",
    "context_variant_paths_hash": "context.variant_paths_hash = 'h'",
    "reading_the_variant_field": "variant_id = ctx.work_variant_id",
    "variant_selected": "db.execute(sa.select(WorkVariant).where(x))",
    "variant_value_selected": "db.execute(sa.select(ContextParameterValue).where(x))",
    "job_kind_assigned": "job.kind = 'family_schema'",
    "catalog_other_attribute_assigned": "row.standard_job_title = 'x'",
    "other_receiver_status": "family.status = 'active'",
    "catalog_other_field_updated": "db.execute(sa.update(CatalogPosition).values(status='ok'))",
    "catalog_position_inserted": "db.execute(pg_insert(CatalogPosition).on_conflict_do_nothing())",
    "query_update_catalog_other_field": "db.query(CatalogPosition).filter(x).update({'status': 'ok'})",
    "raw_select_variants": "db.execute(text('SELECT * FROM work_variants'))",
    "raw_update_other_table_with_kind": "db.execute(text('UPDATE semantic_jobs SET kind = 1'))",
    "setattr_job_kind": "setattr(job, 'kind', 'x')",
    "orm_delete_event": "db.delete(event)",
    "variant_model_selected_by_query": "db.query(WorkVariant).filter(x).all()",
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
