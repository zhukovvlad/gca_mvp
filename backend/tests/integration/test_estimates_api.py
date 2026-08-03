"""Эндпоинты загрузки сметы и заданий импорта (AGENTS.md §5, §8)."""
from __future__ import annotations

import io
import zipfile
from datetime import timedelta

import pytest
import sqlalchemy as sa

from models import Estimate, ImportJob, ImportJobStatus, UserRole
from parser import ParseResult
from services import import_pipeline
from tests.payloads import payload_for, position
from utils import utcnow_aware

pytestmark = pytest.mark.integration

UPLOAD_URL = "/api/v1/estimates/upload"


def xlsx_bytes(marker: str = "a") -> bytes:
    """Настоящий ZIP-контейнер — эндпоинт проверяет магию файла, не расширение."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", f"<workbook>{marker}</workbook>")
    return buffer.getvalue()


@pytest.fixture
def stub_parser(monkeypatch):
    """Подменяет парсер в пайплайне: тесты API не про разбор XLSX.

    Возвращает функцию, которой тест задаёт payload для следующего импорта.
    """
    holder: dict = {"payload": None, "error": None, "calls": 0}

    def fake_parse(_handle):
        holder["calls"] += 1
        if holder["error"] is not None:
            raise holder["error"]
        return ParseResult(data=holder["payload"], parser_version="1.0.0", warnings=[])

    monkeypatch.setattr(import_pipeline, "parse_estimate", fake_parse)

    def configure(payload=None, *, error=None):
        holder["payload"] = payload
        holder["error"] = error
        return holder

    return configure


@pytest.fixture
def contract(committing_db, committing_factories):
    contract = committing_factories.ContractFactory.create()
    committing_db.commit()
    return contract


def upload(client, *, content: bytes, contract_id: int, filename="смета.xlsx", **fields):
    data = {"contract_id": str(contract_id), **{k: str(v) for k, v in fields.items()}}
    return client.post(
        UPLOAD_URL,
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
    )


def finished_job(client, response) -> dict:
    """Состояние задания ПОСЛЕ фоновой работы.

    Ответ на загрузку описывает только созданное задание (`pending`) — тяжёлая
    часть уходит в `BackgroundTasks` (§5, шаг 1). `TestClient` выполняет её сразу
    после отдачи ответа, поэтому финальное состояние читается тем же
    эндпоинтом поллинга, которым его читает фронт.
    """
    return client.get(f"/api/v1/import-jobs/{response.json()['id']}").json()


# ---------------------------------------------------------------------------
#  Полный путь через HTTP
# ---------------------------------------------------------------------------

class TestUploadHappyPath:
    def test_upload_runs_the_whole_pipeline(
        self, committing_client, committing_db, contract, stub_parser
    ):
        stub_parser(payload_for(contract, [position(job_title="Работа", unit="м2", unit_cost_total="10")]))

        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)

        # Ответ описывает СОЗДАННОЕ задание: тяжёлая часть ещё в BackgroundTasks.
        assert response.status_code == 202
        assert response.json()["status"] == ImportJobStatus.pending.value
        assert response.json()["filename"] == "смета.xlsx"

        job = finished_job(committing_client, response)
        assert job["status"] == ImportJobStatus.done.value
        assert job["counters"]["positions_total"] == 1
        assert job["estimate_id"] is not None

        committing_db.expire_all()
        estimate = committing_db.get(Estimate, job["estimate_id"])
        assert estimate.contract_id == contract.id

    def test_original_filename_lives_only_in_the_db(
        self, committing_client, committing_db, contract, stub_parser, tmp_storage
    ):
        """§8: на диске — непрозрачный ключ, имя пользователя туда не попадает."""
        stub_parser(payload_for(contract))

        body = upload(
            committing_client, content=xlsx_bytes(), contract_id=contract.id, filename="Смета ГП.xlsx"
        ).json()

        job = committing_db.get(ImportJob, body["id"])
        assert job.filename == "Смета ГП.xlsx"
        assert tmp_storage.exists(job.file_key)
        assert "Смета" not in job.file_key

    def test_amendment_upload_is_a_separate_pair(
        self, committing_client, committing_db, contract, stub_parser
    ):
        stub_parser(payload_for(contract))
        upload(committing_client, content=xlsx_bytes("base"), contract_id=contract.id)

        response = upload(
            committing_client, content=xlsx_bytes("amd"), contract_id=contract.id, amendment_no=1
        )

        assert response.status_code == 202
        assert finished_job(committing_client, response)["status"] == ImportJobStatus.done.value


# ---------------------------------------------------------------------------
#  Правила повторной загрузки (§5)
# ---------------------------------------------------------------------------

class TestReuploadRules:
    def test_same_file_twice_is_idempotent(
        self, committing_client, committing_db, contract, stub_parser
    ):
        """Правило 1: тот же sha256 → существующий job, без нового импорта."""
        holder = stub_parser(payload_for(contract))
        content = xlsx_bytes()
        first = upload(committing_client, content=content, contract_id=contract.id).json()
        assert holder["calls"] == 1

        response = upload(committing_client, content=content, contract_id=contract.id)

        assert response.status_code == 200
        assert response.json()["id"] == first["id"]
        assert holder["calls"] == 1  # парсер повторно не вызывался
        jobs = committing_db.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one()
        assert jobs == 1

    def test_different_file_without_replace_is_409(
        self, committing_client, contract, stub_parser
    ):
        """Правило 2: смета уже загружена, файл другой, replace не передан → 409."""
        stub_parser(payload_for(contract))
        upload(committing_client, content=xlsx_bytes("first"), contract_id=contract.id)

        response = upload(committing_client, content=xlsx_bytes("second"), contract_id=contract.id)

        assert response.status_code == 409
        assert "replace=true" in response.json()["detail"]

    def test_replace_true_swaps_the_estimate(
        self, committing_client, committing_db, contract, stub_parser
    ):
        """Правило 3: replace=true заменяет смету, прежние задания остаются."""
        stub_parser(payload_for(contract))
        first_response = upload(committing_client, content=xlsx_bytes("first"), contract_id=contract.id)
        first = finished_job(committing_client, first_response)

        response = upload(
            committing_client, content=xlsx_bytes("second"), contract_id=contract.id, replace=True
        )

        assert response.status_code == 202
        body = finished_job(committing_client, response)
        assert body["status"] == ImportJobStatus.done.value
        assert body["estimate_id"] != first["estimate_id"]
        assert any("Заменена смета" in w for w in body["warnings"])

        committing_db.expire_all()
        # Аудит: оба задания на месте, старая смета удалена.
        assert committing_db.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one() == 2
        assert committing_db.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 1
        assert committing_db.get(ImportJob, first["id"]).status == ImportJobStatus.done.value

    def test_replace_requires_admin(self, committing_client, contract, stub_parser):
        stub_parser(payload_for(contract))
        upload(committing_client, content=xlsx_bytes("first"), contract_id=contract.id)
        committing_client.auth_state["role"] = UserRole.member

        response = upload(
            committing_client, content=xlsx_bytes("second"), contract_id=contract.id, replace=True
        )

        assert response.status_code == 403
        assert "администратору" in response.json()["detail"]

    def test_member_can_upload(self, committing_client, contract, stub_parser):
        """Загрузка смет — право member (§3)."""
        stub_parser(payload_for(contract))
        committing_client.auth_state["role"] = UserRole.member

        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)

        assert response.status_code == 202

    def test_running_job_blocks_the_same_pair(
        self, committing_client, committing_db, committing_factories, contract, stub_parser
    ):
        """Лок пары держит незавершённое задание — отдаём понятный 409."""
        stub_parser(payload_for(contract))
        committing_factories.ImportJobFactory.create(
            contract=contract, amendment_no=None, status=ImportJobStatus.matching.value
        )
        committing_db.commit()

        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)

        assert response.status_code == 409
        assert "уже выполняется" in response.json()["detail"]

    def test_running_job_of_another_amendment_does_not_block(
        self, committing_client, committing_db, committing_factories, contract, stub_parser
    ):
        """Блокируется ПАРА, а не договор: разные допсоглашения — параллельно (§4)."""
        stub_parser(payload_for(contract))
        committing_factories.ImportJobFactory.create(
            contract=contract, amendment_no=5, status=ImportJobStatus.matching.value
        )
        committing_db.commit()

        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)

        assert response.status_code == 202

    def test_concurrent_upload_of_the_same_pair_is_409_not_500(
        self,
        committing_client,
        committing_db,
        committing_session_factory,
        contract,
        stub_parser,
        tmp_storage,
        monkeypatch,
    ):
        """Гонка между проверкой активного задания и INSERT.

        Проверка и вставка — не одна операция, поэтому два одновременных запроса
        могут оба пройти проверку; второго остановит частичный уникальный индекс
        `uq_import_jobs_active_pair`. Данные защищены, но клиенту это ровно та же
        ситуация, что и синхронная проверка, — значит и ответ обязан быть 409.

        Гонка воспроизводится детерминированно: соперник создаётся из независимой
        сессии в момент сохранения файла, то есть после проверки и до вставки.
        """
        from storage import new_key

        stub_parser(payload_for(contract))
        original_save = tmp_storage.save
        raced = []

        def racing_save(payload: bytes) -> str:
            if not raced:
                raced.append(True)
                with committing_session_factory() as rival:
                    rival.add(
                        ImportJob(
                            contract_id=contract.id,
                            amendment_no=None,
                            filename="rival.xlsx",
                            file_key=new_key(),
                            file_sha256="0" * 64,
                            status=ImportJobStatus.pending.value,
                        )
                    )
                    rival.commit()
            return original_save(payload)

        monkeypatch.setattr(tmp_storage, "save", racing_save)

        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)

        assert response.status_code == 409
        assert "параллельным запросом" in response.json()["detail"]
        # Проигравший не оставил ни задания, ни файла (§5).
        committing_db.expire_all()
        assert (
            committing_db.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one() == 1
        )
        assert list(tmp_storage.root.iterdir()) == []

    def test_no_file_is_left_behind_on_409(
        self, committing_client, committing_db, contract, stub_parser, tmp_storage
    ):
        """§5: файл остаётся только у реально созданного задания."""
        stub_parser(payload_for(contract))
        upload(committing_client, content=xlsx_bytes("first"), contract_id=contract.id)
        files_after_first = set(p.name for p in (tmp_storage.root).iterdir())

        upload(committing_client, content=xlsx_bytes("second"), contract_id=contract.id)

        assert set(p.name for p in tmp_storage.root.iterdir()) == files_after_first

    def test_no_file_is_left_behind_on_idempotent_answer(
        self, committing_client, contract, stub_parser, tmp_storage
    ):
        stub_parser(payload_for(contract))
        content = xlsx_bytes()
        upload(committing_client, content=content, contract_id=contract.id)
        before = set(p.name for p in tmp_storage.root.iterdir())

        upload(committing_client, content=content, contract_id=contract.id)

        assert set(p.name for p in tmp_storage.root.iterdir()) == before

    def test_reupload_after_error_is_allowed(
        self, committing_client, committing_db, contract, stub_parser
    ):
        """Ошибочное задание не держит лок — «повторите загрузку» выполнимо."""
        from parser import EstimateParseError

        stub_parser(error=EstimateParseError("не разобрано"))
        failed = finished_job(
            committing_client,
            upload(committing_client, content=xlsx_bytes("bad"), contract_id=contract.id),
        )
        assert failed["status"] == ImportJobStatus.error.value

        # Тот же файл, но теперь парсер работает.
        stub_parser(payload_for(contract))
        response = upload(committing_client, content=xlsx_bytes("bad"), contract_id=contract.id)

        assert response.status_code == 202
        assert finished_job(committing_client, response)["status"] == ImportJobStatus.done.value


# ---------------------------------------------------------------------------
#  Валидация запроса
# ---------------------------------------------------------------------------

class TestValidation:
    def test_unknown_contract_is_404(self, committing_client, stub_parser):
        response = upload(committing_client, content=xlsx_bytes(), contract_id=10**9)
        assert response.status_code == 404

    def test_non_xlsx_extension_is_400(self, committing_client, contract):
        response = upload(
            committing_client, content=xlsx_bytes(), contract_id=contract.id, filename="смета.xls"
        )
        assert response.status_code == 400
        assert ".xlsx" in response.json()["detail"]

    def test_not_a_zip_is_400(self, committing_client, contract):
        response = upload(committing_client, content=b"just text", contract_id=contract.id)
        assert response.status_code == 400
        assert "ZIP" in response.json()["detail"]

    def test_empty_file_is_400(self, committing_client, contract):
        response = upload(committing_client, content=b"", contract_id=contract.id)
        assert response.status_code == 400

    def test_too_large_file_is_413(self, committing_client, contract, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "UPLOAD_MAX_SIZE_MB", 0)
        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)
        assert response.status_code == 413

    def test_zero_amendment_no_is_422(self, committing_client, contract):
        response = upload(
            committing_client, content=xlsx_bytes(), contract_id=contract.id, amendment_no=0
        )
        assert response.status_code == 422

    def test_nothing_is_stored_when_validation_fails(
        self, committing_client, committing_db, contract, tmp_storage
    ):
        upload(committing_client, content=b"not a zip", contract_id=contract.id)

        assert committing_db.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one() == 0
        assert not tmp_storage.root.exists() or not list(tmp_storage.root.iterdir())


# ---------------------------------------------------------------------------
#  GET /api/v1/import-jobs/{id} и /file
# ---------------------------------------------------------------------------

class TestJobEndpoints:
    def test_status_polling(self, committing_client, contract, stub_parser):
        stub_parser(payload_for(contract))
        job_id = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id).json()["id"]

        response = committing_client.get(f"/api/v1/import-jobs/{job_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == job_id
        assert body["status"] == ImportJobStatus.done.value
        assert set(body["counters"]) == {
            "positions_total",
            "matched_cache",
            "matched_exact",
            "matched_nonposition",
            "to_review",
        }

    def test_unknown_job_is_404(self, committing_client):
        assert committing_client.get("/api/v1/import-jobs/999999").status_code == 404

    def test_file_download_returns_the_original_bytes(
        self, committing_client, contract, stub_parser
    ):
        stub_parser(payload_for(contract))
        content = xlsx_bytes("download-me")
        job_id = upload(committing_client, content=content, contract_id=contract.id).json()["id"]

        response = committing_client.get(f"/api/v1/import-jobs/{job_id}/file")

        assert response.status_code == 200
        assert response.content == content
        # Кириллическое имя — только через RFC 5987.
        assert "filename*=UTF-8''" in response.headers["content-disposition"]

    def test_download_closes_the_storage_handle(
        self, committing_client, contract, stub_parser, tmp_storage, monkeypatch
    ):
        """Хендл хранилища закрывается детерминированно, а не сборщиком мусора.

        На Windows открытый хендл блокирует удаление файла — незакрытая выдача
        мешала бы ретенции §8 в этом же процессе. Проверяется вызов close(), а
        не удаляемость файла: refcounting CPython закрывает брошенный хендл
        «обычно достаточно быстро», и тест на удаление не отличал бы починку от
        везения.
        """
        stub_parser(payload_for(contract))
        job_id = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id).json()["id"]

        spies = []
        original_get = tmp_storage.get

        class SpyHandle:
            def __init__(self, inner):
                self._inner = inner
                self.closed = False

            def read(self, *args):
                return self._inner.read(*args)

            def close(self):
                self.closed = True
                self._inner.close()

        def spying_get(key):
            spy = SpyHandle(original_get(key))
            spies.append(spy)
            return spy

        monkeypatch.setattr(tmp_storage, "get", spying_get)

        response = committing_client.get(f"/api/v1/import-jobs/{job_id}/file")

        assert response.status_code == 200
        assert len(spies) == 1
        assert spies[0].closed, "хендл хранилища не закрыт после выдачи файла"

    def test_purged_file_is_410_not_404(
        self, committing_client, committing_db, committing_factories, tmp_storage
    ):
        """«Нет файла» и «нет задания» — разные ответы: запись job живёт вечно (§8)."""
        from services.maintenance import purge_expired_error_job_files

        key = tmp_storage.save(xlsx_bytes())
        job = committing_factories.ImportJobFactory.create(
            status=ImportJobStatus.error.value, file_key=key
        )
        committing_db.flush()
        moment = utcnow_aware() - timedelta(days=99)
        job.created_at = job.finished_at = moment
        committing_db.commit()
        purge_expired_error_job_files(committing_db, tmp_storage, retention_days=30)
        committing_db.commit()

        response = committing_client.get(f"/api/v1/import-jobs/{job.id}/file")

        assert response.status_code == 410
        assert "ретенции" in response.json()["detail"]
