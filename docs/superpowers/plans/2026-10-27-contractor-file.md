# План: фича 4б «Файл подрядчику»

**Спека:** `docs/superpowers/specs/2026-10-27-contractor-file-design.md` (гейт 2 одобрен 27.10.2026, три круга Codex, редакция 3, `fe7e882`)
**Стоит на:** спека 4а `docs/superpowers/specs/2026-10-23-our-price-design.md`, devlog 4а `docs/devlog/2026-10-24-our-price.md` (R1–R44)
**Дизайн (фон):** `docs/superpowers/design/2026-10-23-catalog-surfaces.md`, макет — `.../2026-10-23-catalog-surfaces/kp-vs-ours.html` (вид «В», блок «Для выгрузки»)
**Ветка:** `feat/contractor-file`, черновой PR #73
**Статус:** редакция 2, 27.10.2026 — на ревью, гейт 3 не пройден.

> **Круг 1 гейта 3 (27.10, план на `3e3409d`)**: два замечания P2 Codex, оба
> подтверждены:
> 1. кнопка «к шаблону» требовала `template_text`, и свой текст у строки без
>    шаблона нельзя было снять — кнопка теперь при любом `kind = custom`, тест
>    «свой текст без шаблона → „к шаблону“ → `DELETE`» (Task 11);
> 2. Task 3 требовал совпадения сравнения системы по суммам и за единицу, а
>    округление до копеек его ломает (объём 100, 100,40 против 100,00 —
>    воспроизведено `Decimal` с `ROUND_HALF_UP`) — решают только суммы,
>    контрпример в тестах Task 3 и Task 4; фраза «то же сравнение» снята и из
>    спеки §2.7, §2.8, §2.18.

> Исполнителю: задачи идут по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать) и ревью задачи (`docs/process/implementation.md`) до следующей.
> Адреса `§N` без уточнения — разделы спеки 4б; «спека 4а §N» — спеки 4а;
> «решение N» — пронумерованные решения этого плана ниже. **Защит,
> дублирующих предикат, не класть** (урок 4а: мёртвые и равносильные термы —
> главная причина кругов ревью). Числа проверок — нижние границы: точное ПОСЛЕ
> неизвестно до написания тестов. ДО сняты 27.10.2026 на `fe7e882`:
> `uv run pytest <каталог> --collect-only -q -k <шаблон>` (бэкенд), `npx vitest
> list <путь>` (фронт). **Все новые бэкенд-тесты — в файлах `test_kp_file_*.py`,
> шаблон `-k test_kp_file`** (ДО 0 в `tests/unit` и в `tests/integration`);
> тесты 4а (`-k test_our_price`: unit 225, integration 425) обязаны остаться
> зелёными без правки ожиданий — кроме названных в задаче полей, которые 4б
> добавляет в ответ.

## Global Constraints

- **Деньги** — `Decimal` до границы ответа, строки в JSON через `decimal_json`,
  ни одного `float` ни на входе, ни на выходе (`AGENTS.md` §3). В ячейку xlsx —
  `Decimal` числом (`services/excel.py`, докстрока). В JS деньги не
  перемножаются и не делятся (спека 4а §2.9) — ни для «Договорились», ни для
  предпросмотра.
- **Ось сравнения — нетто.** Порядок — нетто → приведение → ставка показа
  (`AGENTS.md` §10). Файл — в базе НДС КП независимо от переключателя (§2.8).
- **Предикат цены «конечно и больше нуля»** — `money.price.is_price` /
  `is_weight`; голое `> 0` запрещено (пропускает `NaN`). В CHECK миграции —
  явно `<> 'NaN'` и `<> 'Infinity'` (R9 4а).
- **Чтение не пишет.** Три `GET` 4а и `GET …/export-preview`, `…/sent-files`,
  `…/sent-files/{id}/file` — ни одной записи (тест счётчиков транзакции, как в
  4а). Пишут только мутации решений (`admin`) и `POST …/export` (`member`).
- **Права** (§2.15): правка решений — `require_admin`; выгрузка, список,
  скачивание — `get_current_user`. Сторож `tests/test_auth_coverage.py`
  (ДО 167) зелёный после каждой задачи с маршрутами.
- **Подрядчику — только цель и источник** (§2.7): ни `range.max`, ни
  `range.median`, ни число договоров, ни ступень не попадают ни в комментарий,
  ни в столбцы файла.
- **Ручная ступень действует везде** (решение пользователя 1, §преамбула
  спеки): этап, разбор, панель, предпросмотр, файл считают одну сцену с
  сохранёнными решениями.
- **Файлы** (`AGENTS.md` §8 после ревизии задачи 14): только через `Storage`,
  ключ — непрозрачный, имя — в БД, выдача — авторизованным `GET`; файл,
  сохранённый до неудачного коммита, удаляется.
- **Не трогаются:** `v_position_deviation_inputs`, `v_category_totals`, свод
  по этапам, сравнение договоров (кроме подъёма `_write_inflation_block` в
  общий модуль — решение 9), матрица, паспорт, каталог и очередь. Ключи
  существующих ответов не меняются — только добавляются (решение 6).
- **Фронтенд** — только shadcn/ui; новых примитивов не нужно (`textarea`,
  `input`, `dialog`, `badge`, `button`, `sheet` уже стоят).
- **Репозиторий публичный:** настоящие объекты, подрядчики, номера договоров
  и цены стенда — ни в тесты, ни в devlog; условные имена («Объект Т»,
  «Подрядчик А»).
- **Стенд `gca_dev`** — только чтение; запись в учётки стенда запрещена;
  кириллицу в терминал не печатать; `psql` — `| tr -d '\r'`.
- **Номер новой версии `AGENTS.md` в плане и спеке не называется** — страж
  «каждое упоминание версии разрешается» покраснел бы до задачи 14
  (`docs/pitfalls/process.md`).

## Review Focus

Входы, которые спека подразумевает, а задачи легко пропустить; тест на каждый
стоит в задаче-владельце:

1. **Ручная ступень строки, чья семья в КП — система, а запись сделана, пока
   строка была работой** (смена `semantic_kind`): `kind_changed`, а ступень
   семьи, если есть, действует; экран не падает 500 (Task 2, Task 6).
2. **Две записи на одну семью из разных смет** (КП двух участников одной
   семьи): ступень семьи одного КП не действует на другой — ключ
   `(estimate_id, work_family_id)` (Task 2, Task 6).
3. **Комментарий с `-` или `=` в начале** (`safe_str`): в ячейке текст
   сохраняется, в предпросмотре — тот же текст, что в ячейке (решение 8:
   предпросмотр отдаёт значение ячейки) (Task 5).
4. **Выгрузка, когда предпросмотр показан с рядом ИПЦ, а ряд за это время
   получил дыру** — `POST …/export` с рядом отвечает 422 тем же кодом, файл не
   сохраняется, записи нет (Task 8).
5. **Удаление участника, у которого есть отправленный файл, но нет сметы**
   (смета ушла заменой раунда): ключ файла собирается по офферу, а не по смете,
   файл удаляется с диска (Task 9).

## Структура файлов

```
backend/
  alembic/versions/2026_10_27_0023-kp_decisions.py   создаётся: пять таблиц §2.13 (Task 1)
  models.py                         правка: KpLineAgreement, KpLineComment, KpLineRung, KpFamilyRung, KpSentFile (Task 1)
  services/our_price.py             правка: ContractRate.nominal/lines, сохранённые ступени, статус ручной ступени, selectable (Task 2)
  services/kp_comment.py            создаётся: случаи и тексты шаблона (Task 3)
  services/kp_file.py               создаётся: строки файла, отклонение, источник, шапка, годы ИПЦ (Task 4)
  services/excel.py                 правка: подписи состава/уровня и блок ИПЦ подняты сюда (Task 5)
  services/excel_changes_export.py, services/excel_comparison.py   правка: импортируют поднятое (Task 5)
  services/excel_kp_file.py         создаётся: книга из строк файла (Task 5)
  crud/kp_decisions.py              создаётся: загрузка решений, тексты единиц, главы КП, запись решений, счёт решений (Task 6, 7, 9)
  crud/our_price.py                 правка: сцена с решениями, поля строки и панели (Task 6); сборка строк файла (Task 8)
  crud/kp_sent_files.py             создаётся: выгрузка с хранением, список, выдача (Task 8)
  crud/tenders.py                   правка: ключи отправленных при удалении, состав токена (Task 9)
  services/round_import.py          правка: предупреждение о решениях (Task 9)
  routers/tenders.py                правка: мутации, предпросмотр, выгрузка, список, файл; счёт в replace_required; хранилище в delete_participant (Task 7, 8, 9)
  tests/conftest.py                 правка: _DOMAIN_TABLES (Task 1)
  tests/unit/test_kp_file_rungs.py, test_kp_file_comment.py, test_kp_file_rows.py, test_kp_file_xlsx.py   создаются
  tests/integration/test_kp_file_schema.py, test_kp_file_scene.py, test_kp_file_mutations.py,
                    test_kp_file_export.py, test_kp_file_lifecycle.py                                  создаются
frontend/src/
  types/domain.ts, services/api/domain.ts, services/queryKeys.ts, services/queries.ts   правка (Task 10)
  services/queries.kpFile.test.tsx                                                     создаётся (Task 10)
  test/handlers.ts, test/fixtures.ts                                                   правка (Task 10)
  pages/tenders/our-price/
    ourPriceCopy.ts                 правка: тексты 4б (Task 11–13)
    ExportBlock.tsx, RungSaveBar.tsx                     создаются (Task 11)
    LineSheet.tsx, OfferLinesTable.tsx                   правка (Task 11)
    KpFilePage.tsx, KpFileTable.tsx, SentFilesList.tsx   создаются (Task 12)
    OurPriceHeader.tsx, OurPriceOfferPage.tsx, ParticipantCard.tsx   правка (Task 13)
    VatBaseDialog.tsx               создаётся (Task 13)
    *.test.tsx рядом                создаются / правятся
  App.tsx                           правка: маршрут предпросмотра (Task 12)
AGENTS.md, docs/AGENTS-revisions.md       правка: ревизия §3, §8 (Task 14)
docs/reference/schema.md, screens.md, money-axes.md   правка (Task 14)
docs/devlog/2026-10-27-contractor-file.md  создаётся; docs/product-roadmap.md правка (Task 15)
```

## Решения плана, которых нет в спеке

1. **Порядок задач:** схема (1) → чистое ядро: ступени (2), комментарий (3),
   строки файла (4), книга (5) → SQL и сцена (6) → мутации (7) → выгрузка и
   хранение (8) → жизненный цикл: замена и удаления (9) → данные фронта (10) →
   панель (11) → предпросмотр (12) → шапка, база НДС, карточка (13) →
   ревизия и справочник (14) → стенд и финал (15).
2. **Модули.** Решения по строкам — новый `crud/kp_decisions.py` (чтение для
   сцены и запись мутаций в одном месте, как `crud/stage_summary.py` для
   разноса); отправленные файлы — `crud/kp_sent_files.py`; чистые
   `services/kp_comment.py`, `services/kp_file.py`, `services/excel_kp_file.py`
   не знают о `db` (образец `services/changes_export.py` ↔
   `excel_changes_export.py`). Префикс `kp_` — у всего, что про КП в 4б.
3. **Сохранённые ступени в ядре — отдельный параметр, а не замена
   `RungChoice`.** `evaluate_offer(..., choice=None, saved=None)`: `saved`
   (`SavedRungs`) — решения из базы, `choice` (как в 4а) — предпросмотр панели,
   и он **побеждает** сохранённое на своей строке или семье. Ветки
   `ValueError` 4а остаются только для `choice` (его проверяет эндпоинт);
   для `saved` недоступная ступень — статус «не действует» с причиной, а не
   исключение.
4. **Снимок ключа ступени работы** — JSON того, что отдаёт
   `LineKey.at(level)`: `{"level":"v","variant_id":N}`,
   `{"level":"p","family_id":F,"parameter_id":P,"value_id":V|null}`,
   `{"level":"f","family_id":F}`. Перевод JSON ↔ `Hashable` — в
   `crud/kp_decisions.py` одной парой функций; ядро сравнивает `Hashable` с
   `key.at(level)`.
5. **Проверка `PUT …/rung`** считает сцену **без ряда ИПЦ и с сохранёнными
   ступенями других строк**: число договоров на ступени от приведения не
   зависит (множитель > 0 и `is_price` сохраняется), а `selectable` системы —
   свойство семьи этого КП. Сохранение идёт, только если ступень в
   `selectable` строки; иначе `unknown_rung` (нет в `rungs`), `rung_empty`
   (есть, но не `selectable`), `no_ladder` (`rungs` пуст).
6. **Дополнения к форме ответа** — ровно §2.11; ключи 4а не меняются.
   `rungs[].selectable` у работы — `contracts >= 1`; у системы — у каждой
   группы семьи на этой ступени ≥ 1 договора. `manual_rung.target` — `"line"`
   или `"family"`. В строке разбора — `agreed`, `comment_kind`, `manual_rung`;
   в `line` панели — они же плюс `comment: {kind, text, template_text}` на
   верхнем уровне ответа панели.
7. **Даты в файле и в источнике «Договорились {ДД.ММ.ГГГГ}»** — дата
   `set_at` в часовом поясе приложения той же функцией, что печатает «правлен
   {дата}» в блоке ИПЦ (`_iso_date_to_ru` поднимается вместе с блоком,
   решение 9); отдельного правила дат не заводится.
8. **Предпросмотр отдаёт значения ячеек, а не сырьё:** строки предпросмотра —
   те же объекты, из которых `excel_kp_file` пишет ячейки, тексты — уже после
   `safe_str`. Тест «предпросмотр = файл» сравнивает ячейку с полем строки
   один в один.
9. **Подъём общего в `services/excel.py`:** `TAX_BASIS_CAPTION`,
   `PRICE_LEVEL_NOMINAL_CAPTION`, `write_caption`, `write_inflation_block`,
   `month_label`, `iso_date_to_ru` — без подчёркивания, со старыми
   модулями-потребителями, импортирующими их; поведение «Изменений КП» и
   выгрузки сравнения не меняется ни одним символом в ячейке (их тесты зелёные
   без правки ожиданий).
10. **Имя файла — одно на сервере.** Предпросмотр несёт `header.filename`;
    фронт сохраняет blob под ним (CORS заголовки не отдаёт), сервер пишет то же
    имя в `kp_sent_files.filename`. Тест: имя в предпросмотре равно имени
    созданной записи.
11. **`POST …/export` отвечает байтами файла и пишет запись в той же
    транзакции роутера:** `storage.save` → `INSERT kp_sent_files` → `commit` →
    ответ; любая ошибка после `save` — `rollback` и `storage.delete(key)`.
    Идентификатор записи наружу не нужен: фронт инвалидирует список.
12. **Транзакции мутаций решений — на роутере** по образцу `_round_override`
    (`routers/tenders.py:444-478`): `commit` на успехе, `rollback` на отказе.
    Конкурентные правки одной строки — «последний пишет» (`INSERT … ON
    CONFLICT (pk) DO UPDATE`); блокировок не заводится: решение одного
    человека над одной строкой, гонке нечего портить, кроме самого значения.
13. **Предупреждение о замене** пишется в `replace_round_estimates` тем же
    списком `warnings`, что ручной разнос; счёт решений для отказа
    `replace_required` — функцией `count_round_decisions(db, round_id)` из
    `crud/kp_decisions.py`, её же зовёт `replace_round_estimates`.

## Задачи

### Task 1: миграция `0023` и модели

**Files**
- Create: `backend/alembic/versions/2026_10_27_0023-kp_decisions.py`
- Edit: `backend/models.py`, `backend/tests/conftest.py` (`_DOMAIN_TABLES`)
- Test: `backend/tests/integration/test_kp_file_schema.py`

**Interfaces**
- Потребляет: `PositionItem`, `Estimate`, `Offer`, `WorkFamily`, `User` (существуют).
- Производит:

```python
class KpLineAgreement(Base):   # __tablename__ = "kp_line_agreements"
    position_item_id: int      # PK, FK position_items ON DELETE CASCADE
    net_price: Decimal         # Numeric, NOT NULL
    set_by: int                # FK users RESTRICT
    set_at: datetime           # timestamptz, server_default now()

class KpLineComment(Base):     # "kp_line_comments": position_item_id PK, text, set_by, set_at
class KpLineRung(Base):        # "kp_line_rungs": position_item_id PK, level, scope, key (JSONB), set_by, set_at
class KpFamilyRung(Base):      # "kp_family_rungs": PK (estimate_id, work_family_id), level, scope,
                               #   first_main_parameter_id (BigInteger NULL, без FK), set_by, set_at
class KpSentFile(Base):        # "kp_sent_files": id, offer_id FK offers CASCADE, file_key UNIQUE,
                               #   filename, file_sha256, size_bytes, vat_rate (Numeric),
                               #   level_label, created_by FK users RESTRICT, created_at

KP_RUNG_LEVELS = ("v", "p", "f"); KP_RUNG_SCOPES = ("cls", "all"); KP_COMMENT_MAX = 2000
```

**Утверждения**
- `alembic upgrade head` создаёт пять таблиц с именами ограничений
  `pk_/fk_/uq_/ck_<таблица>_…`; `alembic check` (`just db-test-check`) не
  видит дрейфа моделей;
- `kp_line_agreements.net_price` отвергает `0`, `-1`, `'NaN'`, `'Infinity'` и
  принимает `0.0000001`;
- `kp_line_comments.text` принимает пустую строку и строку из 2 000 символов,
  отвергает 2 001;
- `level` вне `v/p/f` и `scope` вне `cls/all` отвергаются в обеих таблицах
  ступеней;
- `kp_family_rungs`: строка `level='p'` без `first_main_parameter_id` и строка
  `level='f'` с ним — обе отвергнуты (равенство §2.13 тотально; входы
  «заполнено только зависимое поле» и «пусто при `p`»);
- каскады: удаление строки КП уносит её договорённость, комментарий и ступень;
  удаление сметы — ступени семей; удаление оффера — отправленные файлы;
  удаление пользователя с решением отказывает (`RESTRICT`);
- паритет CHECK модели и миграции — тестом `TestParityWithMigration` по
  образцу `test_catalog_parameters_schema.py` (литералы миграции — константами
  внутри неё, не импортом `models.py`);
- `downgrade` на пустых таблицах снимает все пять; при строке хотя бы в одной —
  `RuntimeError`, называющий таблицу и счёт (`_downgrade_blockers`, образец
  0022); по входу на каждую таблицу.

**Имена**
- Заводятся: пять классов и таблиц выше, `KP_RUNG_LEVELS`, `KP_RUNG_SCOPES`, `KP_COMMENT_MAX`, ревизия `0023`.
- Существуют: `_DOMAIN_TABLES`, `_downgrade_blockers` (в 0022 — как образец).

**Проверка**
- `just test-int-local-k test_kp_file` — ДО 0, ПОСЛЕ ≥ 20.
- `just db-test-check` — зелёная.

### Task 2: ядро — номинал ставки, сохранённые ступени, статус ручной ступени

**Files**
- Edit: `backend/services/our_price.py`
- Test: `backend/tests/unit/test_kp_file_rungs.py`

**Interfaces**
- Потребляет: `LineKey`, `Rung`, `RUNG_ORDER`, `available_rungs`, `evaluate_offer`, `HistoryIndex` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class ContractRate:
    contract_id: int
    net: Decimal          # как в 4а: после приведения, если план передан
    nominal: Decimal      # без множителя ИПЦ
    lines: int            # число строк договора, сложивших ставку по ключу

ResetReason = Literal["kind_changed", "key_changed", "rung_unavailable", "rung_empty"]

@dataclass(frozen=True)
class SavedWorkRung:
    rung: Rung
    key_snapshot: Hashable          # значение LineKey.at(rung.level) на момент выбора

@dataclass(frozen=True)
class SavedFamilyRung:
    rung: Rung
    first_main_parameter_id: int | None

@dataclass(frozen=True)
class SavedRungs:
    work: Mapping[int, SavedWorkRung]        # position_item_id -> ...
    family: Mapping[int, SavedFamilyRung]    # family_id -> ... (внутри одного КП)

@dataclass(frozen=True)
class ManualRung:
    rung: Rung
    target: Literal["line", "family"]
    active: bool
    reset_reason: ResetReason | None

# LineResult получает поля:
#   manual: ManualRung | None
#   selectable: frozenset[Rung]

def evaluate_offer(lines, history, *, kp_vat_base, tender_area, tender_class_id,
                   choice: RungChoice | None = None,
                   saved: SavedRungs | None = None) -> OfferEvaluation
```

**Утверждения**
- без `saved` результат `evaluate_offer` равен результату 4а поле в поле
  (новые поля: `manual = None`, `selectable` — по правилу ниже); все тесты
  `-k test_our_price` в `tests/unit` зелёные без правки ожиданий;
- `ContractRate.nominal` при плане ИПЦ с множителем `1.1` — ставка до
  множителя, `net = nominal × 1.1`; без плана `nominal == net`; `lines` —
  число строк договора по ключу (1 и 3 на двух договорах фикстуры);
- работа: четыре условия §2.5 табличным тестом — по одной строке на причину в
  порядке `kind_changed` → `key_changed` → `rung_unavailable` → `rung_empty`;
  действующая ступень меняет `rung`, `ours`, `to_ours`, `bargain`, `auto =
  False`, а итоги `offer_totals` следуют за строкой;
- вход «вариант сменился в той же семье при сохранённой `f·cls`» — ступень
  действует (`active = True`); тот же вход при сохранённой `v·cls` —
  `key_changed`, строка на авто;
- недействующая ступень не роняет расчёт: ни одного исключения, `rung` —
  авто, `manual.active = False`, `reset_reason` задан;
- строка `in_progress` / `unknown_vat_base` / `no_price` с сохранённой
  ступенью — состояние то же, что без неё; `manual` заполнен, проверка смысла
  идёт по ключу (если ключ есть);
- система: ступень семьи действует на все системные строки семьи этого КП;
  `p` при строке без `first_main` — `rung_unavailable`; `p` при другом
  `parameter_id` первого главного — `key_changed`; группа без договоров на
  ступени — `rung_empty`; запись на семью без системных строк в КП — ничего
  не меняет и не падает;
- работа с сохранённой ступенью строки, ставшая системой, — `manual.target =
  "line"`, `kind_changed`; ступень её семьи (если сохранена) действует
  (Review Focus 1);
- `choice` побеждает `saved` на своей строке/семье: предпросмотр `v·all` при
  сохранённой `f·cls` даёт `v·all`;
- `selectable`: у работы — ступени из `available_rungs` с ≥ 1 договором; у
  системы — ступени, где у **каждой** группы семьи ≥ 1 договор; у строки без
  лестницы — пусто;
- снятие проверки ключа (`key_changed`) краснит вход «точный вариант
  сменился»; снятие `rung_empty` — вход «ступень опустела» даёт `no_history`
  вместо авто.

**Имена**
- Заводятся: `SavedWorkRung`, `SavedFamilyRung`, `SavedRungs`, `ManualRung`, `ResetReason`, поля `ContractRate.nominal`, `ContractRate.lines`, `LineResult.manual`, `LineResult.selectable`, параметр `saved`.
- Существуют: `ContractRate`, `LineResult`, `RungChoice`, `evaluate_offer`, `available_rungs`, `offer_totals` (проверено `grep` по `services/our_price.py`).

**Проверка**
- `uv run pytest tests/unit -q -k test_kp_file` — ДО 0, ПОСЛЕ ≥ 25.
- `uv run pytest tests/unit -q -k test_our_price` — ДО 225, ПОСЛЕ 225 (зелёные).

### Task 3: шаблонный комментарий

**Files**
- Create: `backend/services/kp_comment.py`
- Test: `backend/tests/unit/test_kp_file_comment.py`

**Interfaces**
- Потребляет: Task 2 (`LineResult`, `ContractRate.nominal/lines`, `HistoryContract`), `quantize_money`, `is_price`, `is_weight` (существуют).
- Производит:

```python
CommentCase = Literal["agreed", "kp_unit", "history_unit", "below", "over"]

@dataclass(frozen=True)
class CommentInputs:
    result: LineResult
    is_system: bool
    unit_text: str                       # единица строки КП (Task 4 выбирает текст)
    kp_unit_net: Decimal | None          # unit_cost_total в нетто
    kp_total_net: Decimal | None
    quantity: Decimal | None             # вес строки
    agreed_net: Decimal | None
    level: Level | None                  # уровень действующей ступени
    source: "SourceContract | None"
    tender_contractor_id: int
    tender_area: Decimal | None
    inflation: "InflationLabel | None"   # имя ряда и ММ.ГГГГ; None — без ряда

@dataclass(frozen=True)
class SourceContract:
    contract: HistoryContract
    rate: ContractRate                   # net (цель), nominal (цитата), lines

@dataclass(frozen=True)
class InflationLabel:
    series_name: str
    month: str                           # "ММ.ГГГГ"

def pick_source(result: LineResult, history: HistoryIndex, *, tender_class_id: int,
                tender_contractor_id: int) -> SourceContract | None
def target_net(inp: CommentInputs) -> Decimal | None          # цель за единицу (§2.2)
def target_sum_net(inp: CommentInputs) -> Decimal | None      # целевая сумма строки системы
def comment_case(inp: CommentInputs) -> CommentCase | None
def template_text(inp: CommentInputs) -> str | None
def format_rub(value: Decimal) -> str                         # "34 154,00"
```

**Утверждения**
- `comment_case` — табличный тест по семи строкам §2.7 в их порядке; равенство
  в копейках (`34 154,004` против `34 154,00`) — нет комментария; `34 154,01`
  — `over`;
- система без договорённости решается **только сравнением сумм в копейках**:
  `comparable` с объёмом `NaN` и КП выше цели — `over`; **контрпример
  ревью** (круг 1 гейта 3) — объём 100, сумма КП 100,40, целевая сумма 100,00
  — `over`, хотя за единицу (1,004 и 1,00) в копейках равенство; сумма КП
  100,004 при той же цели — нет комментария; снятие «суммой» (сравнение за
  единицу) краснит контрпример, а строка без объёма теряет случай;
- `agreed` требует годной цены КП за единицу: при `unit_cost_total = None` и
  договорённости — `None` (нет комментария);
- три формы названия цифр системы (таблица §2.7) — по тексту на форму; в
  форме «объём не годен» подстроки «за единицу» и «за {ед}» нет нигде;
- тексты `over` работы на уровнях `v` / `p` / `f`: «эта работа стоила» /
  «сопоставимые работы стоили» (две формы), «в среднем» — при `lines ≥ 2`, без
  него — при `lines = 1`; «с вами» — при подрядчике договора = подрядчике
  участника;
- цитата — `rate.nominal`, цель — `rate.net` (с рядом); хвост «с учётом
  инфляции ({ряд}, к {ММ.ГГГГ})» есть при `inflation` и множителе ≠ 1, нет без
  ряда и при множителе 1;
- **запрет вилки**: на строке с вилкой `100 / 150 / 400` (min / median / max) и
  целью 100 ни `150`, ни `400` ни в каком формате (`150`, `150,00`,
  `400,00`) в тексте нет;
- текст не зависит от НДС: у `template_text` нет параметра ставки показа
  (проверка сигнатуры), во всех текстах — «без НДС»;
- `pick_source` — договор с минимальной ставкой среди класса тендера на
  ступени (или всех при `ours_from_all_classes`); при ничьей ставок — договор
  этого подрядчика, затем поздний год, затем меньший `contract_id`;
- `format_rub` — два знака, разряды пробелом, запятая (`Decimal("1228314.5")`
  → `1 228 314,50`), без `float`.

**Имена**
- Заводятся: все имена блока выше.
- Существуют: `LineResult`, `HistoryIndex`, `HistoryContract`, `ContractRate`, `quantize_money`, `is_price`, `is_weight`.

**Проверка**
- `uv run pytest tests/unit -q -k test_kp_file` — ДО ≥ 25, ПОСЛЕ ≥ 50.

### Task 4: строки файла

**Files**
- Create: `backend/services/kp_file.py`
- Test: `backend/tests/unit/test_kp_file_rows.py`

**Interfaces**
- Потребляет: Task 2, Task 3; `net_to_gross`, `safe_str` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class KpSourceLine:                 # вход: строка сметы КП в порядке файла
    position_item_id: int
    is_chapter: bool
    number: str | None              # item_number_in_proposal
    title: str
    unit_text: str                  # §2.8: raw → units_of_measure → ""
    quantity: Decimal | None        # вес
    unit_price: Decimal | None      # как в КП (валовая в базе КП)
    total: Decimal | None

@dataclass(frozen=True)
class FileSection:
    number: str | None
    title: str

@dataclass(frozen=True)
class FileLine:
    position_item_id: int
    number: str | None
    title: str
    unit: str
    quantity: Decimal | None
    kp_unit_price: Decimal | None
    kp_total: Decimal | None
    ours: Decimal | None            # в базе НДС КП
    deviation_pct: Decimal | None   # один знак
    source: str | None
    inflation: str | None
    comment: str | None
    comment_kind: Literal["template", "custom"] | None
    state: str

@dataclass(frozen=True)
class FileHeader:
    tender: str; object: str; round: str; contractor: str; exported_on: str
    vat_label: str; source_label: str; level_label: str
    in_progress_lines: int
    inflation_years: list[dict] | None   # год, коэффициент, источник, is_forecast, updated_at
    filename: str

def build_file_rows(source_lines: Sequence[KpSourceLine], evaluation: OfferEvaluation,
                    *, comments: Mapping[int, str], agreements: Mapping[int, tuple[Decimal, date]],
                    context: "FileContext") -> list[FileSection | FileLine]
def deviation_pct(line: FileLineInputs) -> Decimal | None
def inflation_years_for(sources: Iterable[SourceContract], plan: InflationPlan,
                        periods: Mapping[int, YearMonth]) -> list[dict]
def build_header(context: "FileContext", rows: Sequence[FileSection | FileLine]) -> FileHeader
```

`FileContext` — датакласс с тем, что строкам нужно от сцены: история, класс и
площадь тендера, подрядчик участника, база НДС КП, `InflationLabel | None`,
план ИПЦ, названия тендера/объекта/этапа/участника, дата выгрузки.
`FileLineInputs` — внутренний датакласс отклонения (имя и состав — на
исполнителе, тестируется через `deviation_pct`).

**Утверждения**
- порядок строк — порядок входа; глава — `FileSection`, позиция — `FileLine`;
  строк «Дополнительные работы» и итогов нет (их нет на входе — тест входа
  «только позиции и главы»);
- «Наша расценка» = `net_to_gross(цель, база КП)`: КП 22 %, цель 100 — `122`;
  без цели — пусто; у `unit_mismatch` без договорённости — пусто, у
  `no_history`, `no_variant`, `in_progress`, `no_price` — пусто;
  «Договорились» — заполнено в любом состоянии;
- отклонение по таблице приоритета §2.8 — четыре строки; **случай ревью**:
  система, объём 2, КП 60 000 за ед. (сумма 120 000), целевая сумма 80 000,
  договорились 50 000 → расценка `50000` (в нетто; в базе 0 %), отклонение
  `20.0`; система с договорённостью и объёмом `NaN` — от цены за единицу;
  договорённость при `unit_price = None` — пусто; знак отклонения и случай
  комментария согласованы на каждой строке таблицы;
- система без договорённости: отклонение по суммам при любом объёме;
  контрпример Task 3 (объём 100, 100,40 против 100,00) — `0.4`, знак
  согласован со случаем `over`;
- «Источник»: «Договорились {ДД.ММ.ГГГГ}» при договорённости; иначе объект,
  номер договора, год, цитата `nominal` и «без НДС»; «в среднем» по правилу
  §2.7; у системы — «на м² объекта», а при негодном объёме — хвост «— для
  вашего объекта {сумма} ₽ за весь объём»;
- «Приведение по ИПЦ» — только при ряде; пусто при договорённости и при
  множителе 1; у системы без объёма — суммой;
- «Комментарий»: свой текст побеждает шаблон; пустой свой текст — пустая
  ячейка и `comment_kind = "custom"`; без своего — `template_text` Task 3;
- `inflation_years_for` — годы договоров-источников файла: два источника с
  периодами 2024-03 и 2025-06 при цели 2026-03 — годы обоих; год договора,
  который не стал источником ни одной строки, не печатается; пустой набор —
  `[]`; без ряда шапка несёт `inflation_years = None`;
- шапка: `vat_label` — «Цены КП и наши расценки — с НДС 22 % (база НДС этого
  КП)» либо «— без НДС (база НДС этого КП — 0 %)»; `level_label` —
  «номинальный, без приведения» либо «приведено по ИПЦ: {ряд}, к {ММ.ГГГГ}; КП
  не приводится»; `in_progress_lines` — число строк `in_progress`;
  `filename` — «КП {подрядчик} этап {N} — наши расценки {ГГГГ-ММ-ДД}.xlsx»
  через `safe_filename_part`;
- в строках файла нет ни `range.max`, ни `range.median` (тест подстрокой по
  всем текстовым полям на фикстуре с вилкой).

**Имена**
- Заводятся: все имена блока выше, `FileContext`.
- Существуют: `net_to_gross`, `safe_filename_part`, `InflationPlan`, `YearMonth`, `year_exponents`, имена Task 2–3.

**Проверка**
- `uv run pytest tests/unit -q -k test_kp_file` — ДО ≥ 50, ПОСЛЕ ≥ 75.

### Task 5: книга xlsx и подъём общих подписей

**Files**
- Create: `backend/services/excel_kp_file.py`
- Edit: `backend/services/excel.py`, `backend/services/excel_changes_export.py`, `backend/services/excel_comparison.py`
- Test: `backend/tests/unit/test_kp_file_xlsx.py`

**Interfaces**
- Потребляет: Task 4 (`FileHeader`, `FileSection`, `FileLine`).
- Производит:

```python
# services/excel.py (подняты из приватных, решение 9)
TAX_BASIS_CAPTION: dict[str, str]
PRICE_LEVEL_NOMINAL_CAPTION: str
def write_caption(ws, row: int, text: str, *, wrap: bool = False) -> int
def write_inflation_block(ws, row: int, inflation: dict) -> int
def month_label(target_month: str) -> str
def iso_date_to_ru(value: str) -> str

# services/excel_kp_file.py
KP_FILE_COLUMNS: list[tuple[str, int, str | None, str]]
def build_kp_file(header: FileHeader, rows: Sequence[FileSection | FileLine]) -> bytes
```

**Утверждения**
- значения ячеек книги построчно равны полям `FileLine` / `FileSection` (тест
  читает книгу `openpyxl.load_workbook` и сравнивает с входом; деньги — тип
  `Decimal`-совместимый, не строка; решение 8);
- шапка листа содержит `vat_label`, `source_label`, `level_label`; при
  `inflation_years` — блок «Цены договоров приведены к: … · ряд: «…»» и по
  строке на год с коэффициентом, источником, «факт/прогноз», «правлен {дата}»;
  при `[]` — фраза «Коэффициенты за годы не потребовались…»; при `None` блока
  нет;
- столбец «Приведение по ИПЦ» есть только при ряде (`inflation_years is not
  None`);
- комментарий `=СУММ(A1)` и `-скидка` в ячейке — с апострофом `safe_str`, и
  предпросмотр несёт то же значение (Review Focus 3);
- наши столбцы выделены заливкой, разделы — жирные строки на ширину листа;
- «Изменения КП» и выгрузка сравнения после подъёма — их тесты зелёные без
  правки ожиданий (решение 9).

**Имена**
- Заводятся: `build_kp_file`, `KP_FILE_COLUMNS`, публичные имена подъёма.
- Существуют: `_TAX_BASIS_CAPTION`, `_PRICE_LEVEL_CAPTION`, `_write_caption` (`excel_changes_export.py:119-124, :283`), `_write_inflation_block`, `_month_label`, `_iso_date_to_ru` (`excel_comparison.py:223-276`) — заменяются импортом.

**Проверка**
- `uv run pytest tests/unit -q -k test_kp_file` — ДО ≥ 75, ПОСЛЕ ≥ 85.
- `uv run pytest tests -q -k "changes_export or comparison_export or excel"` — ДО и ПОСЛЕ одно число, зелёные (исполнитель снимает ДО до правки).

### Task 6: сцена с решениями — этап, разбор, панель

**Files**
- Create: `backend/crud/kp_decisions.py`
- Edit: `backend/crud/our_price.py`
- Test: `backend/tests/integration/test_kp_file_scene.py`

**Interfaces**
- Потребляет: Task 1, Task 2, Task 3; `_build_scene`, `_evaluate`, `_line_view`, `build_line_view` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class EstimateDecisions:
    agreements: dict[int, tuple[Decimal, datetime, int, str]]   # pid -> (net, set_at, user_id, email)
    comments: dict[int, tuple[str, datetime, int, str]]
    saved: SavedRungs
    rung_meta: dict[tuple[str, int], tuple[datetime, int, str]]  # ("line", pid) | ("family", fid) -> аудит

def load_decisions(db: Session, estimate_ids: Sequence[int]) -> dict[int, EstimateDecisions]
def snapshot_to_json(level: Level, key_value: Hashable) -> dict
def snapshot_from_json(data: dict) -> Hashable
def load_unit_texts(db: Session, estimate_id: int) -> dict[int, str]   # pid -> текст единицы §2.8
```

**Утверждения**
- `_build_scene` загружает решения **одним** вызовом `load_decisions` на все
  сметы сцены; `_evaluate` передаёт `saved` своей сметы; число SQL-операторов
  на этап с 2 и с 20 строками и с решениями — одинаковое (сторож решения 2
  плана 4а);
- сохранённая ступень работы меняет строку разбора, `participants[].totals`
  экрана этапа и ячейку статьи этого участника **согласованно**: итоги
  участника на этапе равны итогам разбора (инвариант 4а); снятие загрузки
  решений из сцены краснит этот тест;
- две сметы одного этапа со ступенью одной семьи у одного КП — у второго КП
  ступень не действует (Review Focus 2);
- строка разбора несёт `agreed` (`net`, `shown` — по ставке КП, `null` при
  неизвестной базе; `set_by: {id, email}`, `set_at`), `comment_kind`
  (`"template"` / `"custom"` / `null`) и `manual_rung` (§2.11); у строки без
  решений — `null`, `"template"` либо `null` по случаю, `null`;
- `rung.auto = False` при действующей ручной; `rungs[].selectable` — по
  решению 6;
- панель отдаёт `comment: {kind, text, template_text}`: свой текст — `kind
  "custom"`, `template_text` — шаблон случая (или `null`); текст в строки
  разбора не кладётся;
- «Договорились» не меняет итогов: итоги разбора с договорённостью и без неё
  равны;
- недействующая ступень — 200, `manual_rung.active = false`, `reset_reason`
  задан; эндпоинты чтения — ни одной записи (счётчики транзакции) при
  недействующей ступени;
- снимок ключа: `snapshot_to_json` / `snapshot_from_json` — взаимно обратны
  на трёх уровнях, `value_id = null` на `p` сохраняется;
- `load_unit_texts`: текст из `estimate_raw_data` по `(lot_key,
  position_key_in_proposal)`; нет текста — имя `units_of_measure`; нет и его —
  `""`;
- тесты 4а `-k test_our_price` (integration) зелёные; правки ожиданий —
  только добавленные ключи ответа.

**Имена**
- Заводятся: все имена блока выше.
- Существуют: `_build_scene`, `_evaluate`, `_line_view`, `build_round_view`, `build_offer_view`, `build_line_view`, `EstimateRawData`, `UnitOfMeasure` (проверено `grep`).

**Проверка**
- `just test-int-local-k test_kp_file` — ДО ≥ 20, ПОСЛЕ ≥ 40.
- `just test-int-local-k test_our_price` — ДО 425, ПОСЛЕ 425 (зелёные).

### Task 7: мутации решений

**Files**
- Edit: `backend/crud/kp_decisions.py`, `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_kp_file_mutations.py`

**Interfaces**
- Потребляет: Task 1, Task 2, Task 6; `require_admin`, `DomainError`, `raise_domain_error`, `gross_to_net`, `is_price` (существуют).
- Производит:

```python
CODE_INVALID_AGREED = "invalid_agreed"
CODE_COMMENT_TOO_LONG = "comment_too_long"
CODE_RUNG_EMPTY = "rung_empty"
CODE_NO_LADDER = "no_ladder"
CODE_UNKNOWN_VAT_BASE = "unknown_vat_base"

class AgreedPut(BaseModel):  value: str; basis: Literal["net", "shown"]
class CommentPut(BaseModel): text: str
class RungPut(BaseModel):    level: str; scope: str

def put_agreed(db, tender_id, round_id, offer_id, pid, *, value: Decimal, basis: str, user_id: int) -> None
def delete_agreed(db, tender_id, round_id, offer_id, pid) -> None
def put_comment(db, tender_id, round_id, offer_id, pid, *, text: str, user_id: int) -> None
def delete_comment(db, tender_id, round_id, offer_id, pid) -> None
def put_rung(db, tender_id, round_id, offer_id, pid, *, level: str, scope: str, user_id: int) -> None
def delete_rung(db, tender_id, round_id, offer_id, pid) -> None

# маршруты: PUT|DELETE …/our-price/offers/{offer_id}/lines/{position_item_id}/{agreed|comment|rung}
```

**Утверждения**
- `member` — 403 на все шесть маршрутов; `admin` — 204; сторож
  `test_auth_coverage` — ДО 167, ПОСЛЕ 173;
- 404 на строку чужого КП, на главу, на оффер чужого этапа (как в 4а);
- «Договорились»: `{value: "122", basis: "shown"}` при базе 22 % хранит
  `100` (нетто без округления: `gross_to_net`); `"0"`, `"-1"`, `"NaN"`,
  `"abc"` — 422 `invalid_agreed`; число вместо строки в JSON — 422 (`float`
  отвергается); `basis: "shown"` при неизвестной базе — 422
  `unknown_vat_base`, `basis: "net"` — принимается;
- повторный `PUT` перезаписывает (решение 12), `DELETE` отсутствующего — 204;
- комментарий: 2 000 символов — 204, 2 001 — 422 `comment_too_long`; пустая
  строка — 204, и строка разбора несёт `comment_kind = "custom"`;
- ступень работы: `PUT` ступени из `selectable` — 204, запись со снимком ключа
  (решение 4); ступень вне `rungs` — 422 `unknown_rung`; в `rungs`, но не
  `selectable` — 422 `rung_empty`; строка без лестницы — 422 `no_ladder`;
  неполная пара и неизвестное значение — `unknown_rung` (как 4а);
- ступень системной строки пишет `kp_family_rungs` для `(смета, семья)` с
  `first_main_parameter_id` при `p`; `DELETE` у системной строки удаляет
  ступень семьи;
- проверка `PUT …/rung` идёт без ряда ИПЦ (решение 5): `selectable` в ответе
  панели и решение `PUT` совпадают на всех ступенях фикстуры (тест согласия);
- после `PUT …/rung` экран этапа и разбор считают на ручной ступени (одна
  интеграционная проверка «мутация → чтение»); договорённость этой строки
  после смены ступени — то же значение до последнего знака (DoD 3);
- отказ — `rollback`: после 422 в таблицах решений нет новых строк.

**Имена**
- Заводятся: имена блока выше, шесть маршрутов.
- Существуют: `require_admin`, `raise_domain_error`, `decimal_json`, `CODE_UNKNOWN_RUNG` (`crud/our_price.py:79`), `_rung_choice`.

**Проверка**
- `just test-int-local-k test_kp_file` — ДО ≥ 40, ПОСЛЕ ≥ 65.
- `uv run pytest tests/test_auth_coverage.py -q` — ДО 167, ПОСЛЕ 173.

### Task 8: предпросмотр, выгрузка, отправленные файлы

**Files**
- Create: `backend/crud/kp_sent_files.py`
- Edit: `backend/crud/our_price.py` (сборка `FileContext` и строк файла из сцены), `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_kp_file_export.py`

**Interfaces**
- Потребляет: Task 4, 5, 6; `Storage`, `get_storage`, `xlsx_response`, `ClosingStreamingResponse` (существуют).
- Производит:

```python
def build_file(db: Session, tender_id: int, round_id: int, offer_id: int, *,
               inflation_series_id: int | None) -> tuple[FileHeader, list[FileSection | FileLine]]
def preview_view(header: FileHeader, rows: Sequence[FileSection | FileLine]) -> dict
def export_file(db: Session, storage: Storage, tender_id: int, round_id: int, offer_id: int, *,
                inflation_series_id: int | None, user_id: int) -> tuple[bytes, str]
def list_sent_files(db: Session, tender_id: int, round_id: int, offer_id: int) -> list[dict]
def sent_file(db: Session, tender_id: int, round_id: int, offer_id: int, file_id: int) -> KpSentFile

# GET  …/our-price/offers/{offer_id}/export-preview?inflation_series_id=
# POST …/our-price/offers/{offer_id}/export?inflation_series_id=
# GET  …/our-price/offers/{offer_id}/sent-files
# GET  …/our-price/offers/{offer_id}/sent-files/{file_id}/file
```

**Утверждения**
- предпросмотр — форма §2.11 (`header` с `inflation_years` и `filename`,
  `columns`, `rows`); строки предпросмотра равны ячейкам файла `POST
  …/export` того же состояния (решение 8);
- КП с неизвестной базой — 422 `unknown_vat_base` у предпросмотра и
  выгрузки, запись не создаётся; участник без сметы — 404; три отказа ИПЦ —
  коды 4а; **выгрузка с рядом, получившим дыру**, — 422
  `missing_inflation_years`, файла в хранилище нет, записи нет (Review Focus 4);
- `POST …/export` — `member` 200, тело — xlsx, в `kp_sent_files` одна запись:
  `filename` равно `header.filename` предпросмотра (решение 10), `file_sha256`
  и `size_bytes` — от тела ответа, `vat_rate` — база КП, `level_label` — из
  шапки, `created_by` — пользователь;
- отказ вставки после `storage.save` (подменой) — `rollback`, файла по ключу в
  хранилище нет (решение 11);
- список — новые сверху, `created_by: {id, email}`; оффер чужого этапа — 404;
- скачивание — те же байты, что вернул `POST`; удалённый с диска файл — 410;
  чужой `file_id` — 404; `member` — 200;
- предпросмотр, список, скачивание — ни одной записи; сторож прав — ДО 173,
  ПОСЛЕ 177;
- в предпросмотре и ячейках нет `range.max` и `range.median` строки фикстуры с
  вилкой.

**Имена**
- Заводятся: имена блока выше, четыре маршрута.
- Существуют: `Storage`, `get_storage`, `xlsx_response`, `ClosingStreamingResponse` (`routers/import_jobs.py:40`), `plan_history_inflation`, `round_target_month`, `_offer_context`.

**Проверка**
- `just test-int-local-k test_kp_file` — ДО ≥ 65, ПОСЛЕ ≥ 85.
- `uv run pytest tests/test_auth_coverage.py -q` — ДО 173, ПОСЛЕ 177.

### Task 9: замена раунда и удаления

**Files**
- Edit: `backend/crud/kp_decisions.py`, `backend/services/round_import.py`, `backend/crud/tenders.py`, `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_kp_file_lifecycle.py`

**Interfaces**
- Потребляет: Task 1, Task 8; `replace_round_estimates`, `delete_round`, `delete_tender`, `delete_participant`, `_participant_composition`, `purge_files_best_effort` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class DecisionCounts:
    agreements: int
    comments: int
    rungs: int          # ступени строк + ступени семей

def count_round_decisions(db: Session, round_id: int) -> DecisionCounts
def count_package_decisions(db: Session, package_id: int) -> DecisionCounts
def sent_file_keys(db: Session, *, round_id: int | None = None, tender_id: int | None = None,
                   package_id: int | None = None) -> list[str]
```

**Утверждения**
- `replace_required` при решениях несёт `context = {agreements, comments,
  rungs}` и число в тексте; при нуле — текст прежний, контекста нет;
- после замены задание импорта несёт предупреждение «Заменой раунда удалены
  решения по строкам КП: договорённостей — N, комментариев — M, ручных
  ступеней — K.»; отправленные файлы и их файлы на диске на месте;
- `delete_round` и `delete_tender` возвращают ключи файлов импорта **и**
  отправленных; после коммита файлов нет на диске;
- `delete_participant` принимает хранилище; токен подтверждения меняется от
  числа решений и от числа отправленных файлов (`_participant_composition`:
  `decisions_count`, `sent_files_count`); после удаления файлы участника
  удалены с диска — **в том числе когда сметы у участника уже нет** (Review
  Focus 5);
- снятие ключей отправленных из `delete_tender` краснит тест «файла на диске
  нет».

**Имена**
- Заводятся: имена блока выше, ключи состава `decisions_count`, `sent_files_count`.
- Существуют: `replace_round_estimates` (`services/round_import.py:256`), `delete_round` (`crud/tenders.py:353`), `delete_tender` (`:376`), `_participant_composition` (`:411`), `delete_participant` (`:449`), `purge_files_best_effort` (`services/maintenance.py:170`).

**Проверка**
- `just test-int-local-k test_kp_file` — ДО ≥ 85, ПОСЛЕ ≥ 95.
- `just test-int-local-k "tenders or round_import or tender_award"` — зелёная; число ДО и ПОСЛЕ одно (снимает исполнитель до правки).

### Task 10: данные фронта

**Files**
- Edit: `frontend/src/types/domain.ts`, `frontend/src/services/api/domain.ts`, `frontend/src/services/queryKeys.ts`, `frontend/src/services/queries.ts`, `frontend/src/test/handlers.ts`, `frontend/src/test/fixtures.ts`
- Create: `frontend/src/services/queries.kpFile.test.tsx`

**Interfaces**
- Потребляет: формы ответов Task 6–8.
- Производит:

```ts
interface UserRef { id: number; email: string }      // новый: в types/domain.ts такого нет (grep)
type ResetReason = "kind_changed" | "key_changed" | "rung_unavailable" | "rung_empty";
interface ManualRung { level: RungLevel; scope: RungScope; active: boolean;
  reset_reason: ResetReason | null; target: "line" | "family"; set_by: UserRef; set_at: string }
interface AgreedValue { net: string; shown: string | null; set_by: UserRef; set_at: string }
// OurPriceLine: + agreed, comment_kind, manual_rung; rungs[]: + selectable
// OurPriceLineView: + comment: { kind; text; template_text }
interface KpFilePreview { header: KpFileHeader; columns: KpFileColumn[]; rows: KpFileRow[] }
interface KpSentFileItem { id; filename; created_at; created_by: UserRef; vat_rate; level_label; size_bytes }

qk.tenders.kpFilePreviewOfOffer(t, r, o)            // префикс
qk.tenders.kpFilePreview(t, r, o, series)
qk.tenders.kpSentFiles(t, r, o)

useKpFilePreview(t, r, o, series), useKpSentFiles(t, r, o)
usePutAgreed / useDeleteAgreed / usePutComment / useDeleteComment / usePutRung / useDeleteRung (t, r, o)
useKpFileExport(t, r, o)            // POST → saveBlob(header.filename)
useDownloadSentFile(t, r, o)        // образец useDownloadJobFile: 404 и 410 различимы
useSetOfferVatBase(t, r)            // PATCH /v1/estimates/{id}/vat {base_override}
```

**Утверждения**
- мутации решений инвалидируют ровно: `ourPriceLinesOfOffer(t,r,o)`,
  `ourPriceOffer` этого оффера (префиксом по ряду), `ourPriceRound(t,r,*)` и
  `kpFilePreviewOfOffer(t,r,o)`; **карточка тендера не перезапрашивается**
  (тест: счётчик запросов `GET /tenders/{id}` — 0); снятие ключа
  предпросмотра из инвалидации краснит тест «правка комментария → предпросмотр
  перезапрошен»;
- `useKpFileExport` сохраняет blob под `header.filename` предпросмотра и
  инвалидирует `kpSentFiles`; отказ — тостом с текстом сервера из blob
  (`toastReportError`);
- `useDownloadSentFile` — 410 и 404 дают разные тосты;
- `useSetOfferVatBase` шлёт только `{base_override}` строкой, инвалидирует
  ключи этапа и разборов раунда, не паспорт;
- тело `usePutAgreed` — `value` строкой, ни одного `Number(...)`;
- фикстуры несут форму провода: `variant_label` с семьёй, `quantity` не
  заглушка 1, `agreed.net` с 20+ знаками (урок 4а «вход теста по форме
  провода»).

**Имена**
- Заводятся: имена блока выше.
- Существуют: `qk.tenders.ourPriceLinesOfOffer`, `ourPriceOffer`, `ourPriceRound`, `saveBlob`, `safeFilenamePart`, `toastReportError`, `toastApiError`, `estimatesApi.setVat` (проверено `grep`).

**Проверка**
- `npx vitest run src/services` — ДО 145, ПОСЛЕ ≥ 160.

### Task 11: панель строки — «Для выгрузки» и сохранение ступени

**Files**
- Create: `frontend/src/pages/tenders/our-price/ExportBlock.tsx`, `RungSaveBar.tsx` (+ `.test.tsx`)
- Edit: `LineSheet.tsx`, `OfferLinesTable.tsx`, `ourPriceCopy.ts` (+ их тесты)

**Interfaces**
- Потребляет: Task 10.
- Производит: `ExportBlock({line, comment, canEdit, vatView})`, `RungSaveBar({line, previewRung, canEdit, onSaved})`.

**Утверждения**
- «Для выгрузки» — последний раздел панели: наша расценка справкой с
  подписью «считается из данных и следует за ними»; «Договорились, {ед}, {без
  НДС | с НДС X %}» с заглушкой «не договаривались» и подсказкой «вписываете
  вы, GCA это число не меняет»; комментарий с меткой «шаблон» / «свой текст» /
  «свой текст (пусто)» и кнопкой «к шаблону» — **при любом `kind = custom`,
  независимо от `template_text`** (круг 1 гейта 3: иначе свой текст у строки
  без шаблона не снять — очистка поля сохранила бы пустой свой текст и глушила
  бы будущие шаблоны); «к шаблону» шлёт `DELETE …/comment`; тест: свой текст
  при `template_text = null` → «к шаблону» → запрос `DELETE` ушёл, метка
  после ответа — без «свой текст»; при `template_text` кнопка подсказкой
  показывает, какой текст вернётся, без него — «комментария не будет»;
- ввод «Договорились» уходит строкой с `basis` по переключателю НДС
  (`net`/`shown`); при неизвестной базе поле принимает только нетто;
- у `member` — значения без полей ввода и кнопок (тест: ни одного `textbox`
  в блоке);
- лестница: при предпросмотре ступени ≠ действующей у `admin` — «Сохранить эту
  ступень» (только для `selectable`); при действующей ручной — «Вернуть авто»;
  метка «ручная» / «ручная · для всей семьи в этом КП»; недействующая — плашка
  «Ручная ступень {подпись} не действует: {причина} — считается
  автоматическая» с текстом причины по каждому из четырёх кодов;
- сохранение и правки — под серым слоем (`RecalcOverlay`) с подписью;
  переключатель НДС — без запроса;
- строки разбора: под нашей расценкой — «договорились {X}»; у подписи
  ступени — «ручная» либо «ручная не действует»;
- докстрока `LineSheet` «Блока „Для выгрузки“ здесь нет» переписана;
- вид чисел: «Договорились» с 30 знаками в ответе печатается двумя знаками
  (`formatDecimalMoney`), строки длиннее двух знаков на экране нет.

**Имена**
- Заводятся: `ExportBlock`, `RungSaveBar`, тексты 4б в `ourPriceCopy.ts`.
- Существуют: `LineSheet`, `PanelContent`, `Ladder`, `RecalcOverlay`, `formatDecimalMoney`, `useCurrentUser`.

**Проверка**
- `npx vitest run src/pages/tenders/our-price` — ДО 321 (`npx vitest list our-price`, 27.10.2026), ПОСЛЕ ≥ 341.
- Снимок стенда панели при 1440 и 1280 после задачи (память «вид чисел тестам не виден»).

### Task 12: страница предпросмотра и «Отправлено подрядчику»

**Files**
- Create: `frontend/src/pages/tenders/our-price/KpFilePage.tsx`, `KpFileTable.tsx`, `SentFilesList.tsx` (+ `.test.tsx`)
- Edit: `frontend/src/App.tsx`, `ourPriceCopy.ts`

**Interfaces**
- Потребляет: Task 10, `ExportBlock` не потребляется (свой редактор ячейки).
- Производит: маршрут `/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId/file` (вне `RequireAdmin`, `?inflation=` как у разбора).

**Утверждения**
- таблица «как в файле»: разделы подрядчика строками, наши столбцы справа и
  выделены; столбец «Приведение по ИПЦ» — только при ряде; шапка — подписи
  листа и блок лет ИПЦ;
- сортировок и фильтров нет;
- у `admin` комментарий правится в ячейке той же мутацией; после сохранения
  предпросмотр перезапрошен и показывает новый текст; «Скачать xlsx»
  неактивна, пока идёт мутация или перезапрос (`isFetching`);
- отказ ИПЦ — второй запрос без ряда и баннер своей ветки (поведение 4а);
  выгрузка уходит с параметрами показанного предпросмотра;
- плашка «каталог ещё разбирает N строк — в файле у них не будет наших цен»
  при `in_progress_lines > 0`;
- «Отправлено подрядчику»: дата, кто, состав НДС и уровень, «скачать»; новые
  сверху; пустой список — текст «файлов ещё не отправляли»;
- `member` скачивает и выгружает, ячейка комментария у него не редактируется;
- `container-page` на корне страницы (`docs/pitfalls/frontend.md`).

**Имена**
- Заводятся: `KpFilePage`, `KpFileTable`, `SentFilesList`, маршрут.
- Существуют: `InflationRefusalSection`, `withNominalFallback`, `useUrlSwitches`, `CatalogProgressAlert`.

**Проверка**
- `npx vitest run src/pages/tenders/our-price` — ДО ≥ 341, ПОСЛЕ ≥ 356.
- Снимок стенда страницы при 1440 и 1280, замер вёрстки по рамкам ячеек (0 наложений).

### Task 13: кнопка выгрузки, «Назначить базу НДС», карточка участника

**Files**
- Create: `frontend/src/pages/tenders/our-price/VatBaseDialog.tsx` (+ `.test.tsx`)
- Edit: `OurPriceHeader.tsx`, `OurPriceOfferPage.tsx`, `ParticipantCard.tsx`, `ourPriceCopy.ts` (+ тесты)

**Interfaces**
- Потребляет: Task 10 (`useSetOfferVatBase`).
- Производит: `VatBaseDialog({estimateId, tenderId, roundId})` — сам возвращает `null` у не-`admin` (приём `VatRateDialog`).

**Утверждения**
- в шапке разбора — «Выгрузить КП с нашими расценками», ведёт на страницу
  предпросмотра с тем же `?inflation=`; у КП `unknown_vat_base` кнопка
  неактивна с подписью «база НДС КП неизвестна»;
- «Назначить базу НДС» — у `admin` в шапке разбора и на карточке участника
  `unknown_vat_base` экрана этапа; у `member` — нет;
- диалог: одно поле ставки (строка, 0–100), «Сохранить» шлёт
  `{base_override}`; после успеха экран этапа и разбор перезапрошены, КП
  становится сравнимым (тест: ответ после мутации без `unknown_vat_base`);
- отказ сервера (строкой без `code`) — текст в диалоге.

**Имена**
- Заводятся: `VatBaseDialog`.
- Существуют: `OurPriceHeader`, `ParticipantCard`, `emptyReasonOf`, `VatRateDialog` (как образец, не импортируется).

**Проверка**
- `npx vitest run src/pages/tenders/our-price` — ДО ≥ 356, ПОСЛЕ ≥ 364.

### Task 14: ревизия `AGENTS.md` и справочник

**Files**
- Edit: `AGENTS.md` (§3, §8, преамбула), `docs/AGENTS-revisions.md` (врезка действующей ревизии уезжает в архив), `docs/reference/schema.md`, `docs/reference/screens.md`, `docs/reference/money-axes.md`

**Interfaces**
- Потребляет: §2.15 спеки, фактические имена Task 1–13.
- Производит: следующая версия `AGENTS.md` (номер ставит задача — следующий за действующим).

**Утверждения**
- §3, роли: `admin` — «Договорились», свой комментарий, ручная ступень строки
  на экране «Против нашей цены»; `member` — выгрузка файла подрядчику и
  скачивание отправленных; «Что уходит наружу» не тронут ни символом;
- §8: второй класс хранимых файлов — отправленные подрядчику; выдача —
  `GET …/sent-files/{id}/file`; ретенции нет; удаление с участником, раундом,
  тендером после коммита; правило «удаление участника файлы НЕ трогает»
  уточнено как правило файлов импорта;
- §1, §2, §4–§7, §9–§12 — ни одного символа (проверка `git diff` по
  разделам);
- преамбула — врезка новой версии; прежняя врезка (v6.29) — в
  `docs/AGENTS-revisions.md` без правок, кроме относительных ссылок;
- `schema.md` — блок «Решения по строкам КП и отправленные файлы» с пятью
  таблицами; `screens.md` §8 — абзац «Против нашей цены» дописан (правка у
  `admin`, ступень и сброс, «Для выгрузки», предпросмотр, «Отправлено
  подрядчику», «Назначить базу НДС», запрет выгрузки при неизвестной базе),
  маршрут предпросмотра; `money-axes.md` — раздел «Файл подрядчику» и строка в
  §7 про ручную ступень;
- страж `just check-agents-index` — 18 из 18.

**Имена**
- Существуют: разделы и файлы выше.

**Проверка**
- `just check-agents-index` — 18 из 18 (ДО 18 из 18).

### Task 15: стенд, приёмка, devlog

**Files**
- Create: `docs/devlog/2026-10-27-contractor-file.md`
- Edit: `docs/product-roadmap.md`

**Утверждения**
- DoD 1–12 спеки — по пункту в devlog с вердиктом и тем, чем доказано; на
  стенде — тендер Объекта Т, этап 3, участники «Подрядчик А»…«Подрядчик Г»;
  стенд — только чтение: решения пишутся на **копии** базы (как DoD 9–10 4а),
  `member` — на копии;
- файл стенда открыт и просмотрен: порядок и разделы подрядчика, база НДС КП,
  ни одного числа вилки, кроме цели, в комментариях (поиск подстрокой);
- снимки панели, предпросмотра и списка при 1440 и 1280; замер вёрстки — 0;
- devlog: отступления (`R…`), тронутые области и прочитанные файлы граблей
  (`backend`, `db`, `frontend`, `runs`, `surfaces`, `process`), знаменатель
  ревью по задачам; отложенное — с причиной;
- дорожная карта: 4б закрыта, дальше 4в и скорость 4а; в «Отложено» devlog —
  условие §2.17 одной строкой: кеш истории обязан покрыть `export-preview`,
  `export` и проверку `PUT …/rung`; время предпросмотра и выгрузки на стенде
  записано (цели нет, замер — для задачи скорости).

**Проверка**
- `uv run pytest tests/unit -q -k "test_kp_file or test_our_price"` и `just test-int-local-k "test_kp_file or test_our_price"` — зелёные; `npx vitest run src/pages/tenders src/services src/components/tenders` — зелёный; полный набор — GitHub Actions (решение пользователя 13.10.2026).

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче: GitHub Actions (`just ci` локально не гоняется — решение пользователя
  11.10.2026); перед пушем — затронутые наборы и `just check-agents-index`.
- Стенд: после каждой фронтовой задачи (11–13) — снимки при 1440 и 1280.
