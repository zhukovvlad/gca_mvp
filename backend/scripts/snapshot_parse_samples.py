"""Снимок разбора всех образцов samples/ для регрессии по классам (план фичи
«колонки по заголовкам», задача 1/6; спека §7).

Запуск из backend/:
    PYTHONIOENCODING=utf-8 uv run python -m scripts.snapshot_parse_samples before

Пишет по одному JSON на файл в samples/_snapshots/<метка>/<sha256>.json плюс
manifest.json с происхождением снимка (коммит, дерево backend/parser, версия
парсера, дайджесты). Непустая метка НЕ перезаписывается без --force: снимок
«до» — единственный эталон побайтного сравнения, и повторный запуск кодом
«после» уничтожил бы его молча, дав ложнозелёное сравнение.

Каталог /samples целиком в .gitignore — снимки не коммитятся. В консоль
печатаются только счётчики и дайджесты: имена файлов несут реквизиты
контрагентов и в вывод не попадают (AGENTS.md §9).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from parser import PARSER_VERSION, parse_estimate

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "samples"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def snapshot_one(path: Path) -> dict:
    payload: dict = {"relpath": str(path.relative_to(SAMPLES_DIR))}
    try:
        result = parse_estimate(str(path))
    except Exception as exc:  # noqa: BLE001 — снимок фиксирует и отказ, и его текст
        payload.update(status="error", error=f"{type(exc).__name__}: {exc}")
    else:
        payload.update(
            status="ok",
            parser_version=result.parser_version,
            warnings=result.warnings,
            data=result.data,
        )
    return payload


def main() -> int:
    if len(sys.argv) < 2:
        print("использование: snapshot_parse_samples.py <метка> [--force]")
        return 1

    label = sys.argv[1]
    force = "--force" in sys.argv[2:]
    out_dir = SAMPLES_DIR / "_snapshots" / label
    if out_dir.is_dir() and any(out_dir.iterdir()) and not force:
        print(f"метка «{label}» уже содержит снимок; перезапись только с --force")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    counts = {"ok": 0, "error": 0}
    digests: list[str] = []
    for path in sorted(SAMPLES_DIR.rglob("*.xlsx")):
        if path.name.startswith("~$") or "_snapshots" in path.parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digests.append(digest)
        payload = snapshot_one(path)
        (out_dir / f"{digest}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        counts[payload["status"]] += 1
        print(digest[:12], payload["status"])

    manifest = {
        "label": label,
        "git_head": _git("rev-parse", "HEAD"),
        "parser_tree": _git("rev-parse", "HEAD:backend/parser"),
        "dirty": bool(_git("status", "--porcelain", "--", "backend/parser")),
        "parser_version": PARSER_VERSION,
        "count": len(digests),
        "digests": sorted(digests),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"итого: ok={counts['ok']} error={counts['error']} "
          f"parser={manifest['parser_version']} dirty={manifest['dirty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
