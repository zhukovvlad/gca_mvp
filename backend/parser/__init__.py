"""Парсер смет генподряда (XLSX → JSON).

Перенос `app/excel_parser/` из `parser_tender_xlsx@0e178c0` (AGENTS.md §2),
адаптированный под смету ГП: один подрядчик, нет колонки «Расчётная стоимость»
(baseline), заполнено «Предлагаемое количество».

Пакет самодостаточен: он не импортирует ничего из слоя БД или FastAPI. Внешние
зависимости — `openpyxl` (чтение книги) и `spacy` с моделью `ru_core_news_sm`
(лемматизация наименований работ).

Точка входа — `parse_estimate`; она возвращает `ParseResult`, чья структура
целиком сохраняется в `import_jobs.parsed_data` (вместе с `parser_version`) —
это и есть точный результат разбора файла; `estimate_raw_data.raw_data` под
сметой хранит лишь проекцию этих данных. Предупреждения идут в
`import_jobs.warnings`.
Отдельно экспортируется `normalize_job_title_with_lemmatization`: фаза 4 строит
на ней `catalog_positions.normalized_job_title` и `cache_key` матчинга
(AGENTS.md §4–§5).

Внутренние шаги разбора наружу НЕ реэкспортируются, и это осознанно. Каждый из
них лежит в модуле, названном по самой функции (`read_headers.read_headers`,
`get_summary.get_summary`, …), и реэкспорт затирал бы атрибут-модуль: после
`from .read_headers import read_headers` выражение `parser.read_headers`
означало бы функцию, а не модуль. Нужен шаг разбора — берите его полным путём:
`from parser.read_headers import read_headers`.

Отличия от исходника и замеры — `docs/phase3-parser.md`.
"""

from .estimate import (
    PARSER_VERSION,
    EstimateParseError,
    ParseResult,
    parse_estimate,
    parse_worksheet,
)
from .sanitize_text import (
    NormalizationUnavailableError,
    basic_clean_job_title,
    normalize_job_title_with_lemmatization,
    prepare_for_fts_query,
)

__all__ = [
    # Точка входа
    "parse_estimate",
    "parse_worksheet",
    "ParseResult",
    "EstimateParseError",
    "PARSER_VERSION",
    # Нормализация наименований — контракт с матчингом фазы 4
    "normalize_job_title_with_lemmatization",
    "basic_clean_job_title",
    "prepare_for_fts_query",
    "NormalizationUnavailableError",
]
