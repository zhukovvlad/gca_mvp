"""Страж пропусков: в полном прогоне skip допустим только из явного реестра.

P1 внешнего ревью PR #15: DoD фичи pytest-parallel-workers держится на счёте
«skipped ровно 6», но сам счёт нигде не исполнялся — CI просто запускал pytest,
и снятие 7 реестра (сотни молча пропущенных integration-тестов при коде
возврата 0) прошло бы CI зелёным. К тому же в CI пропусков законно 13, а не 6:
семь тестов реальных оферт скипаются без каталога samples/ (реальные файлы не
коммитятся, AGENTS.md §9) — то есть закреплять надо СОСТАВ, а не число.

Здесь — unit-слой чистой функции `unexpected_skips`; исполнение в прогоне —
хуки в conftest.py (доказано снятием: сломанный генератор имени при живом
барьере теперь роняет прогон, см. devlog).
"""
import tests.conftest as conftest_module

AUTH_SKIP = (
    "tests/test_auth_coverage.py::test_endpoint_requires_auth[GET /api/v1/health]",
    "Skipped: Публичный endpoint — auth не требуется",
)
SAMPLES_SKIP = (
    "tests/unit/parser/test_estimate.py::TestParseEstimateOnRealSamples::test_x",
    "Skipped: Каталог samples/ пуст или отсутствует — реальные оферты не коммитятся (AGENTS.md §9)",
)
BARRIER_SKIP = (
    "tests/integration/test_import_pipeline.py::TestHappyPath::test_upload",
    "Skipped: TEST_DATABASE_URL указывает на базу 'gca_test_gw0' — ожидается имя, "
    "оканчивающееся на '_test'; отказ от DROP SCHEMA",
)


def test_allowed_skips_pass():
    """Оба законных вида пропуска (auth-coverage и samples) — не нарушители."""
    assert conftest_module.unexpected_skips([AUTH_SKIP, SAMPLES_SKIP]) == []


def test_db_engine_barrier_skip_is_flagged():
    """Skip барьера db_engine — ровно тот дефект, ради которого страж заведён."""
    offenders = conftest_module.unexpected_skips([AUTH_SKIP, BARRIER_SKIP, SAMPLES_SKIP])
    assert offenders == [BARRIER_SKIP]


def test_allowed_file_with_foreign_reason_is_flagged():
    """Реестр закрепляет состав, а не имя файла: чужая причина в разрешённом файле — нарушитель."""
    foreign = ("tests/test_auth_coverage.py::test_new", "Skipped: что-то новое")
    assert conftest_module.unexpected_skips([foreign]) == [foreign]


def test_allowed_reason_in_foreign_file_is_flagged():
    """И наоборот: разрешённая причина не даёт индульгенции чужому файлу."""
    foreign = (
        "tests/integration/test_matching.py::test_y",
        "Skipped: Каталог samples/ пуст или отсутствует",
    )
    assert conftest_module.unexpected_skips([foreign]) == [foreign]


def test_empty_run_has_no_offenders():
    assert conftest_module.unexpected_skips([]) == []
