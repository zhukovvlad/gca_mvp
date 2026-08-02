from decimal import ROUND_HALF_UP, Decimal


def money_round(value: Decimal | float | str | int, places: int = 2) -> Decimal:
    """Округляет финансовые значения по правилам РФ (ROUND_HALF_UP, 0.5 → вверх).

    Арифметическое (не банковское) округление — для сверки с УПД/1С/ФНС.
    Принимает любой базовый тип; float конвертируется через str(), что отсекает
    бинарную микропогрешность (Decimal(0.1) != Decimal("0.1")).
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    exp = Decimal(f"0.{'0' * places}") if places > 0 else Decimal("1")
    return value.quantize(exp, rounding=ROUND_HALF_UP)
