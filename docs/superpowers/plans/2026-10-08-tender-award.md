# План: Б2 «Победитель тендера и договор из КП»

**Спека:** `docs/superpowers/specs/2026-10-08-tender-award-design.md` (гейт 2 закрыт 08.10.2026, два круга Codex, редакция `7f66a6d`)
**Ветка:** `feat/tender-award`, черновой PR #65
**Макет плана:** [`2026-10-08-tender-award/mockup.html`](2026-10-08-tender-award/mockup.html) — макет гейта 1, уточнённый спекой; правки против гейта 1 перечислены в его начале и обведены. Экраны фронта (Task 8, 9) и сверка на стенде (Task 11) идут по нему.

> Исполнителю: задачи идут снизу вверх и по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`) и ревью задачи
> (`docs/process/implementation.md`) до следующей. Адреса `§N` без уточнения —
> разделы спеки; «решение N» — пронумерованные решения спеки в её начале.
> Числа проверок — накопительные нижние границы: точное ПОСЛЕ неизвестно до
> написания тестов. ДО сняты 08.10.2026 на `fec2f74` командой
> `uv run pytest <каталог> --collect-only -q -k <шаблон>` (бэкенд) и
> `npx vitest list <каталог>` (фронт).

## Global Constraints

- **Права**: все команды отметки, договора из КП, привязки и отвязки — только
  `admin` (`Depends(require_admin)`), `member` получает `403`; чтение карточек и
  кандидатов — любой вошедший (`AGENTS.md` §3, §2.4). Тест-сторож
  `tests/test_auth_coverage.py` зелёный после каждой задачи с маршрутами.
- **Порядок замков во всей фиче**: тендер → раунды (по `id`) → участник →
  отметка → договор. Команды стороны договора (отвязать, правка, удаление)
  тендер не блокируют. Замок строки — с `populate_existing`. Повышение режима
  замка внутри транзакции запрещено.
- **Каждый запрет — дважды**: проверка команды под замком (текст с
  подстановками, код §2.7) и ограничение схемы, переведённое
  `translating_integrity` парой `(код, текст)` без подстановок (решение 14).
  Отказ через ключ обязан нести тот же код, что синхронный.
- **Существующие вызовы `translating_integrity`** не меняются ни символом; их
  ответы прежние (`code=None`).
- **Транзакционная модель импорта** (`AGENTS.md` §5) не меняется: сессия A —
  статусы и `parsed_data`, сессия B — домен, матчинг, членства, сверка, `done`
  одной транзакцией. Копия КП — та же сессия B.
- **Файлы**: новый ключ копии удаляется на любом пути, где задание не
  закоммичено (`AGENTS.md` §5 шаг 1); старый файл этапа не трогается никогда.
- **Деньги** — `Decimal`/строки в JSON; итоги — только
  `estimate_total_including_vat`/`estimate_totals_including_vat` (правило
  единогласия), никаких `float`.
- **Не трогаются**: парсер, каскад матчинга, каталог и семантический контур,
  аналитика (`crud/analytics.py`, `crud/reports.py`, `crud/project_passport.py`),
  `delete_contract`, замена и загрузка сметы договора, ключи существующих
  ответов API (только добавление).
- **Фронтенд** — только shadcn/ui; недостающее — `npx shadcn add`.
- **Номер ревизии `AGENTS.md`** называется только в коммите ревизии (Task 10):
  страж (проверка 5) краснеет на необъявленную версию.
- **Стенд**: разрушающие шаги приёмки — только на копии `gca_dev_b2` (Task 11).

## Review Focus

Входы, которые спека подразумевает, а задачи легко пропустить; тест на каждый
стоит в задаче-владельце:

1. **У этапа нет «текущего задания»** — после отметки удалён другой участник
   (без отметок), `current_round_job` возвращает `None`; копия КП всё равно
   создаётся из задания КП победителя (Task 6).
2. **ИНН карточки победителя исправлен или обменян с соседом** — в копию
   попадает исходное КП (Task 6).
3. **Дата «договор не заключён» раньше даты записи отметки** — история
   упорядочена по `id` отметки, а не по датам (Task 3).
4. **Повторная отметка того же участника после «договор не заключён»** —
   разрешена, история показывает обе (Task 3).
5. **Договор с допсоглашением** — пометка считается только по основной смете;
   допсоглашение без основной сметы даёт `no_estimate` (Task 5).
6. **`PATCH` связанного договора с неизменёнными объектом и подрядчиком** —
   проходит; меняется только запрошенный реквизит (Task 5).
7. **Отвязка договора без сметы** (импорт копии упал) — разрешена; при
   активном импорте — отказ (Task 5).
8. **Удаление тендера с историей отметок без договора** — проходит, отметок
   не остаётся; с договором по любой отметке — отказ (Task 4).

## Структура файлов

```
backend/
  alembic/versions/2026_10_08_0020-tender_awards.py   создаётся: tender_awards, колонки, составные ключи (§2.2)
  models.py                         правка: TenderAward, колонки Contract/Estimate/ImportJob, CK_* константы
  crud/common.py                    правка: translating_integrity принимает пару (код, текст)
  crud/tender_awards.py             создаётся: отметить, снять, не заключён, кандидаты, привязать, договор из КП, фрагмент карточки, охранные проверки
  crud/tenders.py                   правка: get_tender_card (+award, award_history), create_round, delete_round, delete_tender, delete_participant, participant_deletion_preview
  crud/contracts.py                 правка: get_contract_dict (+tender_basis, estimate_origin), update_contract, unlink_tender_award
  services/round_import.py          правка: kp_inn_of, projection_for_inn, проверка отметок в import_round
  services/import_owners.py         правка: EstimateOwner.source_award_id, contract_estimate_owner(..., source_award_id)
  services/import_pipeline.py       правка: JobContext.source_award_id, ветка копии КП
  routers/tenders.py                правка: маршруты отметки, договора из КП; проверка отметок в upload_round
  routers/contracts.py              правка: DELETE /contracts/{id}/tender-award
  tests/conftest.py                 правка: _DOMAIN_TABLES += tender_awards
  tests/unit/test_tender_award_integrity.py              создаётся
  tests/integration/test_tender_award_*.py               создаются (schema, commands, deletions, contract_side, from_kp, api)
frontend/src/
  components/ui/radio-group.tsx     создаётся: npx shadcn add radio-group
  components/tenders/WinnerBanner.tsx, NotConcludedDialog.tsx, RemoveAwardDialog.tsx, LinkContractDialog.tsx   создаются
  components/tenders/OfferGrid.tsx  правка: меню ячейки финального этапа, подсветка победителя
  pages/tenders/TenderCardPage.tsx  правка: плашка, «Новый этап» при отметке, окно «Создать договор»
  components/contracts/TenderBasisRow.tsx, UnlinkTenderDialog.tsx   создаются
  components/contracts/ContractFormDialog.tsx   правка: режим «из отметки», запертые стороны у связанного договора
  pages/contracts/ContractCardPage.tsx           правка: строка «Основание», отвязка
  services/api/domain.ts, services/queries.ts, services/queryKeys.ts, types/domain.ts, test/handlers.ts   правка
  *.test.tsx рядом с компонентами   создаются/правятся
docs/reference/schema.md            правка: блок tender_awards и три колонки (Task 1)
docs/reference/screens.md           правка: экраны 1 и 8 (Task 10)
docs/reference/money-axes.md        правка: раздел «Итоги файла на карточках тендера и договора» (Task 10)
docs/proposals/2026-08-25-tenders-model.md            правка: указатели на спеку Б2 (Task 10)
docs/proposals/2026-10-07-variant-rate-spread-check.md правка: договорная совокупность (Task 10)
AGENTS.md, docs/AGENTS-revisions.md правка: §3, §5, преамбула; архив действующей врезки (Task 10)
docs/product-roadmap.md             правка: Б2 закрыт (Task 11)
docs/devlog/2026-10-08-tender-award.md   создаётся (Task 11)
docs/superpowers/plans/2026-10-08-tender-award/mockup.html   макет плана (создан с планом)
```

## Решения плана, которых нет в спеке

1. **Команды отметки — в новом модуле `crud/tender_awards.py`**, а не в
   `crud/tenders.py` (451 строка, удаления и решётка). Охранные проверки
   (`refuse_if_round_has_award`, `refuse_if_package_has_award`,
   `refuse_if_active_award`) живут там же, а зовут их команды контура —
   правило «где отметка запрещает» описано в одном файле.
2. **Опознание КП — в `services/round_import.py`** рядом с
   `split_round_payload`: `kp_inn_of` (снятие ИНН с разбора КП при отметке) и
   `projection_for_inn` (выбор проекции при импорте копии) — одна логика ИНН
   файла на оба конца.
3. **Все новые тесты — в файлах `test_tender_award_*.py`**: команда
   `-k tender_award` выбирает их все (ДО — 0 и в `tests/unit`, и в
   `tests/integration`, проверено 08.10.2026). Гонки — в задачах-владельцах, а
   не отдельной задачей: каждая гонка стережёт конкретную команду.
4. **`POST …/awards/{aid}/contract` отвечает 202 `{contract, job}`**; фоновую
   задачу ставит роутер, как `upload_estimate`, — `crud` о `BackgroundTasks` не
   знает.
5. **Имя файла задания копии — имя файла задания КП** (`ImportJob.filename`),
   без префиксов: история загрузок договора покажет тот же файл, а
   предупреждение задания (§2.5) называет, что это копия.
6. **`ContractFormDialog` получает режим «из отметки»** вместо нового окна: те
   же поля и та же проверка ввода; объект, подрядчик и класс в режиме только
   показаны. Запертые стороны у связанного договора — тот же механизм
   (`lockedParties`), что и в режиме «из отметки».
7. **Порядок задач**: схема (1) → переводчик (2) → команды отметки (3) →
   удаления и замена этапа (4) → сторона договора (5) → договор из КП (6) →
   API (7) → фронт тендера (8) → фронт договора (9) → документация и ревизия
   (10) → стенд и финал (11). API после всех команд — маршруты одним
   набором и один прогон сторожа прав.

## Задачи

### Task 1: схема — миграция `0020`, модели, справочник

**Files**
- Create: `backend/alembic/versions/2026_10_08_0020-tender_awards.py`
- Edit: `backend/models.py`, `backend/tests/conftest.py`, `docs/reference/schema.md`
- Test: `backend/tests/integration/test_tender_award_schema.py`

**Interfaces**
- Потребляет: `Base`, `Tender`, `TenderRound`, `OfferPackage`, `Offer`, `Estimate`, `Contract`, `ImportJob`, `User`, `CONTRACTOR_INN_CANONICAL`, `_DOMAIN_TABLES` (существуют).
- Производит:

```python
TENDER_AWARD_NOT_CONCLUDED: str        # выражение ck_tender_awards_not_concluded (§2.2)
TENDER_AWARD_NOTE_NOT_BLANK: str
TENDER_AWARD_KP_INN_CANONICAL: str     # "kp_inn ~ '^[0-9]+$'"
ESTIMATE_SOURCE_AWARD_ORIGINAL: str    # ck_estimates_source_award
IMPORT_JOB_SOURCE_AWARD_ORIGINAL: str  # ck_import_jobs_source_award

class TenderAward(Base): ...           # "tender_awards", колонки и ограничения §2.2
# Contract.tender_award_id, Estimate.source_award_id, ImportJob.source_award_id
# новые UNIQUE: uq_tenders_id_object, uq_offer_packages_id_contractor,
#   uq_offers_id_tender_package, uq_estimates_id_offer,
#   uq_contracts_tender_award, uq_contracts_id_tender_award
```

**Утверждения**
- `alembic upgrade head` и `downgrade -1` проходят на пустой базе и на базе с
  тендерами, сметами и договорами без отметок; после `upgrade` новые колонки
  `NULL` у всех строк;
- сценарии §1.3 на настоящей схеме, по тесту на каждый, — по одному входу на
  ограничение, с тем исходом, что в таблице §1.3: S1 и S2b проходят и не
  оставляют отметок; S2 падает на `fk_tender_awards_offer`; S3, S4 —
  `fk_tender_awards_offer`; S3b, S5b, S8c, S10b, S11 проходят; S5 —
  `fk_tender_awards_kp_estimate`; S6, S7, S8a, S8b, S9, S9b —
  `fk_contracts_tender_award`; S10, S12, S12b — `fk_estimates_source_award`;
  S13 — `ck_estimates_source_award`; S14 — `uq_tender_awards_active`; S14b —
  `fk_tender_awards_kp_estimate`; S14c — `fk_tender_awards_package`; имя
  ограничения проверяется по `diag.constraint_name`, а не подстрокой текста;
- `ck_tender_awards_not_concluded` отвергает каждое из трёх обязательных полей
  «не заключён», заполненное без остальных двух, и комментарий без даты (по
  входу на поле), пропускает отметку без всех четырёх и запись со всеми тремя
  без комментария; `ck_tender_awards_note_not_blank` отвергает пустую и
  пробельную строку; `ck_tender_awards_kp_inn_canonical` отвергает ИНН с
  пробелом и с буквой;
- `ck_import_jobs_source_award` отвергает `source_award_id` у задания раунда и
  у допсоглашения; удаление отметки обнуляет `import_jobs.source_award_id`
  (`SET NULL`), а не отказывает;
- выражения пяти констант `CK_`/`*_ORIGINAL` в миграции равны модели — против
  независимого литерала в тесте;
- `tender_awards` в `_DOMAIN_TABLES`; блок схемы в `docs/reference/schema.md`
  §1 в форме соседних блоков.

**Имена**
- Заводятся: `TenderAward`, пять констант выше, миграция `0020`.
- Существуют, проверено `grep`-ом: `CONTRACTOR_INN_CANONICAL` (`models.py:326`), `_DOMAIN_TABLES` (`tests/conftest.py:423`), `TenderFactory`, `TenderRoundFactory`, `OfferPackageFactory`, `OfferFactory`, `EstimateFactory`, `ContractFactory`, `ImportJobFactory` (`tests/factories.py`).

**Проверка**
- `just test-int-local-k tender_award` — ДО 0, ПОСЛЕ ≥ 35.
- `just test-int-local-k "schema_constraints or tenders_schema"` — ДО 178, ПОСЛЕ ≥ 178.
- `just check-agents-index` — 18 из 18.

### Task 2: переводчик нарушений с кодом

**Files**
- Edit: `backend/crud/common.py`
- Test: `backend/tests/unit/test_tender_award_integrity.py`

**Interfaces**
- Потребляет: `DomainError`, `translating_integrity` (существуют).
- Производит:

```python
IntegrityMessage = str | tuple[str, str]   # текст | (код, текст)

@contextmanager
def translating_integrity(db: Session, messages: Mapping[str, IntegrityMessage]) -> Iterator[None]
```

**Утверждения**
- значение-строка даёт `DomainError(409, текст)` с `code is None` и тем же
  `detail`, что до задачи;
- значение-пара `(код, текст)` даёт `DomainError(409, текст, code=код)`;
- чужое ограничение (нет в словаре) пробрасывает `IntegrityError` как раньше;
  сессия откатывается во всех трёх случаях.

**Имена**
- Заводятся: `IntegrityMessage`.
- Существуют, проверено `grep`-ом: `translating_integrity` (`crud/common.py:122`), `DomainError` (`crud/common.py:26`).

**Проверка**
- `just test-unit-k tender_award` — ДО 0, ПОСЛЕ ≥ 3.
- `just test-unit-k "common or integrity"` — ДО 14, ПОСЛЕ ≥ 14.
- `just test-int-local-k contract` — ДО 284, ПОСЛЕ ≥ 284.

### Task 3: команды отметки, фрагмент карточки тендера, новый этап

**Files**
- Create: `backend/crud/tender_awards.py`
- Edit: `backend/crud/tenders.py` (`get_tender_card`, `create_round`), `backend/services/round_import.py` (`kp_inn_of`)
- Test: `backend/tests/integration/test_tender_award_commands.py`

**Interfaces**
- Потребляет: `_lock_tender`, `_refuse_if_active`, `get_tender_card`, `translating_integrity`, `estimate_total_including_vat`, `canonicalize_inn`, `TenderAward` (Task 1).
- Производит:

```python
# services/round_import.py
def kp_inn_of(raw_data: dict[str, Any]) -> str | None
    # канонические ИНН блоков предложений всех лотов; ровно одно непустое — оно, иначе None

# crud/tender_awards.py
def award_winner(db: Session, tender_id: int, *, offer_id: int, user_id: int) -> dict
def remove_award(db: Session, tender_id: int, award_id: int) -> dict
def mark_not_concluded(db: Session, tender_id: int, award_id: int, *,
                       not_concluded_on: dt.date, note: str | None, user_id: int) -> dict
def contract_candidates(db: Session, tender_id: int, award_id: int) -> list[dict]
def link_contract(db: Session, tender_id: int, award_id: int, *, contract_id: int) -> dict
def award_card_fragment(db: Session, tender_id: int) -> dict   # {"award": …, "award_history": […]}
def refuse_if_active_award(db: Session, tender_id: int) -> None
# команды отметки возвращают get_tender_card; формы award и award_history — §2.6
```

**Утверждения**
- `award_winner` на КП финального этапа создаёт действующую отметку: `estimate_id`
  — смета оферты, `kp_inn` — `kp_inn_of` её разбора, `object_id`/`contractor_id`
  — тендера и участника, `awarded_by` — пользователь; отказы с неизменённым
  состоянием и своим кодом §2.7: оферта не финального этапа —
  `award_not_final_stage`; оферта без сметы — `award_no_kp`; активное задание
  раунда — `active_import`; действующая отметка есть — `award_exists` (текст
  называет текущего победителя); разбор КП без ИНН и с двумя разными ИНН —
  `award_kp_unidentified`; оферта чужого тендера — 404;
- `kp_inn_of`: один ИНН в разных записях (`"77 0000 0001"` и `"7700000001"`)
  даёт одно значение; два разных — `None`; ни одного — `None`;
- после правки ИНН карточки участника до отметки `kp_inn` равен ИНН файла, а не
  карточки;
- `remove_award` удаляет действующую отметку без следа (в `award_history` её
  событий нет); отказы: не действующая — `award_not_active`; есть договор —
  `award_has_contract` с номером договора;
- `mark_not_concluded` пишет дату, комментарий (пустой и пробельный — `NULL`),
  автора и момент записи; отказы — те же два, что у снятия; после неё
  отметить другого участника и того же участника можно;
- `award_history` упорядочена по `id` отметки, «отмечен» раньше «не заключён»,
  в том числе когда дата «не заключён» раньше `awarded_at`; у события
  `awarded` действующей отметки `is_active = true`, у прочих `false`;
- `award` в карточке — `null` без действующей отметки; `total_including_vat`
  — строка итога КП правилом единогласия либо `null`; `contract` — `null` либо
  номер и дата договора по отметке;
- `contract_candidates` возвращает договоры того же объекта и подрядчика без
  основания, по `signed_date` убыванием; договор другого объекта, другого
  подрядчика и уже связанный — не возвращает;
- `link_contract` ставит основание; смета договора не меняется; отказы:
  `award_not_active`, `award_has_contract`, `contract_already_linked`,
  `contract_object_mismatch`, `contract_contractor_mismatch`; договор не найден
  — 404;
- `create_round` при действующей отметке — `round_blocked_by_award`; при
  отметке «не заключён» — этап создаётся;
- гонка «новый этап против отметки» (два соединения, барьер после чтения):
  ровно одна из двух команд проходит; снятие `FOR UPDATE` тендера в
  `create_round` даёт этап поверх действующей отметки — тест краснеет;
- нарушение `uq_tender_awards_active` и `fk_contracts_tender_award` мимо
  проверок команды (проверка выключена подменой) даёт тот же код, что
  синхронный отказ.

**Имена**
- Заводятся: функции выше, коды `award_not_final_stage`, `award_no_kp`, `award_exists`, `award_kp_unidentified`, `award_not_active`, `award_has_contract`, `contract_already_linked`, `contract_object_mismatch`, `contract_contractor_mismatch`, `round_blocked_by_award`.
- Существуют, проверено `grep`-ом: `_lock_tender` (`crud/tenders.py:295`), `_refuse_if_active` (`crud/tenders.py:310`), `get_tender_card` (`crud/tenders.py:144`), `create_round` (`crud/tenders.py:270`), `estimate_total_including_vat` (`crud/estimate_totals.py`), `canonicalize_inn` (`utils`), `round_scene` (`tests/integration/conftest.py:599`), `round_payload`, `proposal` (`tests/payloads.py`).

**Проверка**
- `just test-int-local-k tender_award` — ДО ≥ 35, ПОСЛЕ ≥ 70.
- `just test-unit-k tender_award` — ДО ≥ 3, ПОСЛЕ ≥ 6.
- `just test-int-local-k "tenders or round_import or round_unallocated or round_category"` — ДО 212, ПОСЛЕ ≥ 212.

### Task 4: удаления и замена этапа под отметкой

**Files**
- Edit: `backend/crud/tenders.py` (`delete_round`, `delete_tender`, `delete_participant`, `participant_deletion_preview`), `backend/crud/tender_awards.py`, `backend/services/round_import.py` (`import_round`), `backend/routers/tenders.py` (`upload_round`)
- Test: `backend/tests/integration/test_tender_award_deletions.py`

**Interfaces**
- Потребляет: `delete_round`, `delete_tender`, `delete_participant`, `participant_deletion_preview`, `import_round`, `replace_round_estimates`, `upload_round`, `EstimateImportError`, `run_import_job` (существуют).
- Производит:

```python
# crud/tender_awards.py
def refuse_if_round_has_award(db: Session, round_id: int, *, stage_no: int,
                              action: Literal["replace", "delete"]) -> None
def refuse_if_package_has_award(db: Session, package_id: int, *, title: str) -> None
def awards_have_contract(db: Session, tender_id: int) -> str | None   # номер договора или None
# коды: stage_has_award, participant_has_award, tender_has_contract
```

**Утверждения**
- `delete_round` этапа с отметкой — действующей и только «не заключён», по
  входу на каждую — отказ `stage_has_award` с номером этапа, этап и сметы на
  месте; этап без отметок удаляется;
- `delete_participant` и `participant_deletion_preview` участника с отметкой
  (обе разновидности) — отказ `participant_has_award` с именем участника;
  участник без отметок — прежнее поведение (preview, токен, удаление);
- `delete_tender` с историей отметок и без договора удаляет тендер, отметок
  тендера не остаётся; с договором по любой отметке — отказ
  `tender_has_contract` с номером; снятие явного `DELETE tender_awards` (или
  перенос его после `DELETE offers`) роняет удаление тендера с историей
  ошибкой `fk_tender_awards_offer` — тест краснеет;
- загрузка этапа с заменой при отметке на его КП (обе разновидности) —
  `409 stage_has_award` до сохранения файла: файлов в хранилище и заданий не
  прибавилось; этап без отметок заменяется как раньше;
- `import_round` с `replace=True` при отметке на КП этапа — `EstimateImportError`
  с текстом `stage_has_award` до `replace_round_estimates`; задание `error` с
  этим текстом, сметы этапа на месте (не «непредвиденная ошибка»);
- окно между проверкой роутера и созданием задания: роутер проверил «отметок
  нет», отметка закоммичена (активного задания у раунда ещё нет, команда
  отметки его пропускает), задание замены создано. Вход строится без роутера:
  отметка командой, затем задание замены `pending` фабрикой, затем
  `run_import_job(replace=True)`; задание `error` с текстом `stage_has_award`,
  сметы этапа на месте; снятие проверки в `import_round` даёт `error`
  «непредвиденная ошибка» по `fk_tender_awards_kp_estimate` — тест краснеет.

**Имена**
- Заводятся: функции выше, коды `stage_has_award`, `participant_has_award`, `tender_has_contract`.
- Существуют, проверено `grep`-ом: `delete_round` (`crud/tenders.py:343`), `delete_tender` (`:361`), `delete_participant` (`:412`), `participant_deletion_preview` (`:404`), `import_round`, `replace_round_estimates` (`services/round_import.py`), `upload_round` (`routers/tenders.py:367`), `job_env` (`tests/integration/test_import_pipeline.py:54`), `synthetic_round_sheet` (`tests/integration/test_round_import.py:102`).

**Проверка**
- `just test-int-local-k tender_award` — ДО ≥ 70, ПОСЛЕ ≥ 90.
- `just test-int-local-k "tenders or round_import or round_unallocated or round_category"` — ДО 212, ПОСЛЕ ≥ 212.

### Task 5: сторона договора — правка, отвязка, основание и пометка

**Files**
- Edit: `backend/crud/contracts.py` (`get_contract_dict`, `update_contract`, `unlink_tender_award`)
- Test: `backend/tests/integration/test_tender_award_contract_side.py`

**Interfaces**
- Потребляет: `get_contract_dict`, `update_contract`, `_UNIQUE_MESSAGES`, `translating_integrity`, `TenderAward` (Task 1), `link_contract` (Task 3).
- Производит:

```python
EstimateOrigin = Literal["from_offer", "uploaded_separately", "no_estimate"]

def unlink_tender_award(db: Session, contract_id: int) -> dict        # карточка договора
def tender_basis_of(db: Session, contract: Contract) -> dict | None   # форма §2.6
def estimate_origin_of(db: Session, contract: Contract) -> EstimateOrigin | None
# коды: contract_parties_locked, contract_estimate_is_copy, contract_not_linked, contract_import_active
```

**Утверждения**
- карточка договора без основания: `tender_basis` и `estimate_origin` — `null`;
  с основанием — `tender_basis` по форме §2.6 и одно из трёх значений:
  основная смета с `source_award_id` — `from_offer`; без него —
  `uploaded_separately`; основной сметы нет — `no_estimate`, в том числе при
  существующем допсоглашении; допсоглашение со `source_award_id` невозможно
  (Task 1), и пометку допсоглашение не меняет;
- `update_contract` связанного договора: смена `object_id` либо
  `contractor_id` — отказ `contract_parties_locked` с номером тендера, договор
  не изменён; те же значения в теле и смена любого другого реквизита (номер,
  класс, проценты) — проходят; у договора без основания объект и подрядчик
  меняются как раньше;
- гонка «правка против привязки»: правка прочитала договор без основания,
  привязка закоммитилась, правка упирается в `fk_contracts_tender_award` и
  отвечает кодом `contract_parties_locked`, а не 500 и не `code=None`;
- `unlink_tender_award`: договор со сметой `uploaded_separately` и без сметы
  отвязывается — основание `null`, смета не тронута, отметка снова без
  договора; копия КП — отказ `contract_estimate_is_copy`; активное задание
  импорта договора (основной сметы и допсоглашения, по входу) — отказ
  `contract_import_active`; без основания — `contract_not_linked`;
- отвязка мимо проверки команды (подмена) при смете-копии упирается в
  `fk_estimates_source_award` и даёт код `contract_estimate_is_copy`;
- после отвязки объект меняется правкой; повторная привязка того же договора к
  другой отметке того же объекта и подрядчика даёт `uploaded_separately`.

**Имена**
- Заводятся: `EstimateOrigin`, функции и коды выше.
- Существуют, проверено `grep`-ом: `get_contract_dict` (`crud/contracts.py:327`), `update_contract` (`:472`), `_UNIQUE_MESSAGES` (`crud/contracts.py:58`), `ContractFactory`, `EstimateFactory`.

**Проверка**
- `just test-int-local-k tender_award` — ДО ≥ 90, ПОСЛЕ ≥ 108.
- `just test-int-local-k contract` — ДО 284, ПОСЛЕ ≥ 284.

### Task 6: договор из КП — команда и импорт копии

**Files**
- Edit: `backend/crud/tender_awards.py`, `backend/services/round_import.py` (`projection_for_inn`), `backend/services/import_owners.py`, `backend/services/import_pipeline.py`
- Test: `backend/tests/integration/test_tender_award_from_kp.py`

**Interfaces**
- Потребляет: `Storage`, `_resolve_snapshot_rate_class`, `get_contract_dict`, `split_round_payload`, `RoundProjection`, `import_estimate`, `contract_estimate_owner`, `EstimateOwner`, `JobContext`, `load_job_context`, `run_import_job` (существуют), `TenderAward` (Task 1).
- Производит:

```python
# crud/tender_awards.py
@dataclass(frozen=True)
class ContractFromAwardFields:
    contract_number: str
    signed_date: dt.date
    title: str | None
    signer: str | None
    total_amount: Decimal | None
    notes: str | None
    advance_pct: Decimal | None
    advance_note: str | None
    bank_guarantee_pct: Decimal | None
    bank_guarantee_note: str | None
    retention_pct: Decimal | None
    retention_note: str | None

def create_contract_from_award(db: Session, storage: Storage, tender_id: int, award_id: int, *,
                               fields: ContractFromAwardFields) -> tuple[dict, ImportJob]
# код: stage_file_missing

# services/round_import.py
def projection_for_inn(data: dict[str, Any], inn: str) -> RoundProjection | None

# services/import_owners.py
# EstimateOwner.source_award_id: int | None = None
def contract_estimate_owner(contract: Contract, amendment_no: int | None, *,
                            source_award_id: int | None = None) -> EstimateOwner

# services/import_pipeline.py
# JobContext.source_award_id: int | None
```

**Утверждения**
- `create_contract_from_award` при действующей отметке без договора создаёт
  договор (объект, подрядчик — отметки; класс — текущий класс объекта;
  `tender_award_id` — отметка) и задание (`contract_id`, `amendment_no NULL`,
  `filename` и `file_sha256` задания КП, новый `file_key`, `source_award_id`,
  `pending`); в хранилище — новый файл, побайтно равный файлу этапа; старый не
  тронут;
- отказы ничего не создают — ни договора, ни задания, ни файла в хранилище:
  задания КП нет, оно не `done`, файла нет в хранилище — `stage_file_missing`
  (по входу); класс объекта `NULL` — 422 прежним текстом; номер договора занят
  — 409 прежним текстом; `award_not_active`, `award_has_contract`;
- ошибка вставки после сохранения копии (номер договора занят параллельно —
  подмена) удаляет новый файл: число файлов хранилища как до вызова;
- задание КП берётся по `estimates.import_job_id` сметы КП: после удаления
  другого участника этапа (`current_round_job` — `None`) копия создаётся;
- пайплайн на задании копии, этап с тремя участниками (`round_payload` через
  `fake_parse`): в смете договора позиции и цены КП победителя — число позиций
  и итог «с НДС» равны КП, `source_award_id` — отметка, `amendment_no NULL`;
  у задания `done`, `estimates_created = 1` и предупреждение «Смета — копия КП
  участника …»; позиции договора — членства контекстов (`context_members`);
  `parsed_data` — разбор всего файла;
- опознание (решение 13), три теста, каждый — на позициях и ценах КП
  победителя, а не соседа: ИНН карточки победителя исправлен до отметки;
  исправлен после отметки и до создания договора; ИНН карточек победителя и
  соседа обменяны через промежуточное значение после отметки; в первых двух у
  задания есть предупреждение сверки шапки о расхождении ИНН; подмена выбора
  проекции на ИНН карточки роняет первый тест отказом «нет КП» и третий —
  чужими ценами;
- в файле нет блока с `kp_inn` — задание `error` с текстом «В файле этапа N нет
  КП участника … (ИНН …)», договор без сметы, пометка `no_estimate`;
  разрез отказывает — `error` с текстом разреза;
- основание договора не равно `source_award_id` задания (подмена в базе) —
  `error` с текстом §2.5, а не `IntegrityError`;
- `contract_estimate_owner` без `source_award_id` даёт те же колонки сметы, что
  до задачи (паритет `estimate_columns()` договора и допсоглашения);
- обычная загрузка в такой договор того же файла после успешной копии
  возвращает задание копии (правило 1 `AGENTS.md` §5), другого файла с
  `replace=true` — смета `uploaded_separately`.

**Имена**
- Заводятся: `ContractFromAwardFields`, `create_contract_from_award`, `projection_for_inn`, поле `EstimateOwner.source_award_id`, поле `JobContext.source_award_id`, код `stage_file_missing`.
- Существуют, проверено `grep`-ом: `_resolve_snapshot_rate_class` (`crud/contracts.py:391`), `split_round_payload`, `RoundProjection` (`services/round_import.py`), `contract_estimate_owner`, `EstimateOwner` (`services/import_owners.py`), `JobContext`, `load_job_context`, `run_import_job` (`services/import_pipeline.py`), `fake_parse` (`tests/integration/test_import_pipeline.py:43`), `Storage.save`, `Storage.get`, `Storage.exists`, `Storage.delete` (`storage.py`).

**Проверка**
- `just test-int-local-k tender_award` — ДО ≥ 108, ПОСЛЕ ≥ 128.
- `just test-int-local-k import_pipeline` — ДО 35, ПОСЛЕ ≥ 35.
- `just test-int-local-k "tenders or round_import or round_unallocated or round_category"` — ДО 212, ПОСЛЕ ≥ 212.

### Task 7: API

**Files**
- Edit: `backend/routers/tenders.py`, `backend/routers/contracts.py`
- Test: `backend/tests/integration/test_tender_award_api.py`

**Interfaces**
- Потребляет: команды Task 3–6, `require_admin`, `get_current_user`, `raise_domain_error`, `job_response`, `run_import_job`, `BackgroundTasks` (существуют).
- Производит: маршруты таблицы §2.6.

```python
class AwardCreate(BaseModel): offer_id: int
class AwardNotConcluded(BaseModel): not_concluded_on: dt.date; note: str | None = None
class AwardLink(BaseModel): contract_id: int
class ContractFromAward(_MoneyMixin, _PercentMixin): ...   # поля ContractFromAwardFields; float отвергается
```

**Утверждения**
- семь маршрутов §2.6 отвечают статусами таблицы; тела — карточка тендера с
  `award`/`award_history`, карточка договора с `tender_basis`/`estimate_origin`,
  список кандидатов, `{contract, job}` у договора из КП (фоновая задача
  поставлена — задание доходит до `done` в тестовом прогоне);
- `member` на каждый изменяющий маршрут — 403, на кандидатов — 200; аноним —
  401 (сторож `test_auth_coverage.py`);
- отказ команды доходит до клиента своим статусом, `detail` и `code`
  (`routers/domain_errors.py`) — по одному маршруту на каждый код §2.7;
- `ContractFromAward` отвергает `float` в деньгах и процентах, пустой номер —
  422, как `ContractCreate`;
- отметка чужого тендера в пути — 404; `not_concluded_on` без значения — 422;
- ключи списка тендеров и существующие ключи карточек не изменились
  (сравнение множества ключей с ответом до задачи).

**Имена**
- Заводятся: четыре модели тела выше.
- Существуют, проверено `grep`-ом: `require_admin`, `get_current_user` (`auth`), `raise_domain_error` (`routers/domain_errors.py:36`), `job_response` (`routers/estimates.py:74`), `_MoneyMixin`, `_PercentMixin` (`routers/contracts.py`), `admin_client`, `member_client` (`tests/integration/conftest.py`).

**Проверка**
- `just test-int-local-k tender_award` — ДО ≥ 128, ПОСЛЕ ≥ 150.
- `cd backend && uv run pytest tests/test_auth_coverage.py -q` — зелёный.
- `just test-int-local-k "tenders_api or contracts_api"` — ДО 79, ПОСЛЕ ≥ 79.

### Task 8: фронт — карточка тендера

**Files**
- Create: `frontend/src/components/tenders/WinnerBanner.tsx`, `NotConcludedDialog.tsx`, `RemoveAwardDialog.tsx`, `LinkContractDialog.tsx`, `frontend/src/components/ui/radio-group.tsx` (`npx shadcn add radio-group`)
- Edit: `frontend/src/components/tenders/OfferGrid.tsx`, `frontend/src/pages/tenders/TenderCardPage.tsx`, `frontend/src/types/domain.ts`, `frontend/src/services/api/domain.ts`, `frontend/src/services/queries.ts`, `frontend/src/test/handlers.ts`
- Test: `*.test.tsx` рядом с каждым созданным компонентом, `OfferGrid.test.tsx`, `TenderCardPage.test.tsx`

**Interfaces**
- Потребляет: макет плана (экраны 1–6, запреты), `TenderCard`, `useTender`, `queryKeys.tenders`, `queryKeys.contracts`, `ContractFormDialog` (режим — Task 9), shadcn `dropdown-menu`, `dialog`, `alert-dialog`, `badge`, `tooltip`, `textarea`, `input` (существуют).
- Производит:

```ts
interface TenderAward { id; offer_id; package_id; contractor_id; contractor_title; contractor_inn;
  round_id; stage_no; estimate_id; total_including_vat: string | null; awarded_at: string;
  awarded_by_email: string; contract: { id; contract_number; signed_date } | null }
interface TenderAwardEvent { award_id; kind: "awarded" | "not_concluded"; package_id; contractor_title;
  awarded_at: string | null; not_concluded_on: string | null; note: string | null; by_email; is_active }
interface ContractCandidate { id; contract_number; signed_date; object_title; contractor_title;
  base_total_including_vat: string | null }
// TenderCard += award: TenderAward | null; award_history: TenderAwardEvent[]
function useAwardWinner(tenderId: number)
function useRemoveAward(tenderId: number)
function useMarkNotConcluded(tenderId: number)
function useContractCandidates(tenderId: number, awardId: number | undefined)
function useLinkContract(tenderId: number)
```

**Утверждения**
- меню «⋯» с пунктом «Отметить победителем» — у ячеек финального этапа с КП,
  пока действующей отметки нет; у этапов не финальных, у пустой ячейки финала
  и при действующей отметке — меню нет;
- плашка в четырёх состояниях макета: «не отмечен»; отмечен без договора —
  четыре действия; отмечен с договором — «Открыть договор», без «Снять» и «Не
  заключён»; история — только если в ней есть «не заключён», «· действующий»
  у действующей строки;
- сумма плашки — «… млн с НДС»; `null` — «итог недоступен»;
- «Новый этап» недоступен при действующей отметке, с подсказкой; при отметке
  «не заключён» — доступен;
- «Договор не заключён» не отправляется без даты; комментарий пустой уходит
  `null`; «Снять отметку?» — текст экрана 3;
- окно привязки: список кандидатов, выбор одного, «Привязать»; пустой список
  — текст «Нет подходящих договоров»;
- отказ сервера показывается его `detail`;
- после каждой мутации карточка тендера перезапрашивается; после привязки —
  ещё и карточка договора.

**Имена**
- Заводятся: типы, хуки и компоненты выше.
- Существуют, проверено `grep`-ом: `useTender` (`services/queries.ts:1125`), `TenderCard` (`types/domain.ts:282`), `queryKeys.contracts.card` (`services/queryKeys.ts:50`), «Новый этап» (`pages/tenders/TenderCardPage.tsx:264`).

**Проверка**
- `cd frontend && npx vitest run src/components/tenders` — ДО 45, ПОСЛЕ ≥ 62.
- `cd frontend && npx vitest run src/pages/tenders` — ДО 176, ПОСЛЕ ≥ 180.
- `cd frontend && npx tsc --noEmit` и `npx eslint src` — зелёные.

### Task 9: фронт — карточка и форма договора

**Files**
- Create: `frontend/src/components/contracts/TenderBasisRow.tsx`, `UnlinkTenderDialog.tsx`
- Edit: `frontend/src/components/contracts/ContractFormDialog.tsx`, `frontend/src/pages/contracts/ContractCardPage.tsx`, `frontend/src/pages/tenders/TenderCardPage.tsx`, `frontend/src/types/domain.ts`, `frontend/src/services/api/domain.ts`, `frontend/src/services/queries.ts`, `frontend/src/test/handlers.ts`
- Test: `*.test.tsx` рядом с созданными компонентами, `ContractFormDialog.test.tsx`, `ContractCardPage.test.tsx`

**Interfaces**
- Потребляет: макет плана (экраны 5, 7), `ContractCard`, `useContract`, `useUpdateContract`, `ContractFormDialog` (существуют), `TenderAward` (Task 8).
- Производит:

```ts
type EstimateOrigin = "from_offer" | "uploaded_separately" | "no_estimate"
interface TenderBasis { award_id; tender_id; tender_number; tender_title; round_id; stage_no; offer_id }
// ContractCard += tender_basis: TenderBasis | null; estimate_origin: EstimateOrigin | null
// ContractFormDialogProps += award?: TenderAward & { tender_id: number; object_title; rate_class_title };
//                            lockedParties?: { hint: string }
function useCreateContractFromAward(tenderId: number)
function useUnlinkTenderAward()
```

**Утверждения**
- строка «Основание» — ссылка на тендер, «этап N · финал» и пометка из трёх
  текстов дизайна §2.4; без основания строки нет;
- «Отвязать от тендера» — при `uploaded_separately` и `no_estimate`; при
  `from_offer` вместо кнопки — подсказка экрана 7;
- форма в режиме «из отметки»: объект, подрядчик и класс показаны и не
  редактируются, в запрос не уходят; отправка — в команду договора из КП;
  после успеха — переход в карточку договора;
- правка связанного договора: объект и подрядчик недоступны с подсказкой
  «берутся из тендера» и дополнением по пометке (экран 7); прочие поля
  правятся; без основания форма как раньше;
- после отвязки и создания договора перезапрашиваются карточки договора и
  тендера.

**Имена**
- Заводятся: типы, хуки и компоненты выше, пропсы `award`, `lockedParties`.
- Существуют, проверено `grep`-ом: `ContractCard` (`types/domain.ts:138`), `useContract` (`services/queries.ts:361`), `useUpdateContract` (`:397`), `ContractFormDialogProps` (`components/contracts/ContractFormDialog.tsx:37`).

**Проверка**
- `cd frontend && npx vitest run src/components/contracts` — ДО 39, ПОСЛЕ ≥ 47.
- `cd frontend && npx vitest run src/pages/contracts` — ДО 50, ПОСЛЕ ≥ 56.
- `cd frontend && npx vitest run src/pages/tenders src/components/tenders` — зелёный.
- `cd frontend && npx tsc --noEmit` и `npx eslint src` — зелёные.

### Task 10: ревизия `AGENTS.md`, справочник, рамка, предложение

**Files**
- Edit: `AGENTS.md`, `docs/AGENTS-revisions.md`, `docs/reference/screens.md`, `docs/reference/money-axes.md`, `docs/proposals/2026-08-25-tenders-model.md`, `docs/proposals/2026-10-07-variant-rate-spread-check.md`

**Interfaces**
- Потребляет: тексты §2.9, перечень §2.10 (спека).
- Производит: следующую ревизию `AGENTS.md`.

**Утверждения**
- `AGENTS.md`: новый пункт §3 «Основная цена — смета договора», вставка в роли
  `admin` и абзац §5 «Для договора из КП победителя» — дословно по §2.9;
  действующая врезка уехала в архив своим заголовком, заголовок документа и
  новая врезка — следующий номер; врезка называет правленые (§3, §5) и
  нетронутые разделы и справочник; §4, §8 не тронуты ни символом;
- `screens.md` экраны 1 и 8, `money-axes.md` новый раздел — по §2.10;
- рамка: «Вне четвёрки», строки §3.1 о `source_offer_id` и `awarded_offer_id`,
  §3.2 и вопросы §5 пп. 2–4 заменены указателями на спеку; вопрос 5 на месте;
- предложение: §2.2 и §2.3 по §2.10, «Связанное» ссылается на спеку;
- `check_agents_index.py` — 18 из 18.

**Имена**
- Заводятся: нет.
- Существуют, проверено `grep`-ом: `docs/AGENTS-revisions.md`, `EXPECTED_SCREEN_ANCHORS` (`backend/scripts/check_agents_index.py`) — заголовки `## 1.` и `## 8.` в `screens.md` не переименовываются.

**Проверка**
- `just check-agents-index` — 18 из 18.

### Task 11: стенд, приёмка, финал

**Files**
- Create: `docs/devlog/2026-10-08-tender-award.md`
- Edit: `docs/product-roadmap.md`

**Interfaces**
- Потребляет: миграцию `0020`, всё выше.
- Производит: devlog, закрытый пункт Б2.

**Утверждения**
- `gca_dev` на `0020`; приёмка §5 пп. 1–11 пройдена на стенде с id §1.1,
  разрушающие шаги (пп. 6, 9) — на копии `gca_dev_b2`, после приёмки копия
  удалена; итог стенда — тендеры 2, 3, 4 с отметками и договорами 4, 5, 6,
  тендер 6 с отметкой и договором из КП;
- снимки экранов 1–7 на стенде сверены рядом с макетом плана
  (`docs/superpowers/plans/2026-10-08-tender-award/mockup.html`; браузер,
  `playwright-core`, `channel: "chrome"`);
- devlog: что сделано, отступления от плана, замеры приёмки, тронутые области
  и прочитанные файлы граблей (БД, бэкенд, фронтенд, процесс), число
  `behavioral`-элементов по задачам;
- дорожная карта: Б2 закрыт, строки Б3 и Б4 таблицы зависимостей — «Б2
  сделан»;
- `just ci` зелёный перед пушем; описание PR #65 ссылается на дизайн, спеку,
  план и devlog.

**Имена**
- Заводятся: нет.
- Существуют, проверено `grep`-ом: `just ci`, `just check-agents-index`.

**Проверка**
- `just ci` — зелёный.
- `just check-agents-index` — 18 из 18.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: `just ci` (§9.3) и приёмка на стенде (Task 11).
