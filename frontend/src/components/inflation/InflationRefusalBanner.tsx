import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

interface InflationRefusalBannerProps {
  /** Машинный код отказа: он выбирает ПОВЕДЕНИЕ, а не только текст. */
  code: string;
  /** Сообщение сервера. Печатается ДОСЛОВНО — клиент его не пересобирает. */
  message: string;
  /** Название выбранного ряда: у клиента оно уже есть, из списка выбора. */
  seriesName?: string;
  /** Недостающие годы — только у `missing_inflation_years`. */
  missingYears?: number[];
  /** Кнопка правки — только `admin` (§2.10, DoD 35). */
  canEdit: boolean;
  onFillMissingYears: (years: number[]) => void;
}

export const MISSING_YEARS_CODE = "missing_inflation_years";
export const AMENDMENT_DATE_CODE = "amendment_date_missing";

/**
 * Баннер отказа приведения (спека §2.9, §2.12).
 *
 * **Общий каркас, ДВА разных текста по коду, и кнопка только у одного из них.**
 * У `missing_inflation_years` есть «Заполнить недостающие годы»: система уже знает
 * и ряд, и годы, и отправлять человека искать раздел значит перекладывать на него
 * работу, которую она сделала сама. У `amendment_date_missing` кнопки НЕТ —
 * правкой ряда это не лечится, и предлагать её значило бы звать человека делать
 * работу, которая ничего не исправит.
 *
 * **`message` печатается дословно.** Он уже называет договоры и номера ДС; в
 * контексте лежат `estimate_ids`, но «ДС №1» без номера договора ничего не
 * опознаёт — он есть у каждого второго договора выборки. Человеческую формулировку
 * собрал СЕРВЕР, клиент её печатает: вторая сборка того же текста на клиенте
 * разошлась бы с серверной — тот же довод, которым §2.12 требует одной функции
 * трансляции.
 *
 * Номинальные числа при этом показаны, параметры URL СОХРАНЕНЫ (пользователь обязан
 * видеть, какой ряд и какая цель не сработали), а переключатель визуально выключен.
 */
export function InflationRefusalBanner({
  code,
  message,
  seriesName,
  missingYears,
  canEdit,
  onFillMissingYears,
}: InflationRefusalBannerProps) {
  const canFill =
    code === MISSING_YEARS_CODE && canEdit && (missingYears?.length ?? 0) > 0;

  return (
    <div
      role="alert"
      data-testid="inflation-refusal"
      className="mt-3 flex flex-wrap items-start gap-3 rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 grow">
        <p>
          Приведение не выполнено. {message}
          {code === MISSING_YEARS_CODE && seriesName && ` Выбран ряд «${seriesName}».`}
        </p>
        <p className="mt-1 text-xs">
          Числа показаны номинально, без приведения. Выбранные ряд и месяц остались в
          адресе.
        </p>
      </div>
      {canFill && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onFillMissingYears(missingYears ?? [])}
        >
          Заполнить недостающие годы
        </Button>
      )}
    </div>
  );
}
