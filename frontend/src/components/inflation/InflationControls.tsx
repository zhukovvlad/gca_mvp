import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { InflationSeries } from "@/types/domain";

interface InflationControlsProps {
  series: InflationSeries[];
  /** Выбранный ряд. `null` — «Выберите ряд»: умолчательного ряда не существует. */
  selectedSeriesId: number | null;
  /** Приведение включено, то есть параметры лежат в URL. */
  enabled: boolean;
  /** Месяц, РАЗРЕШЁННЫЙ сервером. Пусто, пока сервер не ответил (§2.7). */
  targetMonth: string;
  onSelectSeries: (id: number | null) => void;
  onToggle: (enabled: boolean) => void;
  onChangeMonth: (month: string) => void;
}

/**
 * Группа «Поправка на инфляцию» на `/compare` — ТРИ элемента и ОДНА группа
 * (спека §2.12).
 *
 * Группа отдельная, а не три поля вперемешку с «НДС», по замечанию пользователя по
 * макету: ряд без режима и месяца ничего не значит, а поставленный между «НДС» и
 * «Инфляцией» он читается как относящийся к НДС. Порядок внутри — режим, ряд,
 * месяц.
 *
 * **Поле месяца до первого ответа сервера ПУСТО.** Показывать в нём текущий месяц
 * нельзя: текущий определяет сервер в названной таймзоне (§2.7), и предзаполнение
 * означало бы, что его вычислил клиент, — то есть два человека получили бы два
 * ответа. Пустое поле при выбранном ряде читается как «текущий месяц, разрешит
 * сервер».
 */
export function InflationControls({
  series,
  selectedSeriesId,
  enabled,
  targetMonth,
  onSelectSeries,
  onToggle,
  onChangeMonth,
}: InflationControlsProps) {
  const NO_SERIES = "none";

  return (
    <fieldset className="rounded-lg border border-border px-4 py-3">
      <legend className="px-1 text-2xs font-semibold tracking-wide text-fg-tertiary uppercase">
        Поправка на инфляцию
      </legend>

      <div className="flex flex-wrap items-end gap-4">
        <div className="grid gap-1">
          <span className="text-2xs text-fg-tertiary">Режим</span>
          <div
            role="group"
            aria-label="Режим приведения"
            className="inline-flex overflow-hidden rounded-lg border border-border"
          >
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="rounded-none"
              aria-pressed={!enabled}
              onClick={() => onToggle(false)}
            >
              Номинал
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="rounded-none"
              aria-pressed={enabled}
              // Пока ряд не выбран, приводить нечем: умолчательного ряда не
              // существует, справочник создаётся пустым и рядов может быть
              // несколько (§2.12).
              disabled={selectedSeriesId === null}
              onClick={() => onToggle(true)}
            >
              Привести
            </Button>
          </div>
        </div>

        <div className="grid gap-1">
          <Label htmlFor="inflation-series-select" className="text-2xs text-fg-tertiary">
            Ряд индексов
          </Label>
          <Select
            value={selectedSeriesId === null ? NO_SERIES : String(selectedSeriesId)}
            onValueChange={(value) =>
              onSelectSeries(value === NO_SERIES ? null : Number(value))
            }
          >
            <SelectTrigger id="inflation-series-select" className="w-64">
              {/*
                `SelectValue` РЕНДЕР-ФУНКЦИЕЙ, а не `placeholder`-ом: без неё
                триггер печатает сырое значение, то есть `id` ряда — «1» вместо
                названия. Это идиома проекта (`ContractsPage`, селектор класса), и
                дефект нашёлся только замером в браузере: компонентный тест кликал
                по опции и не смотрел, что показывает сам триггер.
              */}
              <SelectValue>
                {(raw) =>
                  !raw || raw === NO_SERIES
                    ? "Выберите ряд"
                    : (series.find((row) => String(row.id) === raw)?.name ?? "Выберите ряд")
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_SERIES}>Выберите ряд</SelectItem>
              {series.map((row) => (
                <SelectItem key={row.id} value={String(row.id)}>
                  {row.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1">
          <Label htmlFor="inflation-target-month" className="text-2xs text-fg-tertiary">
            В ценах
          </Label>
          <Input
            id="inflation-target-month"
            type="month"
            className="w-40"
            value={targetMonth}
            disabled={selectedSeriesId === null}
            onChange={(event) => onChangeMonth(event.target.value)}
          />
        </div>
      </div>
    </fieldset>
  );
}
