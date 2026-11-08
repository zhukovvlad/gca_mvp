"""Импорты ядер ацикличны (спека лестницы аналогов, решение плана 3)."""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

import services.analog_ladder as analog_ladder
import services.price_basis as price_basis

BACKEND = pathlib.Path(analog_ladder.__file__).resolve().parents[1]


def _imported_modules(module: ModuleType) -> list[str]:
    """Все импортированные модули файла, включая импорты внутри функций."""
    assert module.__file__ is not None
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.append(base)
            # `from services import our_price` тоже тянет модуль
            found.extend(f"{base}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize(
    "order",
    [
        ("services.analog_ladder", "services.our_price"),
        ("services.our_price", "services.analog_ladder"),
    ],
)
def test_fresh_interpreter_imports_both_cores_in_either_order(order: tuple[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {order[0]}; import {order[1]}"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.mark.parametrize("module", [analog_ladder, price_basis])
def test_no_import_of_our_price_anywhere_in_the_file(module: ModuleType) -> None:
    modules = _imported_modules(module)
    assert not [m for m in modules if m == "services.our_price" or m.startswith("services.our_price.")]


def test_price_basis_imports_nothing_from_services() -> None:
    modules = _imported_modules(price_basis)
    assert not [m for m in modules if m == "services" or m.startswith("services.")]
