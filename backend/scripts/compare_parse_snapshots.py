"""Сравнение снимков «до/после» по четырём классам спеки §7.

Никаких имён файлов в выводе — только дайджесты и счётчики. parser_version
сравнению не подлежит (3.1.0 → 4.0.0 — ожидаемая смена). Побайтность — это
равенство сериализаций json.dumps(data, ensure_ascii=False, indent=1), то есть
той же формы, в которой снимки записаны.

Запуск из backend/:
    PYTHONIOENCODING=utf-8 uv run python -m scripts.compare_parse_snapshots before after
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS_DIR = REPO_ROOT / "samples" / "_snapshots"
EXPECTED_COUNTS = {"class1": 18, "class2": 3, "class3": 1, "class4": 5}

#: База фичи: эталон «до» обязан быть снят деревом парсера ЭТОГО коммита,
#: а не тем, что окажется в origin/main на момент сравнения.
BASELINE_COMMIT = "310c6bf3db20528fb1e1ce85de963b1bafdd4daf"
BASELINE_PARSER_VERSION = "3.1.0"
AFTER_PARSER_VERSION = "4.0.0"


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def _git_tree(rev: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", f"{rev}:backend/parser"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()


def check_manifests(before_dir: Path, after_dir: Path) -> list[str]:
    """Происхождение снимков. Любая строка в ответе — отказ от сравнения:
    сравнивать подделанный или пересданный не тем кодом эталон бессмысленно.

    «До» привязан к дереву парсера БАЗОВОГО коммита, «после» — к дереву
    парсера ТЕКУЩЕГО HEAD: иначе старый снимок «после» от другого дерева с
    той же версией 4.0.0 прошёл бы проверку, и сравнение говорило бы не о
    проверяемой реализации.
    """
    problems: list[str] = []
    before = json.loads((before_dir / "manifest.json").read_text(encoding="utf-8"))
    after = json.loads((after_dir / "manifest.json").read_text(encoding="utf-8"))
    if before["parser_tree"] != _git_tree(BASELINE_COMMIT):
        problems.append("эталон «до» снят не деревом базового коммита")
    if after["parser_tree"] != _git_tree("HEAD"):
        problems.append("снимок «после» снят не деревом текущего HEAD — пересдать")
    if before["parser_version"] != BASELINE_PARSER_VERSION:
        problems.append(f"версия «до» {before['parser_version']} ≠ {BASELINE_PARSER_VERSION}")
    if after["parser_version"] != AFTER_PARSER_VERSION:
        problems.append(f"версия «после» {after['parser_version']} ≠ {AFTER_PARSER_VERSION}")
    if before["dirty"] or after["dirty"]:
        problems.append("снимок снят при грязном backend/parser")
    if before["digests"] != after["digests"]:
        problems.append("наборы файлов образцов в снимках различаются")
    if before["count"] != len(before["digests"]) or after["count"] != len(after["digests"]):
        problems.append("count манифеста расходится со списком дайджестов")
    return problems


def _stale_digest_files(snapshot_dir: Path, known_digests: list[str]) -> list[str]:
    """<sha256>.json на диске, которого манифест ЭТОЙ ЖЕ метки не называет.

    `snapshot_parse_samples.py` не удаляет старые payload-файлы при повторном
    прогоне той же метки с `--force` (задача 1) — каталог мог остаться от
    прежнего прогона. Дайджесты для сравнения читаются из манифеста, а не из
    листинга каталога (манифест авторитетен); лишний файл на диске называется
    здесь как проблема, а не подмешивается в сравнение молча.
    """
    known = set(known_digests)
    stale: list[str] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        if path.stem not in known:
            stale.append(path.stem)
    return stale


def rename_organizer_to_comment(node):
    """Единственная законная дельта класса 2: ключ
    total_cost_for_organizer_quantity становится comment_contractor НА ТОМ ЖЕ
    МЕСТЕ словаря. Любое расхождение сверх этого — нарушение."""
    if isinstance(node, dict):
        return {
            ("comment_contractor" if key == "total_cost_for_organizer_quantity" else key):
                rename_organizer_to_comment(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [rename_organizer_to_comment(item) for item in node]
    return node

def classify(before, after) -> str:
    if before["status"] == "error" and after["status"] == "error":
        return "class4" if before["error"] == after["error"] else "VIOLATION"
    if before["status"] == "error" and after["status"] == "ok":
        return "class3"
    if before["status"] == "ok" and after["status"] == "error":
        return "VIOLATION"
    if dumps(before["data"]) == dumps(after["data"]):
        return "class1" if before["warnings"] == after["warnings"] else "VIOLATION"
    if dumps(rename_organizer_to_comment(before["data"])) == dumps(after["data"]):
        removed = [w for w in before["warnings"] if w not in after["warnings"]]
        added = [w for w in after["warnings"] if w not in before["warnings"]]
        ok = all("колонок вместо" in w for w in removed) and all(
            "не совпадает с ожидаемым" in w for w in added
        )
        return "class2" if ok else "VIOLATION"
    return "VIOLATION"


def main() -> int:
    """Происхождение → объединение дайджестов → классификация → счётчики.

    Успех читается по коду возврата ОДНОЙ команды, без конвейеров
    (docs/insights/silent-test-runs.md): весь вывод — печать, решение — return.
    """
    if len(sys.argv) != 3:
        print("использование: compare_parse_snapshots.py <before> <after>")
        return 1

    before_dir = SNAPSHOTS_DIR / sys.argv[1]
    after_dir = SNAPSHOTS_DIR / sys.argv[2]

    problems = check_manifests(before_dir, after_dir)
    if not problems:
        # Манифесты консистентны — теперь можно доверять их спискам дайджестов
        # для поиска фантомных payload-файлов (гейт до сравнения данных).
        before_manifest = json.loads((before_dir / "manifest.json").read_text(encoding="utf-8"))
        after_manifest = json.loads((after_dir / "manifest.json").read_text(encoding="utf-8"))
        for label, snapshot_dir, manifest in (
            ("before", before_dir, before_manifest),
            ("after", after_dir, after_manifest),
        ):
            stale = _stale_digest_files(snapshot_dir, manifest["digests"])
            if stale:
                problems.append(
                    f"каталог «{label}» содержит {len(stale)} <sha256>.json, не "
                    "названных манифестом (остаток прежнего прогона той же метки "
                    "с --force?): " + ", ".join(d[:12] for d in stale)
                )

    if problems:
        for problem in problems:
            print(problem)
        return 1

    before_manifest = json.loads((before_dir / "manifest.json").read_text(encoding="utf-8"))
    after_manifest = json.loads((after_dir / "manifest.json").read_text(encoding="utf-8"))
    before_digests = set(before_manifest["digests"])
    after_digests = set(after_manifest["digests"])
    all_digests = sorted(before_digests | after_digests)

    counts: dict[str, int] = {"class1": 0, "class2": 0, "class3": 0, "class4": 0, "VIOLATION": 0}
    by_class: dict[str, list[str]] = {key: [] for key in counts}

    for digest in all_digests:
        before_path = before_dir / f"{digest}.json"
        after_path = after_dir / f"{digest}.json"
        if digest not in before_digests or digest not in after_digests or not before_path.is_file() or not after_path.is_file():
            # Не должно случиться после check_manifests (digests совпадают),
            # но не доверять листингу диска и не падать необработанным —
            # называть нарушением явно, а не молча пропускать файл.
            outcome = "VIOLATION"
        else:
            before_payload = json.loads(before_path.read_text(encoding="utf-8"))
            after_payload = json.loads(after_path.read_text(encoding="utf-8"))
            outcome = classify(before_payload, after_payload)
        counts[outcome] += 1
        by_class[outcome].append(digest[:12])

    for cls in ("class1", "class2", "class3", "class4", "VIOLATION"):
        print(f"{cls}={counts[cls]}: {', '.join(by_class[cls])}")

    expected = {**EXPECTED_COUNTS, "VIOLATION": 0}
    actual = {key: counts[key] for key in expected}
    if actual != expected:
        print(
            f"счётчики не совпали с ожидаемыми {EXPECTED_COUNTS} и нулём VIOLATION "
            f"— получено {actual}"
        )
        return 1

    print(
        f"итого: class1={counts['class1']} class2={counts['class2']} "
        f"class3={counts['class3']} class4={counts['class4']}, нарушений нет"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
