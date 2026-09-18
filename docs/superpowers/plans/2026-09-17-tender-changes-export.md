# План: выгрузка «Изменения КП»

**Спека:** `docs/superpowers/specs/2026-09-16-tender-changes-export-design.md`
**Ветка:** `feat/tender-changes-export` — семь коммитов гейтов 1–2,
перебазирована 18.09.2026 на `main` = `55f8465` (мерж prerequisite'а, PR #51)

## Global Constraints

- **Деньги — `Decimal`, никогда `float`** (`AGENTS.md` §3). На листе деньги
  кладутся числами (`backend/services/excel.py`), не строками: `СУММ` по
  текстовой колонке молча даёт ноль (спека §2.5).
- **Округление денег — только `finance.money_round`** (`ROUND_HALF_UP`).
  Собственного написания правила в фиче не заводится ни одного: умолчание
  `Decimal.quantize` — `ROUND_HALF_EVEN`, и на `0,005` оно даёт другой ответ
  (спека §2.3).
- **Тождество состава проверяется на ПЕЧАТАЕМЫХ Δ**, не на свёртке и не с
  построчным допуском (спека §2.3). Нарушено — три колонки гасятся целиком.
- **Предикат цены и предикат веса — «конечно и больше нуля»** (`AGENTS.md` §4,
  §6): ни `x > 0`, ни `x = x` не отсекают `NaN` в PostgreSQL.
- **Ни одна строка сметы не выбрасывается** (спека §2.2): `v_category_totals`
  фильтра по виду каталожной записи не имеет, и книга обязана сохранить каждый
  его рубль.
- **Ноль и пустая цена значат только «цены нет»** — причина не утверждается
  (`AGENTS.md` §4, §6).
- **Ось сравнения и подпись состава** — правило единогласия свода (спека §2.9,
  `docs/reference/money-axes.md`): одна известная ставка → валовые, несколько →
  нетто целиком, ни одной → сумм нет. Подписи налогового состава и ценового
  уровня печатаются на каждом листе (`AGENTS.md` §10).
- **Права — чтение:** `admin` и `member` (`AGENTS.md` §3, спека §2.11).
  Отдельной зависимости на роль эндпоинт не заводит.
- **Обратная совместимость:** ни один существующий ответ и ни один
  существующий лист не меняется ни одним байтом. Перенос хелпера ответа
  (задача 1) обязан оставить `reports_api` и `comparison_excel` зелёными.
- **Экраны свода по этапам и попозиционного раскрытия не правятся ни одним
  символом** (спека §4). Миграций, правок парсера, импорта и матчинга нет.
- **`AGENTS.md` эта фича не правит** — ревизия ей не нужна (спека, преамбула);
  `just check-agents-index` остаётся 18 из 18 на каждой задаче (восемнадцатую
  проверку завела фича переноса карты, не эта).
- **`docs/reference/screens.md` §8 правится ТОЙ задачей, которая заводит
  эндпоинт и кнопку** (спека §2.12), не раньше: справочник описывает
  сегодняшнее состояние.
- **Число запросов не зависит от числа листов** (спека §2.1): чтение идёт
  ограниченным набором запросов с `IN (…)` по сметам тендера.

## Структура файлов

```
backend/
  responses.py                                   Edit   задача 1
  routers/reports.py                             Edit   задача 1
  routers/tenders.py                             Edit   задача 5
  services/changes_export.py                     Create задача 2
  crud/changes_export.py                         Create задача 3
  services/excel_changes_export.py               Create задача 4
  tests/unit/test_responses.py                   Edit   задача 1
  tests/unit/test_changes_export.py              Create задача 2
  tests/unit/test_changes_export_sheet.py        Create задача 4
  tests/integration/test_changes_export_sql.py   Create задача 3
  tests/integration/test_changes_export_api.py   Create задача 5
frontend/src/
  services/api/domain.ts                         Edit   задача 5
  services/queries.ts                            Edit   задача 5
  pages/tenders/TenderCardPage.tsx               Edit   задача 5
  pages/tenders/TenderCardPage.test.tsx          Edit   задача 5
  services/queries.tenders.test.tsx              Edit   задача 5
docs/
  reference/screens.md                           Edit   задача 5 (§8)
  devlog/2026-09-17-tender-changes-export.md     Create задача 6
  product-roadmap.md                             Edit   задача 6
```

Удаляется: ничего.

## Решения плана, которых нет в спеке

1. **Предикаты цены, веса и конечности пишутся В МОДУЛЕ `crud/changes_export.py`,
   а не берутся из `crud/analytics.py`.** В `crud/analytics.py` уже живут
   `_not_finite`, `_price_ok`, `_weight_ok`, а в `crud/project_passport.py` —
   `_finite_amount`; их докстроки прямо объявляют, что модули проекта не тянут
   друг у друга приватные имена, и называют причину (разная полярность, разное
   поведение на `NULL`). Плану предлагается следовать этому соглашению, а не
   ломать его ради одной фичи и не расширять скоуп до общего модуля предикатов
   (это тронуло бы матрицу, которую спека §4 объявила незатрагиваемой).
   Расхождение с единственным источником правила ловится не общим символом, а
   **внешним оракулом**: тест задачи 3 прогоняет SQL-предикат и
   `money.price.is_price` / `money.price.is_weight` по одному и тому же
   граничному набору (`NULL`, `0`, отрицательное, `NaN`, `Infinity`,
   `-Infinity`, положительное) и требует совпадения на всех семи входах.
   Альтернатива — общий модуль `money/price_sql.py` с переносом трёх функций из
   `crud/analytics.py` — отвергнута по скоупу; если пользователь решит иначе,
   это отдельная фича, а не задача этой.
2. **Вместе с `_xlsx` в `backend/responses.py` переезжает `_safe_filename_part`**
   (сегодня — приватный в `routers/reports.py`). Довод тот же, которым спека
   §2.11 обосновывает переезд `_xlsx`: имя книги несёт номер тендера, а номер
   из карточки законно содержит `/` — второе написание чистки разъехалось бы с
   первым. Публичные имена: `xlsx_response`, `safe_filename_part`,
   `XLSX_MEDIA_TYPE`. **Фолбэк пустого значения — явный параметр**, а не
   зашитая строка: сегодня он зашит словом `"договор"`, и общий хелпер с
   зашитым фолбэком остался бы скрыто договорным — книга тендера подписалась бы
   договором. Три существующих отчёта передают `fallback="договор"` и своих
   байтов не меняют.
3. **Чистый слой сборки отделён от чтения.** `services/changes_export.py` не
   знает ни про `Session`, ни про SQL и принимает датаклассы; `crud/changes_export.py`
   их наполняет. Это то же разделение, что у свода по этапам
   (`services/stage_summary.py` ↔ `crud/stage_summary.py`), и оно же делает
   исполнимыми пять пунктов DoD, недостижимых на стенде: вход строится руками.
   Граница проведена там же, где её провела обвязка гейта 2: SQL считает
   построчные счётчики (в том числе «у строки есть все три конечные
   составляющие»), Python — свёртку, Δ и тождество.
4. **Порядок строк листа и порядок листов книги фиксируются, и ключ сортировки
   нормализован.** Спека порядка не называет, а без него две выгрузки одних и
   тех же данных дают разные файлы, и сверить их нечем. Сортировать по КОДУ
   статьи нельзя по двум причинам сразу: у «Нераспределённого» кода нет вовсе, и
   `None` с `str` в Python не сравнивается — сортировка упала бы исключением, а
   не встала бы не туда; а лексикографический порядок кодов ставит `10.1` перед
   `2.5`, тогда как классификатор задаёт другой. Поэтому ключ — **`sort_order`
   классификатора**, тот же, которым §2.13 спеки свода по этапам уже решил этот
   же вопрос (`services/stage_summary.sort_key`), и он `UNIQUE` в схеме, то есть
   задаёт полный порядок. Ключ строки — `(0, sort_order статьи, наименование
   работы, ключ ветви)` для статьи и `(1, 0, …)` для «Нераспределённого»:
   первое поле ставит его последним, и `None` в сравнение не попадает никогда.
   Листы — по наименованию участника.
5. **Имя листа, ставшее пустым после чистки, заменяется на `Участник N`**
   (N — порядковый номер листа). Спека §2.1 задаёт чистку и обрезку, но не
   говорит, что делать с наименованием из одних запрещённых символов; пустое
   имя листа формат отвергает.
6. **Суффикс разведения совпадений учитывается в пределе 31 знака:** обрезка
   повторяется ПОСЛЕ добавления ` (2)`. Иначе разведённое имя выходит за предел,
   который спека объявила требованием формата.
7. **Коды отказов повторяют строки, а не импортируют их:**
   `CODE_TENDER_NOT_FOUND = "tender_not_found"` заводится в
   `crud/changes_export.py` своим экземпляром (в `crud/stage_summary.py` он
   приватная константа того же модуля). Совпадение строки закреплено
   утверждением задачи 3, а не импортом.
8. **Devlog и правка карты вынесены в отдельную задачу 6**, потому что DoD 17
   был неисполним до мержа фичи переноса карты под гит. **Prerequisite снят
   18.09.2026** (PR #51): карта отслеживается как `docs/product-roadmap.md`,
   §9.2 получила класс «Дорожная карта», и задача 6 несёт фактический путь, а не
   заглушку. Ветка перебазирована на `55f8465` в тот же день. Разделять задачу
   и вливать её в задачу 5 всё равно не стоит: правка карты и devlog пишутся
   ПОСЛЕ того, как известны замеры задач 1–5.
9. **Денежное состояние — ОДИН тип на ячейку, подытог статьи и общий итог.**
   Первая редакция плана типизировала подытоги как `list[Decimal]`, и этап с
   неизвестной базой НДС мог бы попасть в них только ложным нулём — тот самый
   дефект «одно значение на два состояния»
   (`docs/insights/one-value-two-states.md`), только этажом выше ячейки, где он
   уже разобран. Прецедент контракта есть и утверждён: `TotalCell` свода по
   этапам несёт `shown: Decimal | None` плюс `unavailable_reason`. Здесь тот же
   контракт одним типом `Money`, который носят и ячейка, и подытог, и итог.
10. **Две недоступности разведены, потому что ведут себя по-разному.**
   *Недоступность по ОСИ* — неизвестная база НДС этапа либо `TAX_NONE` — гасит
   этап ЦЕЛИКОМ: ячейки, подытоги статей и общий итог этого этапа недоступны с
   причиной. *Недоступность по СТРОКЕ* — «конечной суммы нет» у одной группы —
   вносит в подытог ноль, как `SUM` в `v_category_totals` игнорирует `NULL`, но
   поднимает признак неполноты: это в точности приём `row_amount` /
   `row_amount_incomplete` из `AGENTS.md` §6. Смешать их нельзя: первая обязана
   гасить, вторая обязана НЕ гасить — иначе сходимость DoD 4 перестала бы
   держаться, потому что VIEW такую строку тоже считает нулевым вкладом.

---

## Задачи

### Task 1: `xlsx_response` и `safe_filename_part` в `backend/responses.py`

**Files**
- Edit: `backend/responses.py`
- Edit: `backend/routers/reports.py`
- Test: `backend/tests/unit/test_responses.py`

**Interfaces**
- Потребляет: `fastapi.Response` (существует), `urllib.parse.quote` (существует).
- Производит:

```python
XLSX_MEDIA_TYPE: str

def xlsx_response(content: bytes, filename: str) -> Response
def safe_filename_part(value: str, *, fallback: str) -> str
```

**Утверждения**
- `xlsx_response` отдаёт `bytes`, а не `StreamingResponse` с файловым объектом:
  Starlette итерирует такой объект построчно и не закрывает хендл (грабля фазы 4,
  спека §2.11);
- заголовок ответа — `Content-Disposition: attachment; filename*=UTF-8''<percent>`,
  где `<percent>` — `quote(filename)`; ASCII-форма `filename=` не заводится:
  русские буквы в ней искажаются;
- `safe_filename_part` заменяет каждый символ из `/\:*?"<>|` на `-`, а строку,
  ставшую пустой после `strip`, — на значение ОБЯЗАТЕЛЬНОГО именованного
  параметра `fallback`; зашитого слова в общем хелпере нет: книга тендера,
  подписавшаяся словом «договор», была бы скрытой договорённостью, а не
  контрактом;
- три существующих отчёта передают `fallback="договор"` и отдают то же имя
  файла, что до переноса, на входе из одних запрещённых символов;
- три существующих отчёта (`contract-summary`, `bank-comparison`, `comparison`)
  после переноса отдают те же байты тела и тот же заголовок
  `Content-Disposition`, что до него — проверяется существующими тестами
  `reports_api` и `comparison_excel`, которые остаются зелёными БЕЗ правок;
- в `routers/reports.py` не остаётся ни одного собственного написания ни
  хелпера ответа, ни чистки имени: второе написание разъехалось бы с первым
  (спека §2.11).

**Имена**
- Заводятся этой задачей: `xlsx_response`, `safe_filename_part`.
- Существуют, проверено `grep`-ом: `XLSX_MEDIA_TYPE`, `_xlsx`,
  `_safe_filename_part` (все три — `backend/routers/reports.py`, переезжают);
  `decimal_json`, `DecimalJSONResponse` (`backend/responses.py`).

**Проверка**
- `just test-unit-k responses` — зелёная. ДО задачи команда выбирает **8**
  тестов (`tests/unit/test_responses.py`); ПОСЛЕ — не меньше **14**: шесть
  утверждений задачи заводят свои входы, и новые имена обязаны попадать в тот
  же выбор. Точное число называет отчёт о задаче.
- `just test-int-local-k "reports_api or comparison_excel"` — зелёная. ДО — **65**
  тестов, ПОСЛЕ — те же **65**: задача не заводит здесь ни одного теста, и
  совпадение чисел тут ОЖИДАЕМО, а не сигнал (задача переносит хелпер, а не
  меняет поведение отчётов).
- `just lint-backend` — зелёная.

---

### Task 2: сборка листа — чистый слой `services/changes_export.py`

**Files**
- Create: `backend/services/changes_export.py`
- Test: `backend/tests/unit/test_changes_export.py`

**Interfaces**
- Потребляет: `finance.money_round` (существует), `money.vat.gross_to_net`
  (существует), `services.stage_summary.pick_tax_basis`,
  `services.stage_summary.to_shown`, `services.stage_summary.TaxBasis`,
  `services.stage_summary.TAX_GROSS`, `TAX_NET`, `TAX_NONE` (существуют).
- Производит:

```python
KIND_WORK: str
KIND_ADDITIONAL: str
KIND_NONWORK: str
KIND_UNMATCHED: str
MONEY_ONLY_KINDS: frozenset[str]          # три ветви без состава (спека §2.2)
NON_WORK_TITLES: dict[str, str]           # HEADER / LOT_HEADER / TRASH → название группы
UNMATCHED_TITLE: str
UNALLOCATED_ARTICLE: "ArticleRef"
BRIEF_LIMIT: int                          # сколько кодов статей печатается перечнем

REASON_NO_AMOUNT: str                     # «суммы нет» / Δ недоступна
REASON_NO_PRICE: str                      # «цены нет»
REASON_MIX_INCOMPLETE: str                # «состав неполон»
REASON_UNKNOWN_VAT_BASE: str

@dataclass(frozen=True)
class ArticleRef:
    code: str | None
    title: str | None
    sort_order: int | None        # None — только у «Нераспределённого»

@dataclass(frozen=True)
class Money:
    """Денежное состояние: величина, причина её отсутствия и признак неполноты.

    Инвариант: `reason is None` ⟺ `value is not None`.
    Оси независимы: `incomplete` говорит «величина есть, но посчитана не по всем
    строкам», а не «величины нет».
    """
    value: Decimal | None
    reason: str | None
    incomplete: bool

@dataclass(frozen=True)
class Components:
    works: Decimal | None
    materials: Decimal | None
    indirect: Decimal | None

@dataclass(frozen=True)
class GroupRow:
    """Одна строка агрегата позиций: (этап, статья, каталожная позиция)."""
    stage_no: int
    article: ArticleRef
    catalog_position_id: int | None
    catalog_kind: str | None
    work_title: str | None
    unit: str | None
    rows_all: int
    rows_with_amount: int
    rows_with_mix: int
    rows_priced: int
    amount: Decimal | None
    components: Components
    quantity: Decimal | None
    price_num: Decimal | None
    price_den: Decimal | None
    unit_components_num: Components
    numbers: tuple[str, ...]

@dataclass(frozen=True)
class AdditionalRow:
    stage_no: int
    article: ArticleRef
    lot_key: str
    chapter_ref_raw: str | None
    work_title: str | None
    amount: Decimal | None
    rows_all: int
    rows_with_amount: int

@dataclass(frozen=True)
class StageInput:
    stage_no: int
    label: str | None
    held_on: dt.date | None
    vat_rate_base: Decimal | None

@dataclass(frozen=True)
class SheetInput:
    participant_title: str
    stages: list[StageInput]
    groups: list[GroupRow]
    additional: list[AdditionalRow]

@dataclass(frozen=True)
class Cell:
    kind: str
    amount: Money
    quantity: Decimal | None
    unit_price: Money
    components: Components | None
    numbers: tuple[str, ...]
    rows_all: int
    rows_with_amount: int
    rows_priced: int

@dataclass(frozen=True)
class SheetRow:
    key: tuple
    kind: str
    article: ArticleRef
    work_title: str
    unit: str | None
    cells: list[Cell | None]
    delta_amount: Money
    delta_pct: Decimal | None
    delta_components: Components | None
    components_reason: str | None
    completeness: str
    route: list[str]

@dataclass(frozen=True)
class Subtotal:
    article: ArticleRef
    rows: int
    by_stage: list[Money]
    delta: Money

@dataclass(frozen=True)
class Sheet:
    title: str
    stages: list[StageInput]
    rows: list[SheetRow]
    subtotals: list[Subtotal]
    grand_by_stage: list[Money]
    grand_delta: Money
    tax_basis: TaxBasis
    moves_note: str

def build_sheet(data: SheetInput) -> Sheet
def sheet_names(titles: Sequence[str]) -> list[str]
def article_sort_key(article: ArticleRef) -> tuple[int, int]
```

**Утверждения**
- ключ строки — каталожная позиция ВНУТРИ статьи; несколько строк сметы с одной
  каталожной позицией сворачиваются в одну ячейку этапа (спека §2.2);
- ветвей ровно четыре, и они различаются ключом: работа — `(статья,
  catalog_position_id)`; допработа — `(статья, lot_key, chapter_ref_raw)`;
  неработа по виду каталога — `(статья, kind)`; непривязанная строка — `(статья,
  сентинел)`;
- `POSITION` и `TO_REVIEW` идут в ветвь «работа»; `HEADER`, `LOT_HEADER` и
  `TRASH` — в три РАЗНЫЕ названные группы, по одной на статью; строка без
  каталожной записи — в четвёртую; всего четыре диагностических названия, и они
  различны;
- ни одна строка входа не пропадает: сумма `amount` по всем ячейкам этапа равна
  сумме `amount` по всем строкам входа этого этапа — на входе, где присутствуют
  все четыре ветви разом;
- всё без статьи — позиции, допработы, непривязанные — идёт в одну группу
  «Нераспределённое», и её наличие не отменяет сборку (спека §2.2);
- у ветвей `MONEY_ONLY_KINDS` печатается только сумма: `quantity`, `unit_price`
  и `components` у их ячеек — `None`, а не ноль и не `KeyError`;
- инвариант `Money` держится везде, где тип применён: `reason is None` тогда и
  только тогда, когда `value is not None`; признак `incomplete` от этого не
  зависит и живёт своей осью;
- сумма ячейки различает ТРИ состояния: строки нет на этапе → ячейки нет;
  строка есть и `rows_with_amount = 0` → `amount.value is None` с
  `amount.reason = REASON_NO_AMOUNT`; конечная сумма равна нулю →
  `amount.value == Decimal(0)`;
- цена группы — правилом матрицы `Σ(цена × вес) / Σ вес` по строкам, где
  пригодны И цена, И вес; при пустом знаменателе `unit_price.value is None` с
  `unit_price.reason = REASON_NO_PRICE`. Свёрнутая сумма на свёрнутый объём не
  делится: нерасценённая строка вносит ноль в числитель и свой объём в
  знаменатель;
- состав на единицу считается по ТОМУ ЖЕ множеству, что цена; состав абсолютный
  — по множеству суммы (спека §2.4);
- концы Δ — ПЕРВЫЙ и ПОСЛЕДНИЙ этап участника, а не первое и последнее появление
  строки; отсутствие строки на конце даёт нулевой вклад, и Δ считается от нуля
  либо к нулю;
- `delta_pct is None`, если строки на первом этапе нет ЛИБО её сумма на первом
  этапе неположительна; отрицательный и нулевой знаменатель — тот же исход, что
  отсутствие;
- `delta_amount.value is None` с `delta_amount.reason = REASON_NO_AMOUNT`, если
  у любого из двух концов есть ячейка без конечной суммы;
- три Δ состава печатаются ТОЛЬКО когда держится тождество на ПЕЧАТАЕМЫХ Δ:
  каждая из четырёх величин округляется `finance.money_round` до двух знаков, и
  сумма трёх округлённых Δ состава ТОЧНО равна округлённой Δ суммы; иначе
  `delta_components is None` и `components_reason = REASON_MIX_INCOMPLETE`;
- построчная полнота — первое из двух условий: `rows_with_mix` конца должен
  равняться его `rows_with_amount`; отсутствующий конец полон по построению;
- «состав неполон» предъявляется ПЯТЬЮ входами: (а) строка с конечной суммой и
  пропущенной составляющей; (б) группа конечных величин, у которой печатаемые Δ
  не сходятся; (в) расхождение ровно в копейку; (г) две строки по полкопейки в
  одну сторону, построчно полные; (д) `0,004 × 3` против `0,012`. Вход (а)
  ловится первым условием, входы (б)–(д) — вторым, и на них построчная полнота
  ЗЕЛЕНА;
- проверка обязана краснеть при возврате построчного допуска, при свёртке перед
  округлением и при подмене `ROUND_HALF_UP` умолчанием `quantize`: последнее
  различает граница `0,005 → 0,01`;
- в `delta_components` попадают РОВНО те округлённые числа, на которых тождество
  проверено, а не их первообразы;
- на три денежные ветви тождество не распространяется вовсе: у них
  `delta_components is None` и `components_reason is None` — прочерк, а не
  состояние (спека §2.3);
- «Что двигалось» — маршрут по КАЖДОЙ соседней ПО ШКАЛЕ паре этапов; пропуск
  промежуточного этапа не склеивается, и шаг `Э1→Э3` при живом Э2 не
  появляется;
- числовые основания (сумма, объём, цена, состав) требуют обеих ячеек;
  структурные (появилась в КП, исчезла из КП, переезд по статьям) требуют как
  раз перехода «нет ↔ есть» либо смены множества статей;
- «сумма недоступна» — отдельное слово, оно не сливается со словом «сумма»;
- основание «состав» считается НА ЕДИНИЦУ объёма: по абсолютам один лишь рост
  объёма выглядел бы сменой состава;
- подпись о статье строко-локальна: на паре «источник — получатель» одного
  переезда тексты РАЗНЫЕ («ушла в …» против «пришла из …»);
- «исчезла из КП» и «появилась в КП» считаются ПО РАБОТЕ ЦЕЛИКОМ, а не внутри
  статьи; работа, сменившая статью, не получает ни того, ни другого;
- перечень статей в подписи режется на `BRIEF_LIMIT` кодах, дальше — «и ещё N»;
- `moves_note` считает переклассификацией случай, когда ОБА множества статей
  непусты и различаются на соседних по шкале этапах; появление работы после
  пустого этапа переездом не считается;
- ось листа выбирается `pick_tax_basis` по множеству ИЗВЕСТНЫХ ставок этапов:
  одна → валовые, несколько → нетто целиком, ни одной → сумм нет; на этапе с
  неизвестной базой цена, сумма и компоненты погашены с
  `REASON_UNKNOWN_VAT_BASE`, а Δ с таким концом несёт причину, а не пустую
  ячейку;
- **подытог статьи и общий итог несут ТОТ ЖЕ тип `Money`, что ячейка**: на
  этапе с неизвестной базой НДС `Subtotal.by_stage[i].value is None` и
  `Sheet.grand_by_stage[i].value is None`, оба с
  `reason = REASON_UNKNOWN_VAT_BASE` — ложного нуля там не появляется ни в
  одной из трёх величин;
- при `TAX_NONE` (среди этапов нет ни одной известной ставки) недоступны ВСЕ
  суммы, ВСЕ подытоги, весь общий итог и все Δ — на каждом этапе, а не только
  на одном;
- **две недоступности ведут себя по-разному, и вход это различает**: этап с
  неизвестной базой гасит подытог статьи; строка с `rows_with_amount = 0` на
  этапе с ИЗВЕСТНОЙ базой вносит в подытог ноль и подытог не гасит, но
  поднимает `Subtotal.by_stage[i].incomplete` — тот же приём, что
  `row_amount` / `row_amount_incomplete` в `AGENTS.md` §6;
- **Δ считают ровно два поля — `Subtotal.delta` и `Sheet.grand_delta`**, и оба
  недоступны, если недоступен любой из двух концов: Δ подытога и Δ итога
  наследуют недоступность так же, как Δ строки. `grand_by_stage` — список
  этапных итогов, а не Δ, и недоступность одного этапа гасит только его
  элемент; блок подытогов печатает обе величины (макет гейта 1, строка
  «ИТОГО» с колонкой `Δ Э1→Э4`);
- неполнота любого числового конца переходит в `delta.incomplete`: Δ, у которой
  хотя бы один конец посчитан не по всем строкам, не выдаёт себя за полную —
  то же требование, что `row_amount_incomplete` предъявляет строке матрицы;
- приведение к нетто выполняется ДО округления, а тождество состава проверяется
  ПОСЛЕ него: если приведение тождество нарушило, три колонки гасятся подписью,
  а не печатают несходящуюся тройку;
- `completeness` различает ДВА множества строк и называет их отдельно
  (`сумма 13/14, цена 12/14`); у трёх денежных ветвей в ней бывает только
  «сумма»;
- `sheet_names` чистит `[]:*?/\`, обрезает до 31 знака, разводит совпадения
  суффиксами ` (2)`, ` (3)` и повторяет обрезку ПОСЛЕ добавления суффикса; имя,
  ставшее пустым, заменяется на `Участник N`; на входе из двух участников с
  одинаковым 31-знаковым префиксом выходят два РАЗНЫХ имени не длиннее 31;
- порядок строк листа задаёт `article_sort_key`: первичный ключ — `sort_order`
  классификатора, вторичный — наименование работы, третичный — ключ ветви; два
  прогона на одном входе дают одинаковый порядок;
- `article_sort_key` НИКОГДА не сравнивает `None` со строкой и не бросает
  `TypeError`: у «Нераспределённого» `sort_order is None`, и первое поле ключа
  отправляет его в конец, не заглядывая во второе. Вход из статьи с кодом
  `10.1`, статьи с кодом `2.5` и «Нераспределённого» сортируется порядком
  классификатора, а не лексикографическим, и «Нераспределённое» стоит
  последним.

**Имена**
- Заводятся этой задачей: `changes_export` (модуль), `ArticleRef`,
  `Money`, `Components`, `GroupRow`, `AdditionalRow`, `StageInput`,
  `SheetInput`, `Cell`,
  `SheetRow`, `Subtotal`, `Sheet`, `build_sheet`, `sheet_names`,
  `article_sort_key`, `KIND_WORK`,
  `KIND_ADDITIONAL`, `KIND_NONWORK`, `KIND_UNMATCHED`, `MONEY_ONLY_KINDS`,
  `NON_WORK_TITLES`, `UNMATCHED_TITLE`, `UNALLOCATED_ARTICLE`, `BRIEF_LIMIT`,
  `REASON_NO_AMOUNT`, `REASON_NO_PRICE`, `REASON_MIX_INCOMPLETE`,
  `REASON_UNKNOWN_VAT_BASE`.
- Существуют, проверено `grep`-ом: `money_round` (`backend/finance.py`),
  `gross_to_net` (`backend/money/vat.py`), `pick_tax_basis`, `to_shown`,
  `TaxBasis`, `TAX_GROSS`, `TAX_NET`, `TAX_NONE`
  (`backend/services/stage_summary.py`), `CatalogKind.HEADER`,
  `CatalogKind.LOT_HEADER`, `CatalogKind.TRASH`, `CatalogKind.POSITION`,
  `CatalogKind.TO_REVIEW` (`backend/models.py`); `sort_key`
  (`backend/services/stage_summary.py` — прецедент решения о порядке, не
  импортируется: он берёт `CategoryRef`, а не `ArticleRef`);
  `WorkCategory.sort_order` (`backend/models.py`, `UNIQUE`).

**Проверка**
- `just test-unit-k changes_export` — зелёная. ДО задачи команда выбирает **0**
  тестов (модуля и файла нет вовсе); ПОСЛЕ — не меньше **38**: у задачи 38
  утверждений, и у каждого свой вход, а пять случаев «состав неполон» дают пять
  входов на одно утверждение. Точное число называет отчёт о задаче.
- `just lint-backend` — зелёная.

---

### Task 3: чтение входов — `crud/changes_export.py`

**Files**
- Create: `backend/crud/changes_export.py`
- Test: `backend/tests/integration/test_changes_export_sql.py`

**Interfaces**
- Потребляет: `services.changes_export.SheetInput`, `GroupRow`,
  `AdditionalRow`, `StageInput`, `ArticleRef`, `Components` (заводятся задачей 2);
  `crud.common.DomainError` (существует); модели `Tender`, `OfferPackage`,
  `Offer`, `TenderRound`, `Estimate`, `Lot`, `Proposal`, `PositionItem`,
  `EstimateAdditionalWork`, `CatalogPosition`, `WorkCategory`, `Contractor`,
  `UnitOfMeasure` (существуют).
- Производит:

```python
CODE_TENDER_NOT_FOUND: str          # "tender_not_found"
CODE_NO_COMPARABLE: str             # "no_comparable_participants"
MIN_STAGES: int                     # 2 — минимум смет у участника

def load_book(db: Session, tender_id: int) -> list[cx.SheetInput]
```

Приватные помощники модуля — `_price_ok(column)`, `_weight_ok(column)`,
`_finite(column)`, `_participants(db, tender_id)`, `_groups(db, estimate_ids)`,
`_additional(db, estimate_ids)`, `_rates(db, estimate_ids)`.

**Утверждения**
- несуществующий тендер даёт `DomainError` со статусом `404` и кодом
  `tender_not_found`; тендер без единого участника с двумя и более сметами —
  статус `422` и код `no_comparable_participants`, а не пустую книгу;
- участник попадает в книгу по числу СМЕТ, а не предложений: участник с двумя
  предложениями, у одного из которых сметы нет, в книгу не попадает, а участник
  с двумя сметами попадает;
- этапы участника — ВСЕ, что у него есть, в порядке `stage_no`; подмножество не
  выбирается;
- `_price_ok` и `_weight_ok` совпадают с `money.price.is_price` и
  `money.price.is_weight` на семи граничных входах: `NULL`, `0`, отрицательное,
  `NaN`, `Infinity`, `-Infinity`, положительное. Голое `x > 0` на этом наборе
  расходится: PostgreSQL считает `'NaN'::numeric > 0` и `'Infinity'::numeric > 0`
  ИСТИНОЙ, и тест обязан краснеть, если исключения нефинитных убрать;
- три множества строк разведены запросом: присутствие считается по ВСЕМ
  строкам; сумма — только по конечным `total_cost_total`, причём нефинитная
  строка считается в `rows_all`, но не входит в `amount`; цена — по строкам, где
  пригодны И цена, И вес;
- нерасценённая строка не занижает цену группы: на входе из двух строк, где у
  одной цены нет, `price_den` равен весу ОДНОЙ пригодной строки, а не сумме
  весов обеих;
- `rows_with_mix` считает строки, у которых конечен итог И конечны все три
  составляющие; строка с `NULL` в одной составляющей в `rows_with_mix` не
  входит, оставаясь в `rows_with_amount` — эту классификацию считает именно SQL,
  и синтетический прогон гейта 2 её не проверяет;
- статья позиции берётся через `chapter_item_id` → `work_category_id`
  строки-раздела — тем же правилом, каким её берёт `v_category_totals`; ручной
  разнос статьи виден, потому что он пишет `work_category_id` на строку-раздел;
- ключ допработы — `lots.lot_key`, а не `lots.id`: на входе, где одна допработа
  присутствует в двух раундах и `lots.id` у них различны, ключ остаётся ОДИН, и
  строк листа получается одна, а не две;
- **сумма всех строк листа на каждом этапе равна сумме двух ветвей
  `v_category_totals`, ПРИВЕДЁННОЙ к выбранной оси**: ожидание считается
  применением `to_shown` с `TaxBasis` листа к КАЖДОЙ ставочной группе VIEW
  (`v_category_totals` группируется по `COALESCE(vat_rate_base_override,
  vat_rate)` и остаётся ВАЛОВЫМ всегда), и только потом складывается. Правило
  самого VIEW сохраняется дословно: БЕЗ фильтра по виду каталожной записи.
  Первая редакция этого утверждения сравнивала лист с сырой суммой VIEW и была
  верна только на валовой одноставочной оси — на нескольких ставках лист
  переходит в нетто, а VIEW нет, и проверка разошлась бы;
- сходимость предъявлена ЧЕТЫРЬМЯ входами, по одному на исход
  `pick_tax_basis` и на неизвестную базу: одна известная ставка среди этапов —
  валовая сходимость; несколько ставок — нетто-сходимость; этап с неизвестной
  базой — этап недоступен, и в сравнение не входит ни он, ни ноль вместо него;
  ни одной известной ставки — суммы и подытоги недоступны на всех этапах, и
  сравнивать нечего;
- проверка сходимости обязана краснеть на входе, где присутствуют строка за
  `HEADER`, строка за `LOT_HEADER`, строка за `TRASH` и строка без каталожной
  записи: на каталоге из одних `TO_REVIEW` фильтр по виду ничего не меняет, и
  такая проверка недискриминирующая;
- база НДС этапа — `COALESCE(estimates.vat_rate_base_override, единогласие
  Proposal.vat_rate)`, то же правило, что у `v_category_totals`; при
  разногласии предложений сметы база этапа `None`;
- `ArticleRef.sort_order` заполняется из `WorkCategory.sort_order`, а у
  «Нераспределённого» остаётся `None`: без этого поля `article_sort_key`
  задачи 2 нечем сортировать;
- число запросов НЕ зависит от числа листов: тендер с двумя участниками и
  тендер с шестью читаются одинаковым числом запросов;
- строки читаются `IN (…)` по сметам тендера, а не запросом на участника: при
  добавлении участника число запросов не меняется.

**Имена**
- Заводятся этой задачей: `crud.changes_export` (модуль), `load_book`,
  `CODE_TENDER_NOT_FOUND`, `CODE_NO_COMPARABLE`, `MIN_STAGES`, `_price_ok`,
  `_weight_ok`, `_finite`, `_participants`, `_groups`, `_additional`, `_rates`.
- Существуют, проверено `grep`-ом: `DomainError` (`backend/crud/common.py`),
  `is_price`, `is_weight` (`backend/money/price.py`), `CATEGORY_TOTALS`
  (`backend/crud/project_passport.py`), `chapter_item_id`, `work_category_id`,
  `lot_key`, `chapter_ref_raw`, `item_number_in_proposal`,
  `total_cost_indirect_costs`, `unit_cost_works`, `suggested_quantity`
  (`backend/models.py`), `import_round` (`backend/services/round_import.py`),
  `UnitResolver` (`backend/services/unit_resolution.py`), `CategoryResolver`
  (`backend/services/category_resolution.py`), `count_queries` (приём
  `backend/tests/integration/test_stage_summary_api.py`, копируется в файл
  задачи — общий хелпер не заводится).

**Проверка**
- `just test-int-local-k changes_export` — зелёная. ДО задачи команда выбирает
  **0** тестов; ПОСЛЕ — не меньше **16**: у задачи 16 утверждений, и у каждого
  свой вход; одна только сходимость DoD 4 требует четырёх входов. Точное число называет отчёт о задаче.
- `just test-int-local-k "category_totals_view or price_predicate"` — зелёная;
  ДО — столько же, сколько ПОСЛЕ: задача не трогает ни VIEW, ни миграцию, и
  совпадение здесь ожидаемо.
- `just lint-backend` — зелёная.

---

### Task 4: билдер книги — `services/excel_changes_export.py`

**Files**
- Create: `backend/services/excel_changes_export.py`
- Test: `backend/tests/unit/test_changes_export_sheet.py`

**Interfaces**
- Потребляет: `services.changes_export.Sheet`, `SheetRow`, `Cell`, `Subtotal`
  (заводятся задачей 2); `services.excel.workbook_bytes`, `write_cell`,
  `write_banner`, `write_column_headers`, `apply_column_widths`, `safe_str`,
  `fill`, `font`, `align`, `BORDER`, `FMT_MONEY`, `FMT_QTY`, `C_COL_BG`,
  `C_TOTAL_BG` (существуют).
- Производит:

```python
LEFT_COLUMNS: tuple[str, ...]        # Статья, Наименование статьи, Наименование работы, Ед.
STAGE_COLUMNS: tuple[str, ...]       # № строк, Объём, Цена за ед., Сумма
TAIL_COLUMNS: tuple[str, ...]        # Δ сумма, Δ %, Δ работы, Δ материалы, Δ косвенные, Полнота, Что двигалось
TEXT_NO_AMOUNT: str                  # «суммы нет»
TEXT_NO_PRICE: str                   # «цены нет»
TEXT_MIX_INCOMPLETE: str             # «состав неполон»
TEXT_UNKNOWN_VAT: str

def build_changes_export(sheets: Sequence[Sheet], *, tender_header: Mapping[str, str | None]) -> bytes
```

**Утверждения**
- колонок ровно `4 + 4 × число этапов + 7`: четыре слева, четыре на этап, семь
  справа (спека §2.3);
- область данных ПЛОСКАЯ: ни одной строки-заголовка статьи внутри диапазона
  автофильтра — статья живёт колонками в каждой строке;
- автофильтр стоит на области данных; подытоги статей — отдельным блоком ПОСЛЕ
  неё и ВНЕ диапазона автофильтра: `ws.auto_filter.ref` не покрывает ни одной
  строки блока подытогов;
- окно закреплено по шапке и первым четырём колонкам;
- денежная ячейка — ОДНОЯРУСНАЯ: деньги пишутся `Decimal` с форматом
  `FMT_MONEY`, а подпись неполноты стоит в СОСЕДНЕЙ колонке, не второй строкой
  внутри ячейки; иначе ячейка становится текстовой и `СУММ` по колонке молча
  даёт ноль;
- ячейка без конечной суммы несёт ТЕКСТ `TEXT_NO_AMOUNT`, а не число `0`;
  ячейка с конечным нулём несёт число `0`; ячейки отсутствующей строки нет —
  прочерк;
- **у подытога статьи, этапного итога и `grand_delta` причина бывает ТОЛЬКО
  осевой**: недоступны они лишь при неизвестной базе НДС этапа либо `TAX_NONE`,
  и печатают тогда `TEXT_UNKNOWN_VAT`. `TEXT_NO_AMOUNT` в блоке подытогов не
  появляется НИ РАЗУ — даже когда все строки статьи на этапе без конечной
  суммы: при известной оси такой подытог равен числовому нулю с
  `incomplete = True` (A2.31), а `REASON_NO_AMOUNT` относится к ячейке и её Δ,
  а не к агрегату;
- `Money` со значением печатается числом с `FMT_MONEY` — и в блоке подытогов
  тоже; ложного нуля вместо недоступности там нет ни на одном этапе;
- подытог со значением и признаком `incomplete` остаётся ЧИСЛОМ, а неполнота
  подписывается заливкой и соседней колонкой: иначе `СУММ` по блоку подытогов
  сломался бы ровно так же, как по колонке суммы;
- ячейка без пригодной цены несёт текст `TEXT_NO_PRICE`;
- у ветвей `MONEY_ONLY_KINDS` колонки «№ строк», «Объём» и «Цена за ед.» несут
  прочерк, а три колонки Δ состава — прочерк, а не подпись состояния;
- наименование работы прогоняется через `safe_str`: строка из чужого XLSX,
  начинающаяся с `=`, `+`, `-` или `@`, попадает на лист ТЕКСТОМ, а не формулой;
- шапка листа печатает подпись налогового состава («валовые, как в файлах» /
  «нетто») и подпись ценового уровня («номинальный, без приведения») — обе, на
  каждом листе;
- шапка листа печатает `moves_note` один раз; строковых подписей о массовости
  переезда нет;
- имена листов книги — результат `sheet_names`, и их столько же, сколько
  элементов в `sheets`; порядок листов совпадает с порядком `sheets`;
- книга СТРУКТУРНО читаема: `openpyxl.load_workbook` разбирает полученные
  `bytes` без исключения, и каждая ячейка, которой передан `Money.value`, после
  чтения обратно имеет числовой тип, а не строковый. Формулировка сужена
  намеренно: ячейки с `TEXT_NO_AMOUNT`, `TEXT_NO_PRICE` и `TEXT_UNKNOWN_VAT`
  текстовы ПО КОНТРАКТУ, и требование «каждая денежная ячейка числовая»
  противоречило бы им. Отсутствие окна восстановления Excel этим
  утверждением НЕ доказывается и доказываться не может: Excel — другой
  разборщик с другими требованиями, и прогон его не наблюдает
  (`docs/insights/unobservable-in-the-runner.md`). DoD 13 в части «без
  предупреждений» закрывается ручным замером на стенде, вынесенным в команды
  проверки по фиче.

**Имена**
- Заводятся этой задачей: `excel_changes_export` (модуль),
  `build_changes_export`, `LEFT_COLUMNS`, `STAGE_COLUMNS`, `TAIL_COLUMNS`,
  `TEXT_NO_AMOUNT`, `TEXT_NO_PRICE`, `TEXT_MIX_INCOMPLETE`, `TEXT_UNKNOWN_VAT`.
- Существуют, проверено `grep`-ом: `workbook_bytes`, `write_cell`,
  `write_banner`, `write_column_headers`, `apply_column_widths`, `safe_str`,
  `fill`, `font`, `align`, `BORDER`, `FMT_MONEY`, `FMT_QTY`, `C_COL_BG`,
  `C_TOTAL_BG` (`backend/services/excel.py`); `load_workbook` (`openpyxl`).

**Проверка**
- `just test-unit-k changes_export_sheet` — зелёная. ДО задачи команда выбирает
  **0** тестов; ПОСЛЕ — не меньше **16** (по числу утверждений задачи). Точное
  число называет отчёт о задаче.
- `just test-unit-k changes_export` — зелёная; ДО — число, названное отчётом
  задачи 2, ПОСЛЕ — оно же плюс тесты этой задачи. Совпадение чисел здесь было
  бы сигналом: новый файл обязан попадать в тот же выбор.
- `just lint-backend` — зелёная.

---

### Task 5: маршрут, кнопка, справочник

**Files**
- Edit: `backend/routers/tenders.py`
- Edit: `frontend/src/services/api/domain.ts`
- Edit: `frontend/src/services/queries.ts`
- Edit: `frontend/src/pages/tenders/TenderCardPage.tsx`
- Edit: `docs/reference/screens.md`
- Test: `backend/tests/integration/test_changes_export_api.py`
- Test: `frontend/src/pages/tenders/TenderCardPage.test.tsx`
- Test: `frontend/src/services/queries.tenders.test.tsx`

**Interfaces**
- Потребляет: `responses.xlsx_response`, `responses.safe_filename_part`
  (заводятся задачей 1); `crud.changes_export.load_book` (задача 3);
  `services.excel_changes_export.build_changes_export` (задача 4);
  `routers.domain_errors.raise_domain_error` (существует); `database.get_db`
  (существует); `tendersApi` (`frontend/src/services/api/domain.ts`,
  существует); `saveBlob`, `toastReportError` (`frontend/src/services/queries.ts`,
  существуют).
- Производит:

```
GET /api/v1/tenders/{tender_id}/changes-export
200 → xlsx, Content-Disposition: attachment; filename*=UTF-8''…
422 → { detail: { code: "no_comparable_participants", message: "…" } }
404 → { detail: { code: "tender_not_found", message: "…" } }
```

```python
@router.get("/{tender_id}/changes-export")
def changes_export(tender_id: int, db: Session = Depends(get_db)) -> Response
```

```ts
// frontend/src/services/api/domain.ts — в объекте tendersApi
changesExport: (tenderId: number) => Promise<Blob>

// frontend/src/services/queries.ts
export function useTenderChangesExport(): UseMutationResult<Blob, unknown, number>
```

**Утверждения**
- маршрут — `GET /api/v1/tenders/{tender_id}/changes-export`; тело ответа
  `200` — байты xlsx с media-type
  `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`;
- имя файла в `Content-Disposition` собирается из номера тендера, прогнанного
  через `safe_filename_part`, и уезжает формой `filename*=UTF-8''…`: номер вида
  «12/2025» не ломает путь, а кириллица не искажается;
- читать выгрузку вправе и `admin`, и `member`; неаутентифицированный запрос
  отвергается тем же механизмом, что остальные маршруты тендеров;
- тендера нет → `404` с `detail.code = "tender_not_found"`; участников с двумя
  и более сметами нет → `422` с `detail.code = "no_comparable_participants"`, и
  тело в обоих случаях — объект, а не строка;
- листов в книге столько, сколько участников с двумя и более сметами: на входе
  из семи участников, у шестерых из которых по два и более раунда, книга несёт
  шесть листов;
- имена листов очищены и обрезаны до 31 знака, совпадения разведены —
  закреплено входом с участником, чьё наименование длиннее 31 знака, и двумя
  участниками с одинаковым 31-знаковым префиксом;
- ответ отдаётся `bytes`, не `StreamingResponse` с файловым объектом;
- эндпоинт зовёт `load_book` и `build_changes_export` и собственной арифметики
  не содержит: правило, посчитанное вторым экземпляром, разошлось бы с первым;
- кнопка «Изменения КП» стоит на карточке тендера рядом с кнопкой свода по
  этапам и НЕ зависит от выбора этапов на решётке: книга собирается по всем
  участникам (спека §2.1);
- отказ `422` показывается человеку сообщением сервера, а не «Request failed
  with status code 422»: блоб-ответ разбирается тем же путём, что у трёх
  существующих выгрузок;
- `docs/reference/screens.md` §8 получает описание кнопки и состава книги —
  этим же коммитом, потому что сегодня кнопка появляется; заголовок `## 8.
  Тендеры` не меняется, и `just check-agents-index` остаётся 18 из 18.

**Имена**
- Заводятся этой задачей: `changes_export` (хендлер `backend/routers/tenders.py`),
  `changesExport` (метод `tendersApi`), `useTenderChangesExport`.
- Существуют, проверено `grep`-ом: `router` (prefix `/api/v1/tenders`),
  `get_db`, `raise_domain_error`, `DomainError`, `decimal_json`
  (`backend/routers/tenders.py` и его импорты); `tendersApi`
  (`frontend/src/services/api/domain.ts`); `saveBlob`, `toastReportError`,
  `reportErrorMessage` (`frontend/src/services/queries.ts`); `PageHeader`,
  `Button`, `Surface` (`frontend/src/components/ui-domain/`,
  `frontend/src/components/ui/`); `summaryHref`, `selectedOfferIds`
  (`frontend/src/pages/tenders/TenderCardPage.tsx`).

**Проверка**
- `just test-int-local-k changes_export_api` — зелёная. ДО задачи команда
  выбирает **0** тестов; ПОСЛЕ — не меньше **9** (утверждения задачи A5.1–A5.8 и A5.11;
  A5.9 и A5.10 предъявляет фронт). Точное число называет отчёт о задаче.
- `just test-int-local-k changes_export` — зелёная; ДО — число, названное
  отчётом задачи 3, ПОСЛЕ — оно же плюс тесты этой задачи.
- `just test-frontend-file src/pages/tenders/TenderCardPage.test.tsx` —
  зелёная. ДО — **22** теста, ПОСЛЕ — не меньше **24**.
- `just test-frontend-file src/services/queries.tenders.test.tsx` — зелёная.
  ДО — **26** тестов, ПОСЛЕ — не меньше **28**.
- `just check-agents-index` — зелёная, 18 из 18.
- `just ci` — зелёная перед пушем (DoD 18).

---

### Task 6: devlog и дорожная карта (DoD 17)

**Prerequisite СНЯТ 18.09.2026.** Фича переноса карты под гит прошла свой цикл
и смержена (PR #51, `main` = `55f8465`): карта лежит как
`docs/product-roadmap.md`, `tasks/ROADMAP.md` удалён, `AGENTS.md` дошёл до v6.23
тринадцатой строкой §9.2 — класс «Дорожная карта», — а страж вырос с 17 проверок
до **18**. Ветка этой фичи перебазирована на `55f8465` тем же днём; интерфейс
задачи ниже больше не заглушка, а фактический путь.

Правило, которое задача исполняет, теперь записано в `AGENTS.md` §9.2 дословно:
**«Карту пересматривает фича, закрывшая или сдвинувшая пункт, — в её же
ветке.»** До переноса оно требовало невозможного — карта была под `.gitignore`,
и коммитом ветки её правка быть не могла.

**Files**
- Create: `docs/devlog/2026-09-17-tender-changes-export.md`
- Edit: `docs/product-roadmap.md`

**Interfaces**
- Потребляет: спеку и этот план (ссылками), отчёты задач 1–5 (замеры).
- Производит: devlog фичи и правки карты — **поимённо, без счётчика**: пункт А3
  (основной), пункт А3 в очереди «Если начинать завтра», зависимость Б1 → А3 в
  «Что стоит на чём», замеры пункта А1, раздел «Долги, которые карта закрывает
  по пути», строка «Состояние на …» и строка «Источники». Счётчик «две правки»
  стоял здесь до третьего круга ревью и был неверен уже тогда — перечисление
  не даёт соврать молча.
- НЕ производит в служебных строках карты: **SHA, номер текущей версии
  документа, глобальный числовой максимум и закрытый период с конечной датой.**
  Запрет именно на эти четыре формы, а не на числа вообще: задача обязана
  занести замер `5 970` (A6.5), дату состояния (A6.7) и точные номера долгов
  (A6.6, A6.8), и абсолютный запрет отменял бы их. Граница проходит по тому,
  о чём число говорит. **Замер и дата описывают МОМЕНТ** — «на такую-то дату
  было столько» верно навсегда, и стареет не запись, а её полезность.
  **SHA, версия, максимум и закрытый период описывают СЕЙЧАС** — и становятся
  ложью молча, от чужого коммита, не тронувшего карту ни байтом.

**Утверждения**
- devlog называет тронутые области и прочитанные по ним файлы граблей
  `docs/pitfalls/` (`AGENTS.md` §10): фича трогает бэкенд, БД (чтением),
  поверхности, фронтенд и прогоны;
- devlog несёт метрику фазы предъявления на уровне фичи: число
  `behavioral`-элементов по задачам, идентификаторы пропусков, найденных
  финальным либо внешним кругом, и стоимость
  (`docs/process/implementation.md`, «Обязанности оркестратора»);
- пункт **А3** карты («Excel-выгрузка свода по этапам, лист на участника»)
  переписан под то, чем фича оказалась: не зеркало экрана свода по статьям, а
  попозиционный протокол (спека, преамбула и §3), и оформлен по правилу 1 самой
  карты — **ссылкой со словом «сделано»**, а не вычеркнутым абзацем;
- **все упоминания `А3` пройдены инструментом, а не по памяти**
  (`docs/insights/walk-dependents-with-a-tool.md`): `grep` по карте даёт ТРИ
  места, и каждое получает свой исход — основной пункт (строка 123) уходит по
  правилу 1; очередь «Если начинать завтра» (строка 289) больше не советует
  брать сделанное; зависимость «Б1 сравнение участников → А3 полезно» (строка
  256) либо помечена исполненной, либо СОХРАНЕНА с явным обоснованием, почему
  она остаётся живой. Молчаливо не остаётся ни одно;
- замеры пункта **А1** карты пересняты: каталог стенда вырос с 5 234 до 5 970
  строк `TO_REVIEW`, и прежние числа устарели;
- долги 31–34 внесены в раздел карты **«Долги, которые карта закрывает по
  пути»** — он существует и сегодня несёт пять строк — с честными владельцами и
  НЕ объявлены закрытыми этой выгрузкой: №31 — старые отчёты, №32 — валидация
  нормативов, №33 — паспорт фазы 6, №34 маршрутизирован в А1;
- **строка «Состояние на 09.09.2026, `main caa10e0`» обновлена, и SHA из неё
  УХОДИТ.** Спека переноса §4 оставила строку устаревшей намеренно и назвала
  правщиком именно эту ветку — без утверждения здесь она законно пережила бы
  реализацию. Но починить её подстановкой любого нового SHA нельзя: на момент
  написания devlog назвать можно только базу (`55f8465` — там А3 ещё НЕ
  сделана, и строка соврала бы о состоянии) либо HEAD ветки (его обесценивает
  сам devlog-коммит, следующий за ним), а SHA мержа ещё не существует. Поэтому
  строка называет **дату и фичу, без коммита** — форма, которой карта уже
  пользуется в пункте А1 («фича „правило цены“ 11.09.2026, ревизия v6.21»), и
  которая не может протухнуть (`docs/insights/claims-about-the-branch-need-merge-tense.md`);
- **строка «Источники» в конце карты переведена в НЕПРОТУХАЮЩУЮ форму**, а не
  пересняты её числа. Внесение долгов 31–34 делает её ложной прямо в том же
  коммите, но замена `v6.20` на `v6.23` и `1–27` на сегодняшний максимум
  протухнет от следующей же ревизии или следующего долга — это починка
  перечислением (`docs/insights/enumerative-hardening-does-not-converge.md`).
  Движущихся снимков в строке ТРИ, и каждый чинится своим способом: у
  `AGENTS.md` снимается номер версии — адреса `§1, §3, §7` устойчивы сами по
  себе; у `TECH_DEBT.md` глобальный максимум заменяется ТОЧНЫМ набором записей,
  на которые карта реально опирается, и набор сверяется `grep`-ом по самой
  карте, а не пишется по памяти; период «история мержей 07.08–09.09.2026»
  **убирается либо теряет конечную дату** — и ровно один из двух исходов, без
  «либо обновить»: свежий закрытый диапазон — тот же протухающий снимок, только
  моложе, и он нарушил бы непротухающую форму, ради которой правится строка.
  Остальные два элемента строки — названные предложения и список спек — не
  текут и не трогаются;
- пункт карты, который фича закрыла либо сдвинула, пересмотрен В ЭТОЙ ЖЕ ВЕТКЕ,
  как требует §9.2: коммит devlog и коммит правки карты лежат в
  `feat/tender-changes-export`, а не в следующей;
- утверждения devlog о составе ветки записаны во времени ПОСЛЕ мержа
  (`docs/insights/claims-about-the-branch-need-merge-tense.md`).

**Имена**
- Заводятся этой задачей: `docs/devlog/2026-09-17-tender-changes-export.md`.
- Существуют, проверено `grep`-ом: `docs/TECH_DEBT.md` разделы 31, 32, 33, 34;
  `docs/product-roadmap.md` (под гитом с PR #51), его пункты `А1` (строка 95) и
  `А3` (строки 123, 256, 289 — три упоминания), разделы «Долги, которые карта
  закрывает по пути» (строка 261), «Что стоит на чём» (строка 248), «Если
  начинать завтра» (строка 285), строка «Состояние на …» (строка 13) и строка
  «Источники» (строка 296); правило 1 карты «ссылка со словом „сделано“»;
  строка класса «Дорожная карта» в таблице §9.2 `AGENTS.md`.
- Номера строк выше — адреса НА МОМЕНТ ГЕЙТА 3, сверенные построчно; задача 6
  правит эти же места и номера сдвинет сама. Опознавать их надо по ТЕКСТУ,
  который назван рядом, а разъехавшийся номер дефектом не является.

**Проверка**
- `just check-agents-index` — зелёная, 18 из 18. ДО и ПОСЛЕ задачи команда
  выбирает одни и те же **18** проверок: задача не заводит ни одной новой, и
  совпадение здесь ожидаемо (правка документации, не кода).
- `just ci` — зелёная перед пушем.

---

## Команды проверки

- По задаче: указаны в самой задаче, вместе с выбором ДО и ПОСЛЕ
  (`docs/process/implementation.md`, «Выбор команды проверки»).
- По фиче целиком:
  - `just ci` (§9.3) — перед пушем, целиком;
  - `just test-backend-parallel` — полный backend-suite: ДО фичи он выбирает
    **2810** тестов, ПОСЛЕ — больше на сумму тестов задач 1–5;
  - шесть команд обвязки спеки из её §6, из каталога
    `docs/superpowers/specs/2026-09-16-tender-changes-export/`, буквально;
    пять из шести требуют стенда `gca_dev` на порту 5459
    (`just pg-test-start` поднимает кластер), `check_diagnostic_branches.py`
    базы не касается. Обязателен из них `python check_spec_numbers.py` —
    DoD 16: **33** выбранных замера по-прежнему считаются базой и стоят в спеке
    в одном абзаце со своей якорной фразой;
  - прогон на стенде: кнопка на карточке тендера 449-ТУ отдаёт книгу из шести
    листов (DoD 1);
  - **ручной замер в Excel** — отдельная проверка, а не следствие предыдущей:
    скачанная книга открывается настоящим Excel'ом БЕЗ окна восстановления, в
    ней работает автофильтр, а `СУММ` по колонке суммы даёт число (DoD 13).
    Прогоном это ненаблюдаемо: `openpyxl` — другой разборщик, и его зелень
    говорит только о структуре файла
    (`docs/insights/unobservable-in-the-runner.md`).
