import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

interface MoneyCellProps {
  /**
   * Десятичная строка из API (AGENTS.md §3) либо число. Строка форматируется
   * без перевода в `number` — иначе последний разряд суммы терялся бы молча.
   */
  value: string | number | null | undefined;
  currency?: string;
  /**
   * Округлить показ до этого числа знаков — **только для вычисленных величин**.
   *
   * Нужно средневзвешенной ставке матрицы (§6): деление `SUM(ставка × вес) /
   * SUM(вес)` на `numeric` даёт двенадцать знаков после запятой, и они изображают
   * точность, которой нет. Прогон стенда показал, что без этого матрица читается
   * как набор случайных цифр.
   *
   * Для **хранимых** ставок и сумм задавать нельзя: утверждённая ставка
   * 1 075,35475 обязана показываться целиком, иначе форма врёт о цифре, по которой
   * идёт торг. Поэтому у параметра нет значения по умолчанию — округление всегда
   * осознанное решение вызывающего.
   *
   * Точное значение при округлении уходит в `title` и остаётся доступным.
   */
  maxFractionDigits?: number;
  className?: string;
}

export function MoneyCell({ value, currency, maxFractionDigits, className }: MoneyCellProps) {
  const shown = formatDecimalMoney(value, currency, maxFractionDigits);
  const exact = maxFractionDigits === undefined ? undefined : formatDecimalMoney(value, currency);

  return (
    <span
      className={cn("font-mono tabular-nums", className)}
      // Подсказка только если округление действительно что-то изменило: иначе она
      // повторяла бы видимое и лишь мешала.
      title={exact !== undefined && exact !== shown ? `Точное значение: ${exact}` : undefined}
    >
      {shown}
    </span>
  );
}
