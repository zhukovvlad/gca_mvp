"""Тесты lifespan: init провайдера до sweep, teardown в finally."""
import pytest

import llm
import main


@pytest.mark.asyncio
async def test_lifespan_init_before_sweep_and_teardown(monkeypatch):
    """init_provider — ДО startup-sweep (fail-fast раньше мутаций БД); reset — в конце."""
    calls: list[str] = []
    monkeypatch.setattr(main, "_sweep_stuck_documents", lambda: calls.append("sweep") or 0)
    monkeypatch.setattr(main.s3, "ensure_bucket", lambda: calls.append("s3"))
    monkeypatch.setattr(llm, "init_provider", lambda s: calls.append("init"))
    monkeypatch.setattr(llm, "reset_provider", lambda: calls.append("reset"))
    async with main.lifespan(main.app):
        pass
    assert calls.index("init") < calls.index("sweep")
    assert calls[-1] == "reset"


@pytest.mark.asyncio
async def test_lifespan_teardown_on_body_exception(monkeypatch):
    """reset_provider вызывается даже если тело контекста бросило исключение."""
    calls: list[str] = []
    monkeypatch.setattr(main, "_sweep_stuck_documents", lambda: 0)
    monkeypatch.setattr(main.s3, "ensure_bucket", lambda: None)
    monkeypatch.setattr(llm, "init_provider", lambda s: None)
    monkeypatch.setattr(llm, "reset_provider", lambda: calls.append("reset"))
    with pytest.raises(RuntimeError):
        async with main.lifespan(main.app):
            raise RuntimeError("boom")
    assert calls == ["reset"]
