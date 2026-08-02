"""Unit-тесты доменных ошибок обработки (S0-4)."""
from decimal import Decimal

from processing import PermanentError, ProcessingError, TransientError


def test_transient_is_processing_error():
    """TransientError — подкласс ProcessingError (общий except ловит обе)."""
    assert issubclass(TransientError, ProcessingError)
    assert issubclass(PermanentError, ProcessingError)


def test_error_carries_accounting():
    """Ошибка несёт накопленный учёт платных вызовов для error-пути (S0-9, §2.5)."""
    err = TransientError("timeout", cost_usd=Decimal("0.0015"), paid_calls=1)
    assert err.cost_usd == Decimal("0.0015")
    assert err.paid_calls == 1
    assert err.message == "timeout"


def test_error_accounting_defaults_zero():
    """Ошибка без платного вызова несёт нулевой учёт и не задаёт http_status."""
    err = PermanentError("no api key")
    assert err.cost_usd == Decimal(0)
    assert err.paid_calls == 0
    assert err.http_status is None


def test_error_http_status_hint():
    """Доменная ошибка может нести подсказку HTTP-статуса для эндпоинта (AC-S0-8)."""
    err = PermanentError("too many pages", http_status=413)
    assert err.http_status == 413


def test_message_is_str_of_exception():
    """str(err) возвращает сообщение — для логов и last_error."""
    assert str(PermanentError("boom")) == "boom"
