"""Константы парсера смет ГП.

Перенос `app/constants.py` из `parser_tender_xlsx@0e178c0` (AGENTS.md §2).
Содержит:

* текстовые маркеры для поиска якорей на листе XLSX;
* ключи результирующей JSON-структуры (она уходит в `estimate_raw_data.raw_data`
  и разбирается импортом фазы 4 — переименовывать ключи нельзя);
* границы сканирования листа.

Отличия от исходника перечислены в `docs/phase3-parser.md`.
"""

# ==============================================================================
# === Ключевые слова и префиксы для поиска и парсинга данных в XLSX файлах ===
# ==============================================================================

# Заголовок блока "Дополнительная информация" о подрядчике (ищется в колонке A).
SEARCH_KEYWORD_ADDITIONAL_INFO = "Дополнительная информация"

# Итоговая строка "отклонение от расчетной стоимости" в таблице позиций.
# В сметах ГП baseline отсутствует, но маркер сохранён: постобработка использует
# его, чтобы вычистить поля отклонений (см. postprocess.normalize_lots_json_structure).
TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST = "отклонение от расчетной стоимости"

# Заголовок предложения "Расчетная стоимость" (baseline). В сметах ГП его нет —
# postprocess штатно подставляет "Расчетная стоимость отсутствует".
TABLE_PARSE_BASELINE_COST = "расчетная стоимость"

# Префикс ячейки-маркера строки с заголовками контрагентов.
TABLE_PARSE_CONTRACTOR_TITLE = "наименование контрагента"

# Информация об исполнителе документа (в нижней части листа).
TABLE_PARSE_EXECUTOR = "исполнитель"

# Префикс ячейки, обозначающей начало нового лота (колонка D).
PARSE_TABLE_LOT_NUMBER = "лот №"

# Строка с датой составления документа (источник estimates.data_prepared_on_date).
TABLE_PARSE_PREPARATION_DATE = "дата составления"

# Строка с телефоном исполнителя.
TABLE_PARSE_TELEPHONE = "тел"

# Строка "Предмет тендера" в шапке документа.
TABLE_PARSE_TENDER_SUBJECT = "Предмет тендера:"

# Строка "Объект" в шапке документа.
TABLE_PARSE_OBJECT = "Объект"

# Строка "Адрес" объекта в шапке документа.
TABLE_PARSE_ADDRESS = "Адрес"

# Итоговая строка "первоначальная стоимость" в таблице позиций.
TABLE_PARSE_INITIAL_COST = "первоначальная стоимость"

# Заголовок колонки веса позиции. Пишется по РЕАЛЬНОМУ заголовку файла —
# "Предлагаемое" (предложенное подрядчиком), не "Предполагаемое"
# (AGENTS.md §4, docs/phase0-input-data.md). На этой колонке держится
# семантика средневзвешенной ставки §6, поэтому раскладка проверяется
# явно — см. layout.check_estimate_layout.
TABLE_PARSE_SUGGESTED_QUANTITY = "Предлагаемое количество"

#: Заголовки общих колонок таблицы позиций. Замерены на трёх реальных офертах
#: (спека Ф2 §1): строка 9, A «№ п/п», B «№ раздела», C «Статья СМР»,
#: D «Наименование работ». Сверяются ВСЕ ЧЕТЫРЕ: колонки читаются по фиксированным
#: позициям, поэтому чужая шапка делает недостоверным весь позиционный разбор,
#: а не одну колонку статьи.
TABLE_PARSE_POSITION_COLUMN_HEADERS: dict[int, str] = {
    1: "№ п/п",
    2: "№ раздела",
    3: "Статья СМР",
    4: "Наименование работ",
}

#: Название агрегатной строки допработ в колонке D. Замер: 159-ТУ строка 1523;
#: в 42-ТУ и 449-ТУ такой строки нет вовсе — это валидное состояние.
TABLE_PARSE_ADDITIONAL_WORKS_TITLE = "Дополнительные работы"


# ==============================================================================
# === Ключи для формирования результирующей JSON-структуры ===
# ==============================================================================

# -- Общие ключи для итоговых сумм тендера/лота и специфических полей --
# Ф4a: три независимых ключа вместо одного двусмысленного. Прежние
# `total_cost_with_vat` и `vat` удалены, а не переосмыслены: `raw_data`
# неизменяем и backfill невозможен, поэтому одно имя с двумя значениями у
# старых и новых смет различалось бы только по `parser_version`
# (спека Ф4a §2.1).
JSON_KEY_TOTAL_COST_INCLUDING_VAT = "total_cost_including_vat"  # валовое ИТОГО
JSON_KEY_VAT_AMOUNT = "vat_amount"  # СУММА НДС; ставка — Ф4б, имя `vat_rate` за ней
JSON_KEY_TOTAL_COST_EXCLUDING_VAT = "total_cost_excluding_vat"  # ИТОГО без НДС
JSON_KEY_INITIAL_COST = "initial_cost"  # Первоначальная стоимость
# Отклонение предложения подрядчика от базовой (расчетной) стоимости.
JSON_KEY_DEVIATION_FROM_CALCULATED_COST = "deviation_from_baseline_cost"

# Метки блока итогов в колонке A. Сравниваются ТОЧНО, после нормализации
# (спека Ф4a §2.2): замер даёт побайтово одинаковые метки во всех четырёх
# известных файлах, поэтому строгость ничего не стоит, а нестрогость и есть
# исходный дефект.
TABLE_PARSE_SUMMARY_INCLUDING_VAT = "ИТОГО, руб. с учетом НДС"
TABLE_PARSE_SUMMARY_VAT = "В том числе НДС"
TABLE_PARSE_SUMMARY_EXCLUDING_VAT = "ИТОГО, руб. без учета НДС"

# -- Ключи для описания отдельных позиций (работ/материалов) в предложении --
JSON_KEY_NUMBER = "number"  # Порядковый номер позиции в списке
JSON_KEY_CHAPTER_NUMBER = "chapter_number"  # Номер раздела/главы (для иерархии)
JSON_KEY_ARTICLE_SMR = "article_smr"  # Код или статья СМР
JSON_KEY_JOB_TITLE = "job_title"  # Наименование работы, услуги или материала
JSON_KEY_JOB_TITLE_NORMALIZED = "job_title_normalized"  # Нормализованное наименование
# Комментарий организатора. В tenders-go ключ назывался `comment_organazier`
# (опечатка); в нашей схеме и здесь — `comment_organizer` (docs/phase3-start.md §7).
JSON_KEY_COMMENT_ORGANIZER = "comment_organizer"
JSON_KEY_UNIT = "unit"  # Единица измерения ("шт.", "м2", "компл.")
JSON_KEY_QUANTITY = "quantity"  # Количество по документации (объем организатора)
JSON_KEY_SUGGESTED_QUANTITY = "suggested_quantity"  # Количество, предложенное участником
JSON_KEY_IS_CHAPTER = "is_chapter"  # Признак раздела (группы), а не позиции
JSON_KEY_CHAPTER_REF = "chapter_ref"  # Ссылка на родительский раздел

# -- Ключи для стоимостных показателей внутри каждой позиции --
JSON_KEY_UNIT_COST = "unit_cost"  # Детализация стоимости за единицу
JSON_KEY_TOTAL_COST = "total_cost"  # Детализация общей стоимости по позиции

# -- Компоненты стоимости (вложены в unit_cost и total_cost) --
JSON_KEY_MATERIALS = "materials"
JSON_KEY_WORKS = "works"
JSON_KEY_INDIRECT_COSTS = "indirect_costs"
JSON_KEY_TOTAL = "total"

# -- Данные, предоставляемые подрядчиком по каждой позиции --
JSON_KEY_COMMENT_CONTRACTOR = "comment_contractor"  # Комментарий участника
# Стоимость позиции, пересчитанная на объемы организатора.
JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST = "total_cost_for_organizer_quantity"

# -- Ключи для описания структуры лотов --
JSON_KEY_LOTS = "lots"
JSON_KEY_LOT_INDEX = "lot_"  # Префикс ключей отдельных лотов ("lot_1", "lot_2")
JSON_KEY_LOT_TITLE = "lot_title"

# -- Ключи для описания предложений подрядчиков внутри каждого лота --
JSON_KEY_PROPOSALS = "proposals"

# -- Метаданные и основная информация о подрядчике --
JSON_KEY_CONTRACTOR_INDEX = "contractor_"  # Префикс ключей ("contractor_1")
JSON_KEY_CONTRACTOR_TITLE = "title"
JSON_KEY_CONTRACTOR_INN = "inn"
JSON_KEY_CONTRACTOR_ADDRESS = "address"
JSON_KEY_CONTRACTOR_ACCREDITATION = "accreditation"
JSON_KEY_CONTRACTOR_COORDINATE = "contractor_coordinate"  # Координата первой ячейки ("D4")
JSON_KEY_CONTRACTOR_WIDTH = "contractor_width"  # colspan объединённой ячейки заголовка
JSON_KEY_CONTRACTOR_HEIGHT = "contractor_height"  # rowspan объединённой ячейки заголовка
JSON_KEY_CONTRACTOR_ITEMS = "contractor_items"  # Позиции и итоги данного подрядчика
JSON_KEY_CONTRACTOR_POSITIONS = "positions"
JSON_KEY_CONTRACTOR_SUMMARY = "summary"
JSON_KEY_CONTRACTOR_ADDITIONAL_INFO = "additional_info"
JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS = "additional_works"  # Агрегатная строка допработ
JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW = "source_row"  # Номер строки листа, откуда она взята

# -- Выделенное базовое ("расчетное") предложение в рамках лота --
JSON_KEY_BASELINE_PROPOSAL = "baseline_proposal"

# -- Информация об исполнителе документа --
JSON_KEY_EXECUTOR = "executor"
JSON_KEY_EXECUTOR_NAME = "executor_name"
JSON_KEY_EXECUTOR_PHONE = "executor_phone"
JSON_KEY_EXECUTOR_DATE = "executor_date"

# -- Общая (заголовочная) информация о тендере/смете --
JSON_KEY_TENDER_ID = "tender_id"
JSON_KEY_TENDER_TITLE = "tender_title"
JSON_KEY_TENDER_OBJECT = "tender_object"
JSON_KEY_TENDER_ADDRESS = "tender_address"


# ==============================================================================
# === Границы сканирования листа ===
# ==============================================================================

# Диапазон строк, в котором ищется шапка документа (предмет/объект/адрес).
# В исходнике было жёстко 3..5. В сметах ГП шапка на строку выше — "Предмет
# тендера" лежит в строке 2, и исходный диапазон терял tender_id/tender_title
# (замер — docs/phase3-parser.md). Диапазон расширен вверх; при дублировании
# ключа побеждает нижняя строка, поэтому для тендерных таблиц поведение прежнее.
HEADER_SCAN_ROW_START = 2
HEADER_SCAN_ROW_END = 5

# Диапазон строк, в котором ищется строка заголовков контрагентов.
CONTRACTOR_SCAN_ROW_START = 4
CONTRACTOR_SCAN_ROW_END = 10

# Строка, с которой начинается поиск маркеров лотов ("Лот №" в колонке D).
START_INDEXING_LOT_ROW = 10

# Жёсткий предел ширины сканирования шапки контрагентов.
#
# Зачем: в реальных выгрузках встречается пустая стилизованная ячейка в XFD
# (колонка 16384), из-за которой `ws.max_column` равен 16384 при фактических
# 24 колонках данных (docs/phase0-input-data.md). Полноширинный скан семи строк
# шапки стоил 115 тыс. ячеек. Все остальные сканы парсера ограничены правой
# границей блока подрядчика (sheet.contractor_last_column), а вот сама шапка
# сканируется ДО того, как эта граница известна, — здесь и нужен предел.
#
# 256 колонок с запасом покрывают смету ГП (24 колонки, один подрядчик) и
# тендерную таблицу примерно на 20 подрядчиков по 11 колонок. Подрядчик правее
# 256-й колонки найден не будет: read_contractors вернёт одну ячейку-маркер,
# и parse_worksheet откажет с EstimateParseError «самого подрядчика в ней
# нет» — отказ, не предупреждение.
MAX_HEADER_SCAN_COLUMN = 256
