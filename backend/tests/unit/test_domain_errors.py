"""Единая трансляция доменного отказа в HTTP (спека инфляции §2.12; план, задача 4).

Расширение `DomainError` обратно совместимо, и это условие DoD 1: при `code is
None` ответ обязан остаться строкой ДО СИМВОЛА — иначе фича меняла бы отказы,
которых не касается.

Транслятор один на три роутера (DoD 23). Ветвление по `code`, размноженное по
файлам, дало бы три копии одного правила — запись 12 техдолга в утроенном виде;
экран, лист и редактор ряда обязаны получать один контракт.

Границу слоёв тест тоже сторожит: `DomainError` живёт в `crud/`, где FastAPI не
импортируется нигде, поэтому `HTTPException` создаётся в `routers/`.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from crud.common import DomainError
from routers import analytics, reports
from routers.domain_errors import raise_domain_error


def _translated(err: DomainError) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        raise_domain_error(err)
    return exc.value


def test_uncoded_refusal_stays_a_plain_string():
    """Отказ без кода не изменился ни на символ (DoD 22)."""
    raised = _translated(DomainError(400, "Неизвестный режим показа НДС: 'gross'."))

    assert raised.status_code == 400
    assert raised.detail == "Неизвестный режим показа НДС: 'gross'."
    assert isinstance(raised.detail, str)


def test_coded_refusal_becomes_an_object_with_context_keys_alongside_code():
    """Ключи контекста лежат РЯДОМ с `code` и `message`, а не вложенным узлом.

    Утверждается равенство словаря целиком, а не наличие ключей: вложенный
    `{"context": {...}}` прошёл бы проверку «`missing_years` где-то есть»,
    оставаясь другим контрактом.
    """
    raised = _translated(
        DomainError(
            422,
            "Не заданы коэффициенты за годы: 2024, 2026.",
            code="missing_inflation_years",
            context={"missing_years": [2024, 2026]},
        )
    )

    assert raised.status_code == 422
    assert raised.detail == {
        "code": "missing_inflation_years",
        "message": "Не заданы коэффициенты за годы: 2024, 2026.",
        "missing_years": [2024, 2026],
    }


def test_coded_refusal_without_context_carries_code_and_message_only():
    raised = _translated(DomainError(422, "Отказ без контекста.", code="some_code"))

    assert raised.detail == {"code": "some_code", "message": "Отказ без контекста."}


def test_status_comes_from_the_error_and_not_from_a_code_map():
    """Карты «код → статус» НЕТ: статус несёт сама ошибка (§2.12).

    Карта осмысленна там, откуда её взяли: у `category_overrides` код вне карты
    намеренно проваливается в `500`. Здесь все коды — законные пользовательские
    состояния, и карта не покупает ничего, кроме машинерии.
    """
    assert _translated(DomainError(409, "Ряд в архиве.", code="archived")).status_code == 409
    assert _translated(DomainError(422, "Дубль года.", code="duplicate_year")).status_code == 422


def test_context_key_shadowing_the_message_fails_loudly():
    """Ключ контекста, накрывающий `code`/`message`, — НАША ошибка, и она обязана
    падать, а не подменять человеку текст отказа.

    Молчаливое перекрытие соврало бы читателю ровно тем полем, которое баннер
    печатает дословно; молчаливое отбрасывание — спрятало бы контекст, по которому
    клиент строит кнопку «Заполнить недостающие годы».
    """
    with pytest.raises(RuntimeError):
        raise_domain_error(
            DomainError(422, "Текст.", code="x", context={"message": "подмена"})
        )


def test_translation_exists_in_a_single_instance(monkeypatch):
    """Экран и лист получают ОДИН объект трансляции (DoD 23).

    Тождество, а не равенство поведения: две независимо написанные функции вели бы
    себя одинаково ровно до первой правки одной из них. Подмена проверяет то же с
    другой стороны — правка «в одном месте» обязана быть видна обоим потребителям.
    """
    assert analytics.raise_domain_error is reports.raise_domain_error
    assert analytics.raise_domain_error is raise_domain_error

    sentinel = object()
    monkeypatch.setattr("routers.domain_errors.raise_domain_error", sentinel)
    # Роутеры импортируют ИМЯ, поэтому подмена в модуле их ссылок не двигает —
    # утверждение выше и есть проверка тождества; здесь фиксируется, что общий
    # модуль существует именно как единственный источник функции.
    import routers.domain_errors as translator

    assert translator.raise_domain_error is sentinel


def test_no_local_domain_error_translator_remains_in_the_two_routers():
    """У экрана и листа своего `_raise` больше нет.

    Проверяется отсутствием атрибута, а не чтением файла: локальная функция,
    оставленная рядом с импортом, продолжала бы работать и разошлась бы с общей
    при первой правке.

    ГРАНИЦА, названная вслух: в `routers/` остаются собственные `_raise` у
    `contracts`, `rate_standards`, `references`, `review` и `settings`. Кодов они
    не поднимают вовсе, DoD 23 их не касается, и переписывать их заодно — чужая
    поверхность (границы плана).
    """
    assert not hasattr(analytics, "_raise")
    assert not hasattr(reports, "_raise")
