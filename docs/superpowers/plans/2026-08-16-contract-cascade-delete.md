# Каскадное удаление договора — план реализации

> **Для исполнителя:** задачи идут по порядку, каждая замкнута и заканчивается
> коммитом. Шаги с чекбоксами (`- [ ]`) отмечаются по мере выполнения. Код тестов
> ниже — **проверенный эскиз**: все названные имена (фикстуры, фабрики, функции,
> ключи ответов) сверены `grep`-ом по репозиторию на 2026-08-16; там, где символ
> заводится этой фичей, это сказано прямо.

**Цель:** `admin` удаляет договор вместе со сметами, заданиями импорта и файлами;
удаление сериализовано с загрузкой и не оставляет ни `500`, ни файлов-сирот при
штатной работе.

**Архитектура:** одна транзакция в `crud/contracts.delete_contract` (лок
`FOR UPDATE` → отказ при активном задании → удаление заданий → смет → договора →
commit), возврат `file_key` наверх; роутер удаляет файлы **после** коммита
best-effort. Загрузка учится отличать исчезнувший договор (`404`) от конфликта
активной пары (`409`). Фронтенд получает два входа в один диалог с
подтверждением вводом номера договора.

**Стек:** FastAPI, SQLAlchemy 2.x sync, psycopg3, PostgreSQL 16; React + TS,
TanStack Query, shadcn/ui, vitest, MSW.

**Спека:** [docs/superpowers/specs/2026-08-16-contract-cascade-delete-design.md](../specs/2026-08-16-contract-cascade-delete-design.md)
— план аргументирует от неё, читать обе.

**Ветка:** `feat/contract-cascade-delete` (создана, спека закоммичена — `98f6aba`).

## Global Constraints

- **Миграций у фичи нет.** Схема не меняется: `estimates.contract_id` и
  `import_jobs.contract_id` остаются без `ON DELETE CASCADE` намеренно (спека
  §2.2, §3.2). Если задача потребовала миграцию — это ошибка в задаче.
- **Деньги** — `Decimal` в Python, строки в JSON; новых денежных полей фича не
  вводит.
- **Тесты бэкенда:** каждому новому файлу в `backend/tests/integration/`
  обязателен `pytestmark = pytest.mark.integration` — без него `pytest -m
  integration` молча пропустит файл (грабли фазы 5).
- **Фикстуры с настоящими коммитами** (`committing_client`,
  `committing_session_factory`, `committing_factories`, `tmp_storage`) нужны
  везде, где проверяется видимость между сессиями или файлы на диске;
  транзакционные `client`/`factories` — для остального.
- **`file_key` фабрики `ImportJobFactory` — `key-00000000`, и это НЕ ключ
  хранилища:** `LocalStorage` требует 32 hex-символа (`KEY_PATTERN`), иначе
  `StorageKeyError`. Там, где тест смотрит на файлы, ключ берётся из
  `tmp_storage.save(b"...")` или `storage.new_key()`.
- **Фронтенд гоняется из `frontend/`** (`npx vitest` из корня поднимает чужую
  установку и валит весь набор); типы — `npx tsc -b --noEmit`, голый
  `npx tsc --noEmit` проверяет ноль файлов.
- **Только shadcn/ui**, свои UI-компоненты не писать; `AlertDialog`,
  `DropdownMenu`, `Checkbox` уже лежат в `frontend/src/components/ui/`.
- **prettier в проекте не настроен** — не запускать.
- **Перед пушем `just ci`** (ruff → pytest → eslint → tsc → vitest, шаги по
  отдельности; конвейеры с `| tail` прячут падение).
- Кириллица в выводе дочернего python — `PYTHONIOENCODING=utf-8`.

---

## Файловая карта

| Файл | Что с ним |
|---|---|
| `backend/crud/contracts.py` | переписывается `delete_contract`; правится шапка модуля |
| `backend/models.py` | правится докстринг `ImportJob` |
| `backend/routers/contracts.py` | эндпоинт получает `storage` и удаляет файлы после коммита |
| `backend/services/maintenance.py` | новая `purge_files_best_effort`; правится шапка модуля |
| `backend/routers/estimates.py` | распознавание FK-нарушения → `404` |
| `backend/storage.py`, `backend/routers/import_jobs.py`, `backend/services/estimate_import.py` | правка утверждений «аудит навсегда» |
| `backend/tests/integration/test_contracts_api.py` | два теста отказа заменяются на тесты каскада |
| `backend/tests/integration/test_contract_cascade_delete.py` | **новый**: файлы, порядок, локи, гонка |
| `backend/tests/integration/test_estimates_api.py` | новый тест `404` при исчезнувшем договоре |
| `frontend/src/services/queries.ts` | список инвалидаций `useDeleteContract` |
| `frontend/src/components/contracts/ContractDeleteDialog.tsx` | **новый** диалог |
| `frontend/src/pages/contracts/ContractsPage.tsx` | колонка действий, меню, вызов диалога |
| `frontend/src/pages/contracts/ContractCardPage.tsx` | кнопка и уход на список после удаления |
| `frontend/src/test/handlers.ts` | обработчик `DELETE /api/v1/contracts/:id` |
| `AGENTS.md`, `docs/devlog/2026-08-16-contract-cascade-delete.md` | ревизия v6.7 и devlog |

---

## Task 1: Каскад в CRUD — лок, отказ при активном импорте, возврат ключей

**Files:**
- Modify: `backend/crud/contracts.py` (шапка модуля; `delete_contract`, строки 435–455)
- Modify: `backend/models.py` (докстринг `ImportJob`, строка 405)
- Test: `backend/tests/integration/test_contracts_api.py` (заменяются
  `test_delete_contract_with_import_history_is_refused` и
  `test_delete_contract_with_estimate_is_refused`)
- Test: `backend/tests/integration/test_contract_cascade_delete.py` (новый файл;
  в этой задаче — только тест сериализации)

**Interfaces:**
- Produces: `crud.contracts.delete_contract(db: Session, contract_id: int) -> list[str]`
  — возвращает `file_key` удалённых заданий в порядке возрастания `id`;
  коммитит сам. Ошибки — `DomainError(404)` / `DomainError(409)`.
- Consumes: `models.TERMINAL_IMPORT_JOB_STATUSES`, `models.ImportJob`,
  `models.Estimate`, `models.Contract`, `crud.common.DomainError` — все
  существуют.

- [ ] **Step 1: Заменить два теста отказа на тесты каскада**

В `backend/tests/integration/test_contracts_api.py` удалить
`test_delete_contract_with_import_history_is_refused` и
`test_delete_contract_with_estimate_is_refused` (они кодируют отменённое
правило) и вписать на их место:

```python
def test_delete_contract_removes_estimates_and_jobs(client, factories, db_session):
    """Каскад: договор уходит вместе со сметой, позициями и заданиями (спека §2.2)."""
    contract = factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(contract=contract)
    proposal = factories.ProposalFactory.create(lot__estimate=estimate)
    position = factories.PositionItemFactory.create(proposal=proposal)
    job = factories.ImportJobFactory.create(
        contract=contract, status=ImportJobStatus.done.value
    )
    catalog = factories.CatalogPositionFactory.create()
    position.catalog_position_id = catalog.id
    db_session.flush()

    assert client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204

    assert db_session.get(Contract, contract.id) is None
    assert db_session.get(Estimate, estimate.id) is None
    assert db_session.get(ImportJob, job.id) is None
    assert db_session.get(PositionItem, position.id) is None
    # Каталог общий (§3), справочники и нормативы живут своей жизнью (спека §2.5).
    assert db_session.get(CatalogPosition, catalog.id) is not None
    assert db_session.get(ObjectModel, contract.object_id) is not None
    assert db_session.get(Contractor, contract.contractor_id) is not None
    assert db_session.get(RateClass, contract.rate_class_id) is not None
    # И договора больше нет в списке — то, что видит человек.
    listed = client.get("/api/v1/contracts").json()["items"]
    assert all(item["id"] != contract.id for item in listed)


@pytest.mark.parametrize(
    "status",
    [
        ImportJobStatus.pending.value,
        ImportJobStatus.parsing.value,
        ImportJobStatus.importing.value,
        ImportJobStatus.matching.value,
    ],
)
def test_delete_contract_is_refused_while_import_runs(client, factories, status):
    """Активное задание — отказ 409, а не каскад посреди импорта (спека §2.2)."""
    contract = factories.ContractFactory.create()
    job = factories.ImportJobFactory.create(contract=contract, status=status)

    response = client.delete(f"/api/v1/contracts/{contract.id}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert str(job.id) in detail and status in detail


def test_delete_missing_contract_gives_404(client):
    assert client.delete("/api/v1/contracts/999999").status_code == 404
```

Импорты файла дополнить: `Estimate`, `ImportJob`, `PositionItem`,
`CatalogPosition`, `ObjectModel`, `Contractor`, `RateClass` из `models` (сейчас
импортируются только `Contract`, `ImportJobStatus`, `UserRole`).

**Матрица и дашборд** этими тестами не проверяются намеренно: чтобы договор
появился в матрице, нужна смета с расценёнными позициями и наполненный каталог,
и такой тест доказывал бы работу агрегатов, а не удаления. Их исчезновение
проверяется прогоном на стенде (Task 8) и — на стороне фронта — тестом
инвалидаций (Task 4).

- [ ] **Step 2: Прогнать и увидеть красное**

```
cd backend && DATABASE_URL=postgresql+psycopg://postgres@localhost:5459/postgres TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5459/gca_test SECRET_KEY=test uv run pytest tests/integration/test_contracts_api.py -k delete -v
```
Ожидание: `test_delete_contract_removes_estimates_and_jobs` падает с `409`
(«удалить нельзя»), остальные новые — тоже.

- [ ] **Step 3: Переписать `delete_contract`**

```python
def delete_contract(db: Session, contract_id: int) -> list[str]:
    """Удаляет договор вместе со сметами, заданиями импорта и их файлами.

    Возвращает ключи файлов удалённых заданий — удаляет их вызывающая сторона
    ПОСЛЕ коммита (спека §2.4): ошибка носителя не вправе откатывать доменное
    решение.

    Порядок шагов несущий:

    1. `FOR UPDATE` на договоре. Вставка задания импорта берёт на этой же строке
       `FOR KEY SHARE` по внешнему ключу, а он конфликтует с `FOR UPDATE`, —
       значит загрузку и удаление сериализует база, и обогнать проверку шага 2
       новым upload нельзя.
    2. Отказ, если задание этого договора активно: удалять данные из-под
       работающего импорта нельзя.
    3. Задания удаляются ПЕРЕД сметами: `estimates.import_job_id` объявлен
       `ON DELETE SET NULL`, обратный порядок ничего не ломает, но и не нужен.

    Всё, что ниже сметы (raw_data, лоты, предложения, позиции, ручные решения по
    статьям, расшивка допработ), уносит `ON DELETE CASCADE` самой БД.
    """
    contract = db.execute(
        sa.select(Contract)
        .where(Contract.id == contract_id)
        .with_for_update()
        # identity map отдал бы объект, загруженный ДО блокировки, и проверка
        # шла бы по устаревшему состоянию (урок фазы 5).
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if contract is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")

    active = db.execute(
        sa.select(ImportJob.id, ImportJob.status)
        .where(
            ImportJob.contract_id == contract_id,
            ImportJob.status.notin_([s.value for s in TERMINAL_IMPORT_JOB_STATUSES]),
        )
        .order_by(ImportJob.id)
        .limit(1)
    ).first()
    if active is not None:
        raise DomainError(
            409,
            f"Импорт сметы этого договора выполняется (задание {active.id}, статус "
            f"«{active.status}»). Дождитесь завершения и повторите удаление.",
        )

    file_keys = list(
        db.execute(
            sa.select(ImportJob.file_key)
            .where(ImportJob.contract_id == contract_id)
            .order_by(ImportJob.id)
        ).scalars()
    )
    db.execute(sa.delete(ImportJob).where(ImportJob.contract_id == contract_id))
    db.execute(sa.delete(Estimate).where(Estimate.contract_id == contract_id))
    db.execute(sa.delete(Contract).where(Contract.id == contract_id))
    db.commit()
    log.info(
        "contract_deleted id=%s jobs=%d", contract_id, len(file_keys)
    )
    return file_keys
```

Добавить в импорты модуля `TERMINAL_IMPORT_JOB_STATUSES` и `ImportJob` из
`models` (проверить: `Estimate` и `Contract` уже импортированы).

- [ ] **Step 4: Прогнать — тесты шага 1 зелёные**

Команда шага 2. Ожидание: PASS.

- [ ] **Step 5: Тест сериализации — доказать, что `FOR UPDATE` работает**

Новый файл `backend/tests/integration/test_contract_cascade_delete.py`:

```python
"""Каскадное удаление договора: файлы, порядок, локи, гонка (спека §2.2–§2.4)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Contract, ImportJob, ImportJobStatus

pytestmark = pytest.mark.integration


def test_lock_blocks_concurrent_job_insert(
    committing_db, committing_factories, committing_session_factory
):
    """Пока договор заблокирован удалением, вставить задание импорта нельзя (§2.2).

    Замок проверяется НАБЛЮДАЕМЫМ отказом: вторая сессия ставит короткий
    `lock_timeout` и обязана упереться. Снятие `.with_for_update()` в
    `delete_contract` этот тест не ловит по построению (он берёт замок сам) —
    он доказывает лишь то, ЧТО замок такого рода сериализует вставку; снятие
    защиты в самом коде проверяется шагом 6.
    """
    contract = committing_factories.ContractFactory.create()
    # Фабрики настроены на `sqlalchemy_session_persistence = "flush"` — без
    # явного коммита другие сессии договора не увидят.
    committing_db.commit()

    holder = committing_session_factory()
    holder.execute(
        sa.select(Contract).where(Contract.id == contract.id).with_for_update()
    ).scalar_one()

    other = committing_session_factory()
    try:
        other.execute(sa.text("SET lock_timeout = '300ms'"))
        other.add(
            ImportJob(
                contract_id=contract.id,
                amendment_no=None,
                filename="e.xlsx",
                file_key="0" * 32,
                file_sha256="0" * 64,
                status=ImportJobStatus.pending.value,
            )
        )
        with pytest.raises(Exception) as exc:
            other.commit()
        assert "lock" in str(exc.value).lower()
    finally:
        other.rollback()
        other.close()
        holder.rollback()
        holder.close()
```

**Исполнителю:** `SET lock_timeout` — без `LOCAL`, иначе вне явного блока
транзакции значение не переживёт. Если окажется, что вставка не блокируется,
проверить замером, какой замок реально берёт `INSERT` (`pg_locks` по
`relation = 'contracts'::regclass`) — утверждение «FK берёт `FOR KEY SHARE`»
должно подтвердиться наблюдением, а не остаться предпосылкой.

- [ ] **Step 6: Прогнать тест сериализации; убедиться, что он краснеет без замка**

```
cd backend && … uv run pytest tests/integration/test_contract_cascade_delete.py -v
```
Затем временно убрать `.with_for_update()` из `delete_contract` — тест должен
упасть; вернуть. Пробник правки — с `assert old in source`, иначе не
применившийся патч читается как доказанная защита (урок фазы 5).

- [ ] **Step 7: Правка утверждений в тронутых файлах**

`backend/crud/contracts.py`, шапка модуля — второй пункт списка заменить на:

```
* **Удаление договора каскадное и необратимое** (спека 2026-08-16): вместе с
  договором уходят его сметы, задания импорта и файлы. Прежнее правило «задания
  импорта не удаляются никогда» сужено: аудит защищает от подмены сметы в живом
  договоре, а не от удаления самого договора владельцем данных (`AGENTS.md` §5).
```

`backend/models.py`, докстринг `ImportJob`:

```python
    """Задание импорта сметы (§4, §5).

    Записи не удаляются ретенцией и заменой сметы — это аудит; удаление самого
    договора уносит их вместе с ним (спека 2026-08-16, ревизия §5).
    """
```

- [ ] **Step 8: Коммит**

```bash
git add backend/crud/contracts.py backend/models.py backend/tests/integration/test_contracts_api.py backend/tests/integration/test_contract_cascade_delete.py
git commit -m "feat(contracts): каскадное удаление договора в CRUD"
```

---

## Task 2: Файлы удаляются после коммита, best-effort

**Files:**
- Modify: `backend/services/maintenance.py` (новая функция; шапка модуля)
- Modify: `backend/routers/contracts.py` (эндпоинт `delete_contract`, строки 265–276)
- Modify: `backend/storage.py` (комментарий в `StorageFileNotFound`)
- Test: `backend/tests/integration/test_contract_cascade_delete.py`

**Interfaces:**
- Produces: `services.maintenance.purge_files_best_effort(storage: Storage, file_keys: Iterable[str], *, context: str) -> int`
  — удаляет каждый ключ в своём `try/except`, возвращает число удалённых.
- Consumes: `crud.contracts.delete_contract` (Task 1), `storage.get_storage`,
  `storage.Storage`, `storage.StorageKeyError` — существуют.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `backend/tests/integration/test_contract_cascade_delete.py`:

```python
def test_files_are_deleted_after_commit(
    committing_client, committing_db, committing_factories, tmp_storage
):
    """Файлы заданий исчезают с диска вместе с договором (спека §2.4)."""
    contract = committing_factories.ContractFactory.create()
    keys = [tmp_storage.save(b"PK\x03\x04 first"), tmp_storage.save(b"PK\x03\x04 second")]
    for key in keys:
        committing_factories.ImportJobFactory.create(
            contract=contract, file_key=key, status=ImportJobStatus.done.value
        )
    committing_db.commit()

    assert committing_client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204

    assert [tmp_storage.exists(key) for key in keys] == [False, False]


def test_commit_failure_deletes_no_files(
    committing_client, committing_db, committing_factories, tmp_storage, monkeypatch
):
    """Сбой коммита → НИ ОДНОГО обращения к хранилищу, файлы и записи целы.

    Это утверждение о ПОРЯДКЕ, а не о факте удаления: шпион «позван на каждый
    ключ» прошёл бы и при удалении файлов до коммита (спека §2.4).
    """
    contract = committing_factories.ContractFactory.create()
    key = tmp_storage.save(b"PK\x03\x04 payload")
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key=key, status=ImportJobStatus.done.value
    )
    committing_db.commit()

    calls: list[str] = []
    monkeypatch.setattr(tmp_storage, "delete", lambda k: calls.append(k) or True)

    import crud.contracts as crud_contracts

    def explode(db, contract_id):
        raise RuntimeError("коммит не прошёл")

    monkeypatch.setattr(crud_contracts, "delete_contract", explode)

    with pytest.raises(RuntimeError):
        committing_client.delete(f"/api/v1/contracts/{contract.id}")

    assert calls == []
    assert tmp_storage.exists(key)
    assert committing_db.get(Contract, contract.id) is not None
```

**Исполнителю про `test_commit_failure_deletes_no_files`.** Патч ставится на
`crud.contracts.delete_contract`, а НЕ на `Session.commit`: `committing_client`
берёт сессии из той же фабрики, и общий патч коммита уронил бы фикстуры вместе с
проверяемым кодом (эталон обязан не ехать вместе с проверяемым —
`docs/insights/verifying-guards.md`). Роутер импортирует модуль
(`from crud import contracts as crud_contracts`), поэтому подмена атрибута
модуля действует. `TestClient` по умолчанию пробрасывает исключение наружу
(`raise_server_exceptions=True`) — отсюда `pytest.raises`, а не проверка кода
`500`; если в проекте окажется обработчик, превращающий `RuntimeError` в ответ,
заменить на проверку кода.

**Важно:** этот тест доказывает только «до вызова CRUD файлы не трогают».
Настоящий порядок «коммит → хранилище» доказывается шагом 6 — переносом вызова
уборки перед CRUD.

```python
def test_one_broken_key_does_not_stop_the_rest(
    committing_client, committing_db, committing_factories, tmp_storage
):
    """Ошибка на одном файле не отменяет остальные, ответ всё равно 204 (спека §2.4)."""
    contract = committing_factories.ContractFactory.create()
    good = tmp_storage.save(b"PK\x03\x04 ok")
    # Ключ не проходит KEY_PATTERN → StorageKeyError; ровно этот случай изолирует
    # `purge_files_best_effort`.
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key="сломанный ключ", status=ImportJobStatus.error.value
    )
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key=good, status=ImportJobStatus.done.value
    )
    committing_db.commit()

    assert committing_client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204
    assert not tmp_storage.exists(good)


def test_missing_file_is_normal(
    committing_client, committing_db, committing_factories, tmp_storage
):
    """Файла уже нет (ретенция §8) — это штатный исход, а не ошибка."""
    contract = committing_factories.ContractFactory.create()
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key="0" * 32, status=ImportJobStatus.error.value
    )
    committing_db.commit()

    assert committing_client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204
```

Порядок ключей в `test_one_broken_key_does_not_stop_the_rest` неслучаен: битый
идёт ПЕРВЫМ, иначе тест прошёл бы и при обрыве цикла на первой ошибке.

- [ ] **Step 2: Прогнать — красное**

```
cd backend && … uv run pytest tests/integration/test_contract_cascade_delete.py -v
```
Ожидание: файлы остаются на диске (роутер их пока не удаляет).

- [ ] **Step 3: Функция уборки в `services/maintenance.py`**

```python
def purge_files_best_effort(storage: Storage, file_keys: Iterable[str], *, context: str) -> int:
    """Удаляет файлы по ключам, изолируя ошибки ПО КЛЮЧАМ.

    Вызывается ПОСЛЕ коммита доменной транзакции (спека §2.4): ошибка носителя
    не вправе откатывать уже принятое решение, поэтому она только пишется в лог.
    Цена названа границей спеки §4.1: неудалённый файл остаётся на диске
    навсегда — ретенция §8 ходит по заданиям, а записи задания уже нет.
    """
    purged = 0
    for key in file_keys:
        try:
            if storage.delete(key):
                purged += 1
        except (StorageKeyError, OSError):
            log.exception("%s: файл %r не удалён — пропущен", context, key)
    return purged
```

`Iterable` импортировать из `collections.abc`.

- [ ] **Step 4: Роутер вызывает уборку после CRUD**

`backend/routers/contracts.py`:

```python
@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    _: User = Depends(require_admin),
):
    """Удалить договор вместе со сметами, заданиями импорта и файлами.

    `409`, если импорт этой сметы выполняется прямо сейчас (спека §2.2).
    Файлы удаляются ПОСЛЕ коммита и best-effort: ответ `204` не зависит от того,
    удалось ли снять их с диска (спека §2.4).
    """
    try:
        file_keys = crud_contracts.delete_contract(db, contract_id)
    except DomainError as e:
        _raise(e)
    purge_files_best_effort(storage, file_keys, context=f"Удаление договора {contract_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Импорты роутера дополнить: `from storage import Storage, get_storage`,
`from services.maintenance import purge_files_best_effort`.

- [ ] **Step 5: Прогнать — зелёное**

Команда шага 2. Ожидание: PASS, включая `test_commit_failure_deletes_no_files`.

- [ ] **Step 6: Проверить порядок снятием защиты**

Временно перенести `purge_files_best_effort` ДО `crud_contracts.delete_contract`
— `test_commit_failure_deletes_no_files` обязан покраснеть. Вернуть как было.

- [ ] **Step 7: Правка утверждения в `storage.py`**

Докстринг `StorageFileNotFound`: «…а сама запись job остаётся навсегда — это
аудит» → «…а сама запись job переживает ретенцию — это аудит; уносит её только
удаление договора (спека 2026-08-16)».

- [ ] **Step 8: Коммит**

```bash
git add backend/routers/contracts.py backend/services/maintenance.py backend/storage.py backend/tests/integration/test_contract_cascade_delete.py
git commit -m "feat(contracts): файлы удалённого договора убираются после коммита"
```

---

## Task 3: Гонка загрузки и удаления — `404` вместо `500`

**Files:**
- Modify: `backend/routers/estimates.py` (обработчик `IntegrityError`, строки 245–264; шапка модуля)
- Test: `backend/tests/integration/test_estimates_api.py`

**Interfaces:**
- Produces: константа `estimates.CONTRACT_FK_CONSTRAINT = "import_jobs_contract_id_fkey"`.
- Consumes: существующая `ACTIVE_PAIR_INDEX`, `storage.delete`.

- [ ] **Step 1: Тест предпосылки — имя констрейнта не выдумано**

В `backend/tests/integration/test_estimates_api.py`:

```python
def test_contract_fk_constraint_name_is_what_the_router_expects(db_session):
    """ПРЕДПОСЫЛКА: имя внешнего ключа присвоил PostgreSQL, миграция его не задавала.

    Роутер распознаёт исчезнувший договор по имени констрейнта; если имя другое,
    ветка `404` мертва, а тест пути ниже это скроет — он вызывает ту же ошибку.
    Поэтому имя проверяется НАСТОЯЩИМ нарушением (insights/false-test-premises).
    """
    import psycopg
    from routers.estimates import CONTRACT_FK_CONSTRAINT

    with pytest.raises(Exception) as exc:
        db_session.execute(
            sa.text(
                "INSERT INTO import_jobs (contract_id, filename, file_key, file_sha256, status) "
                "VALUES (999999, 'x.xlsx', '0', '0', 'pending')"
            )
        )
        db_session.flush()
    orig = getattr(exc.value, "orig", exc.value)
    assert isinstance(orig, psycopg.errors.ForeignKeyViolation)
    assert orig.sqlstate == "23503"
    assert orig.diag.constraint_name == CONTRACT_FK_CONSTRAINT
    db_session.rollback()
```

- [ ] **Step 2: Тест исполняемого пути `404`**

```python
def test_upload_returns_404_when_contract_deleted_mid_flight(
    committing_client, contract, committing_session_factory, tmp_storage, monkeypatch
):
    """DELETE успел первым: загрузка отвечает 404, файла и задания не остаётся.

    Договор удаляется в реальном шве — между проверкой его существования и
    вставкой задания, — поэтому воспроизводится именно рассматриваемая гонка,
    а не её имитация (спека §2.3).

    `contract` — существующая фикстура этого файла (строка 55): она создаёт
    договор и КОММИТИТ его, без чего другие сессии его не увидят.
    """
    contract_id = contract.id

    import routers.estimates as estimates_router

    real_active_job = estimates_router._active_job

    def delete_then_check(db, cid, amendment_no):
        killer = committing_session_factory()
        killer.execute(sa.delete(Contract).where(Contract.id == cid))
        killer.commit()
        killer.close()
        return real_active_job(db, cid, amendment_no)

    monkeypatch.setattr(estimates_router, "_active_job", delete_then_check)

    before = set(p.name for p in tmp_storage.root.iterdir()) if tmp_storage.root.exists() else set()
    response = committing_client.post(
        "/api/v1/estimates/upload",
        files={"file": ("smeta.xlsx", b"PK\x03\x04payload", XLSX_MEDIA_TYPE)},
        data={"contract_id": str(contract_id)},
    )
    after = set(p.name for p in tmp_storage.root.iterdir()) if tmp_storage.root.exists() else set()

    assert response.status_code == 404
    assert "удал" in response.json()["detail"].lower()
    assert after == before  # файл проигравшей загрузки не остался
```

Проверить в файле фактические имена уже используемых констант
(`XLSX_MEDIA_TYPE` объявлен в `routers/estimates.py`) и импортировать
`Contract`, `sqlalchemy as sa`, если их там ещё нет.

- [ ] **Step 3: Прогнать — красное**

```
cd backend && … uv run pytest tests/integration/test_estimates_api.py -k "fk_constraint or mid_flight" -v
```
Ожидание: тест предпосылки падает на импорте `CONTRACT_FK_CONSTRAINT`, тест пути
— на `500` вместо `404`.

- [ ] **Step 4: Распознавание FK в загрузке**

В `backend/routers/estimates.py` рядом с `ACTIVE_PAIR_INDEX`:

```python
#: Внешний ключ `import_jobs.contract_id`. Имя присвоено PostgreSQL по умолчанию
#: (миграция 0002 объявила ключ без имени) — оно закреплено тестом предпосылки.
CONTRACT_FK_CONSTRAINT = "import_jobs_contract_id_fkey"
```

В обработчике `IntegrityError`, ПЕРЕД проверкой `ACTIVE_PAIR_INDEX`:

```python
        # Договор удалён между проверкой его существования и вставкой задания
        # (спека §2.3). Распознаём структурированной диагностикой, а не поиском
        # подстроки: имя в тексте ошибки соседствует с пользовательскими данными.
        diag = getattr(exc.orig, "diag", None)
        if (
            getattr(exc.orig, "sqlstate", None) == "23503"
            and getattr(diag, "constraint_name", None) == CONTRACT_FK_CONSTRAINT
        ):
            log.info("Договор %s удалён, пока загружался файл", contract_id)
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Договор {contract_id} удалён, пока загружался файл. Смета не "
                "импортирована.",
            ) from exc
```

Файл к этому моменту уже удалён строкой `storage.delete(file_key)` выше — новых
действий с хранилищем не нужно.

- [ ] **Step 5: Прогнать — зелёное**

Команда шага 3. Ожидание: PASS.

- [ ] **Step 6: Снять защиту и убедиться, что тест ловит**

Временно испортить константу (`"нет_такого_ключа"`) — тест пути обязан
покраснеть с `500`. Вернуть.

- [ ] **Step 7: Правка шапки `routers/estimates.py`**

В перечень ответов эндпоинта (докстринг `upload_estimate`) добавить: «`404` —
договора нет либо он удалён, пока загружался файл».

- [ ] **Step 8: Коммит**

```bash
git add backend/routers/estimates.py backend/tests/integration/test_estimates_api.py
git commit -m "fix(estimates): исчезнувший договор при загрузке даёт 404, а не 500"
```

---

## Task 4: Инвалидации `useDeleteContract`

**Files:**
- Modify: `frontend/src/services/queries.ts` (строки 329–344)
- Test: `frontend/src/services/queries.test.tsx`

**Interfaces:**
- Consumes: `qk` из `services/queryKeys.ts` — корни `contracts`, `importJobs`,
  `passport`, `matrix`, `dashboard`, `review`, `objects`, `contractors`,
  `rateClasses` существуют.

- [ ] **Step 1: Тест на девять корней**

В `frontend/src/services/queries.test.tsx` (рядом с существующим блоком про
корень паспорта):

```tsx
describe("useDeleteContract: инвалидация после каскадного удаления", () => {
  it("удаление договора инвалидирует все поверхности, где он виден", async () => {
    const queryClient = createTestQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    server.use(
      http.delete("/api/v1/contracts/:id", () => new HttpResponse(null, { status: 204 }))
    );

    const { result } = renderHook(() => useDeleteContract(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    });
    await act(async () => {
      await result.current.mutateAsync(1);
    });

    // Ключи проверяются ПО ОТДЕЛЬНОСТИ: «вызвано девять раз» прошло бы и при
    // девяти одинаковых ключах.
    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    for (const root of [
      qk.contracts.all,
      qk.importJobs.all,
      qk.passport.all,
      qk.matrix.all,
      qk.dashboard.all,
      qk.review.all,
      qk.objects.all,
      qk.contractors.all,
      qk.rateClasses.all,
    ]) {
      expect(keys).toContain(JSON.stringify(root));
    }
  });
});
```

- [ ] **Step 2: Прогнать — красное**

```
cd frontend && npx vitest run src/services/queries.test.tsx
```
Ожидание: не хватает семи корней.

- [ ] **Step 3: Дописать инвалидации**

В `useDeleteContract`, `onSuccess`, после существующих двух:

```ts
      // Каскад уносит сметы и позиции, поэтому устаревает всё, где договор
      // виден или посчитан (спека §2.7). `qk.contracts.all` накрывает и историю
      // загрузок договора; поллинг задания живёт в ОТДЕЛЬНОМ пространстве
      // `qk.importJobs`, под префикс договоров он не попадает.
      qc.invalidateQueries({ queryKey: qk.importJobs.all });
      qc.invalidateQueries({ queryKey: qk.matrix.all });
      qc.invalidateQueries({ queryKey: qk.dashboard.all });
      // Позиции ушли — фактическая очередь Review изменилась.
      qc.invalidateQueries({ queryKey: qk.review.all });
      // Счётчики договоров в справочниках: ровно их инвалидирует
      // `useCreateContract`, и несимметричность была бы дефектом.
      qc.invalidateQueries({ queryKey: qk.objects.all });
      qc.invalidateQueries({ queryKey: qk.contractors.all });
      qc.invalidateQueries({ queryKey: qk.rateClasses.all });
```

- [ ] **Step 4: Прогнать — зелёное**

Команда шага 2. Ожидание: PASS.

- [ ] **Step 5: Коммит**

```bash
git add frontend/src/services/queries.ts frontend/src/services/queries.test.tsx
git commit -m "fix(contracts): удаление договора инвалидирует все его поверхности"
```

---

## Task 5: Диалог удаления и вход из списка

**Files:**
- Create: `frontend/src/components/contracts/ContractDeleteDialog.tsx`
- Modify: `frontend/src/pages/contracts/ContractsPage.tsx`
- Modify: `frontend/src/test/handlers.ts`
- Test: `frontend/src/pages/contracts/ContractsPage.test.tsx`

**Interfaces:**
- Produces: `ContractDeleteDialog` с пропсами
  `{ contract: { id: number; contract_number: string } | null; onOpenChange: (open: boolean) => void; onDeleted?: () => void }`.
- Consumes: `useDeleteContract`, `useContractImportJobs` (существует в
  `services/queries.ts`), `AlertDialog*` из `components/ui/alert-dialog`,
  `DropdownMenu*` из `components/ui/dropdown-menu`.

- [ ] **Step 1: Обработчик DELETE в MSW**

В `frontend/src/test/handlers.ts` рядом с `http.patch("/api/v1/contracts/:id", …)`:

```ts
  http.delete("/api/v1/contracts/:id", () => new HttpResponse(null, { status: 204 })),
```

- [ ] **Step 2: Написать падающие тесты экрана**

В `frontend/src/pages/contracts/ContractsPage.test.tsx`:

```tsx
  it("admin видит действие удаления в строке договора", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.click(screen.getAllByRole("button", { name: /Действия с договором/ })[0]);

    expect(await screen.findByRole("menuitem", { name: /Удалить/ })).toBeInTheDocument();
  });

  it("member действия удаления не видит", async () => {
    renderWithProviders(<ContractsPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });
    await screen.findByText("ГП-2026-001");

    expect(screen.queryByRole("button", { name: /Действия с договором/ })).not.toBeInTheDocument();
  });

  it("удаление подтверждается вводом номера договора", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.click(screen.getAllByRole("button", { name: /Действия с договором/ })[0]);
    await user.click(await screen.findByRole("menuitem", { name: /Удалить/ }));

    const confirm = await screen.findByRole("button", { name: "Удалить договор" });
    expect(confirm).toBeDisabled();

    // Опечатка не разблокирует: сверка точная.
    await user.type(screen.getByLabelText(/Введите номер договора/), "ГП-2026-00");
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText(/Введите номер договора/), "1");
    expect(confirm).toBeEnabled();
  });

  it("диалог называет, сколько смет и заданий уйдёт", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.click(screen.getAllByRole("button", { name: /Действия с договором/ })[0]);
    await user.click(await screen.findByRole("menuitem", { name: /Удалить/ }));

    // Число заданий приходит из GET /contracts/:id/import-jobs (обработчик MSW
    // уже есть и отдаёт sampleImportJobs).
    expect(await screen.findByText(/заданий импорта/i)).toBeInTheDocument();
  });
```

- [ ] **Step 3: Прогнать — красное**

```
cd frontend && npx vitest run src/pages/contracts/ContractsPage.test.tsx
```

- [ ] **Step 4: Компонент диалога**

`frontend/src/components/contracts/ContractDeleteDialog.tsx`:

```tsx
import { useEffect, useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useContractImportJobs, useDeleteContract } from "@/services/queries";

type Target = { id: number; contract_number: string; estimates_count?: number };

/**
 * Подтверждение каскадного удаления договора (спека §2.6).
 *
 * Кнопка разблокируется только точным вводом номера договора: операция уносит
 * сметы вместе с историей загрузок и необратима, а «Вы уверены?» нажимается
 * мимо. Состав удаляемого называется вслух — число заданий берётся из
 * `GET /contracts/:id/import-jobs`, то есть из того же источника, что и история
 * на карточке.
 */
export function ContractDeleteDialog({
  contract,
  onOpenChange,
  onDeleted,
}: {
  contract: Target | null;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const [typed, setTyped] = useState("");
  const remove = useDeleteContract();
  const jobsQ = useContractImportJobs(contract?.id);

  useEffect(() => {
    setTyped("");
  }, [contract?.id]);

  const confirmed = contract !== null && typed.trim() === contract.contract_number;

  return (
    <AlertDialog open={contract !== null} onOpenChange={(open) => !open && onOpenChange(false)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Удалить договор «{contract?.contract_number}»?</AlertDialogTitle>
          <AlertDialogDescription>
            Вместе с договором будут удалены его сметы
            {contract?.estimates_count !== undefined ? ` (${contract.estimates_count})` : ""},
            задания импорта ({jobsQ.data?.length ?? "…"}) и загруженные файлы. Действие
            необратимо: восстановить их из приложения будет нельзя.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2">
          <Label htmlFor="confirm-contract-number">
            Введите номер договора, чтобы подтвердить
          </Label>
          <Input
            id="confirm-contract-number"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
          />
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
          <AlertDialogAction
            render={
              <Button
                variant="destructive"
                disabled={!confirmed || remove.isPending}
                onClick={() => {
                  if (!contract || !confirmed) return;
                  remove.mutate(contract.id, {
                    onSuccess: () => {
                      onOpenChange(false);
                      onDeleted?.();
                    },
                  });
                }}
              >
                Удалить договор
              </Button>
            }
          />
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

Сигнатура хука сверена: `useContractImportJobs(id: number | undefined)` сам
несёт `enabled: id !== undefined` (`services/queries.ts:282-288`), поэтому
`contract?.id` передаётся как есть и запрос при закрытом диалоге не уходит.
`useDeleteContract` уже показывает тост и на успех, и на ошибку — своего тоста
диалогу заводить не нужно.

- [ ] **Step 5: Колонка действий в списке**

В `ContractsPage.tsx`: добавить `useState<Target | null>` под удаление, колонку
`<TableHead className="w-12" />` в конце шапки и в каждой строке — меню (только
для `isAdmin`):

```tsx
<TableCell className="text-right">
  {isAdmin && (
    <DropdownMenu>
      {/*
        Триггер принимает СВОИ пропсы и children (base-ui `MenuPrimitive.Trigger`),
        а не `render`/`asChild` — образец живого использования в проекте:
        `components/layout/TopNav.tsx:79`.
      */}
      <DropdownMenuTrigger
        type="button"
        aria-label="Действия с договором"
        className="inline-flex size-8 items-center justify-center rounded-md hover:bg-surface-hover"
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem variant="destructive" onClick={() => setToDelete(contract)}>
          <Trash2 className="size-4" /> Удалить
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )}
</TableCell>
```

и в конце компонента — `<ContractDeleteDialog contract={toDelete} onOpenChange={() => setToDelete(null)} />`.

`DropdownMenuItem` действительно принимает `variant="destructive"`
(`components/ui/dropdown-menu.tsx:74-82`) — отдельного класса не нужно.

- [ ] **Step 6: Прогнать — зелёное**

```
cd frontend && npx vitest run src/pages/contracts/ContractsPage.test.tsx
```

- [ ] **Step 7: Коммит**

```bash
git add frontend/src/components/contracts/ContractDeleteDialog.tsx frontend/src/pages/contracts/ContractsPage.tsx frontend/src/pages/contracts/ContractsPage.test.tsx frontend/src/test/handlers.ts
git commit -m "feat(contracts): удаление договора из списка с подтверждением"
```

---

## Task 6: Вход с карточки договора

**Files:**
- Modify: `frontend/src/pages/contracts/ContractCardPage.tsx` (блок `actions`, строки 105–133)
- Test: `frontend/src/pages/contracts/ContractCardPage.test.tsx`

**Interfaces:**
- Consumes: `ContractDeleteDialog` (Task 5), `useNavigate` из `react-router-dom`.

- [ ] **Step 1: Тест**

Тесты пишутся через **существующий хелпер файла `renderCard`** (строки 28–96):
он монтирует `<Routes><Route path="/contracts/:contractId" …>` на маршруте
`/contracts/100` и принимает короткую опцию `role`. Отдельный
`renderWithProviders` здесь не годится — страница читает `contractId` из
`useParams`. Номер договора карточки — `ГП-2026-001` (`sampleContractCard`
наследует его от `sampleContracts[0]`).

```tsx
  it("admin удаляет договор с карточки и уходит со страницы", async () => {
    const user = userEvent.setup();
    renderCard({ role: "admin" });
    await screen.findByRole("button", { name: /Удалить/ });

    await user.click(screen.getByRole("button", { name: /Удалить/ }));
    await user.type(screen.getByLabelText(/Введите номер договора/), "ГП-2026-001");
    await user.click(screen.getByRole("button", { name: "Удалить договор" }));

    // Роутер здесь MemoryRouter, `window.location` он не трогает; уход виден
    // тем, что маршрут карточки больше не совпадает и её содержимое пропало.
    await waitFor(() => {
      expect(screen.queryByText("ЖК Северный")).not.toBeInTheDocument();
    });
  });

  it("member кнопки удаления не видит", async () => {
    renderCard({ role: "member" });
    await screen.findByText("ГП-2026-001");
    expect(screen.queryByRole("button", { name: /Удалить/ })).not.toBeInTheDocument();
  });
```

**Исполнителю:** «ЖК Северный» — название объекта из `sampleContracts[0]`;
сверить, что оно есть на карточке (в подзаголовке `PageHeader`), и при
несовпадении взять текст, который карточка точно показывает. Обработчик
`DELETE /api/v1/contracts/:id` заведён в MSW задачей 5.

- [ ] **Step 2: Прогнать — красное**

```
cd frontend && npx vitest run src/pages/contracts/ContractCardPage.test.tsx
```

- [ ] **Step 3: Кнопка и диалог на карточке**

В `actions` после кнопки «ТЭП объекта»:

```tsx
              {isAdmin && (
                <Button variant="outline" onClick={() => setDeleteOpen(true)}>
                  <Trash2 className="size-4" /> Удалить
                </Button>
              )}
```

и в конце компонента:

```tsx
      <ContractDeleteDialog
        contract={deleteOpen ? contract : null}
        onOpenChange={() => setDeleteOpen(false)}
        onDeleted={() => navigate("/contracts")}
      />
```

Дополнить импорты и состояние страницы: `Trash2` из `lucide-react`,
`useNavigate` из `react-router-dom` (страница сейчас импортирует только
`useParams` — проверить строку импорта), `const navigate = useNavigate();`,
`const [deleteOpen, setDeleteOpen] = useState(false);` и сам
`ContractDeleteDialog` из `@/components/contracts/ContractDeleteDialog`.

- [ ] **Step 4: Прогнать — зелёное**

Команда шага 2.

- [ ] **Step 5: Коммит**

```bash
git add frontend/src/pages/contracts/ContractCardPage.tsx frontend/src/pages/contracts/ContractCardPage.test.tsx
git commit -m "feat(contracts): удаление договора с карточки"
```

---

## Task 7: Документы — ревизия `AGENTS.md` и devlog

**Files:**
- Modify: `AGENTS.md` (преамбула версий; §3; §5 правило 3; §8)
- Modify: `backend/services/maintenance.py`, `backend/routers/import_jobs.py`,
  `backend/services/estimate_import.py` (оставшиеся утверждения «аудит навсегда»)
- Create: `docs/devlog/2026-08-16-contract-cascade-delete.md`

- [ ] **Step 1: Врезка версии в `AGENTS.md`**

Первой в блоке цитат, перед v6.6:

```markdown
> **v6.7 (2026-08-16), по итогам фичи каскадного удаления договора.** Одна
> осознанная ревизия: прежнее «задания импорта — аудит и не удаляются никогда»
> (§5 правило 3, §8) сужено до своей области. Аудит защищает от подмены сметы в
> живом договоре, а не от удаления самого договора владельцем данных: удаление
> снимает предмет вопроса «что лежало здесь раньше», и запись о загрузке к
> несуществующему договору — не аудит, а мусор с висящим внешним ключом. В §3
> право `admin` дополнено удалением договора вместе со сметами и аудитом.
> Обоснование, границы и отвергнутые альтернативы —
> [спека](docs/superpowers/specs/2026-08-16-contract-cascade-delete-design.md),
> замеры — [devlog](docs/devlog/2026-08-16-contract-cascade-delete.md).
```

- [ ] **Step 2: Правки по тексту `AGENTS.md`**

§3, роль `admin`: после «замена/удаление смет» дописать «удаление договора
целиком — вместе со сметами, заданиями импорта и файлами (v6.7)».

§5, правило 3, после «Старые `import_jobs` и их файлы **не удаляются** — это
аудит»: «…при ЗАМЕНЕ сметы. Удаление самого договора уносит их вместе с ним
(v6.7); удаление отвергается, пока импорт этой сметы выполняется».

§8, после «файлы `done`-jobs хранятся бессрочно (аудит)»: «…пока жив договор;
удаление договора удаляет их после коммита доменной транзакции, best-effort
(v6.7)».

- [ ] **Step 3: Оставшиеся утверждения в коде**

- `backend/services/maintenance.py`, шапка модуля: «Сама запись job не удаляется
  никогда — это аудит» → «Сама запись job ретенцию переживает — это аудит;
  уносит её только удаление договора (v6.7)».
- `backend/routers/import_jobs.py`, докстринг выдачи файла и текст предупреждения
  («Запись задания при этом остаётся навсегда, это аудит») → «остаётся, пока жив
  договор».
- `backend/services/estimate_import.py:585` (комментарий) и `:673` (текст
  warning'а замены) → «Прежние задания импорта и их файлы сохранены как аудит
  замены.» Проверить `grep -r "сохранены как аудит" backend/tests` — на
  2026-08-16 ни один тест этой строки не утверждает, но проверить перед правкой.

- [ ] **Step 4: Devlog**

Создать `docs/devlog/2026-08-16-contract-cascade-delete.md` по таксономии §9.2:
что сделано (по задачам плана), замеры (число тестов до/после, время `just ci`),
отступления от плана, найденные грабли. Обязательно назвать границу §4.1 спеки
(файл-сирота при сбое носителя) и результат прогона на стенде.

- [ ] **Step 5: Коммит**

```bash
git add AGENTS.md backend/services/maintenance.py backend/routers/import_jobs.py backend/services/estimate_import.py docs/devlog/2026-08-16-contract-cascade-delete.md
git commit -m "docs: AGENTS.md v6.7 — удаление договора уносит аудит, devlog фичи"
```

---

## Task 8: Приёмка — `just ci` и прогон на стенде

- [ ] **Step 1: Полный прогон проверок**

```
just ci
```
Шаги смотреть по отдельности; конвейер с `| tail` вернёт код `tail` и покажет
падение как `exit 0`.

- [ ] **Step 2: Прогон на стенде `gca_dev`**

Поднять бэкенд и фронт, войти как `admin@example.com`. Завести договор, загрузить
к нему смету из `samples/`, дождаться `done`. Затем:

1. попытаться удалить договор **во время** импорта — ожидается отказ с текстом
   про активное задание;
2. удалить после завершения — договор исчезает из списка, матрицы и главной
   **без перезагрузки страницы**;
3. проверить, что файл исчез из каталога хранилища (`STORAGE_DIR` из настроек);
4. убедиться, что остальные договоры стенда и их сметы целы (`select count(*)
   from contracts, estimates`).

- [ ] **Step 3: Замеры в devlog**

Дописать в devlog фактические числа: сколько тестов стало, что показал стенд,
чем прогон разошёлся с планом.

- [ ] **Step 4: Коммит и PR**

```bash
git add docs/devlog/2026-08-16-contract-cascade-delete.md
git commit -m "docs(devlog): замеры прогона каскадного удаления"
git push -u origin feat/contract-cascade-delete
```
PR со ссылками на спеку и план в описании (§9.3).
