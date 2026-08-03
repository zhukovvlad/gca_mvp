"""`ClosingStreamingResponse`: хендл хранилища закрывается при ЛЮБОМ исходе.

Дефект второго внутреннего круга ревью фазы 4: `finally` внутри генератора
закрывал хендл только при полном прочтении. При разрыве соединения `send()`
падает, Starlette перестаёт потреблять генератор и хранит его в
`body_iterator`, — `finally` не выполняется, пока жив объект ответа, и закрытие
снова доставалось сборщику мусора. Ключ к честности этих тестов — объект ответа
удерживается живым локальной переменной: иначе refcounting CPython закрыл бы
брошенный хендл сразу после выхода, и тест не отличал бы починку от везения.
"""
from __future__ import annotations

import asyncio
import contextlib
import io

from routers.import_jobs import ClosingStreamingResponse

SCOPE = {"type": "http", "method": "GET", "path": "/f", "headers": []}


class SpyHandle(io.BytesIO):
    """Файл-шпион: помнит, закрывали ли его."""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.close_calls = 0

    def close(self) -> None:  # noqa: D102 — поведение описано классом
        self.close_calls += 1
        super().close()


async def _never_disconnects():
    await asyncio.Event().wait()  # http.disconnect так и не приходит


class TestClientDisconnect:
    async def test_handle_is_closed_while_the_response_object_is_still_alive(self):
        spy = SpyHandle(b"PK\x03\x04" * 4096)
        response = ClosingStreamingResponse(spy, media_type="application/octet-stream")

        async def failing_send(message):
            if message["type"] == "http.response.start":
                return
            raise OSError("клиент разорвал соединение")

        # Тип исключения наружу зависит от версий starlette/anyio (может быть
        # ExceptionGroup) — предмет теста не он, а судьба хендла.
        with contextlib.suppress(BaseException):
            await response(SCOPE, _never_disconnects, failing_send)

        # `response` жив — GC хендл закрыть не мог. Только finally __call__.
        assert spy.close_calls >= 1, "хендл не закрыт после разрыва соединения"
        assert response._handle is spy


class TestNormalCompletion:
    async def test_full_read_sends_all_bytes_and_closes(self):
        payload = b"PK\x03\x04" + bytes(range(256)) * 1024  # больше одного чанка
        spy = SpyHandle(payload)
        response = ClosingStreamingResponse(spy, media_type="application/octet-stream")

        received: list[bytes] = []

        async def collecting_send(message):
            if message["type"] == "http.response.body":
                received.append(message.get("body", b""))

        await response(SCOPE, _never_disconnects, collecting_send)

        assert b"".join(received) == payload
        assert spy.close_calls >= 1
