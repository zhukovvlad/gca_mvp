import type { ReactNode } from "react";

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
import {
  CONTROLS_ROW_CLASS,
  CONTROL_CELL_CLASS,
  CONTROL_LABEL_CLASS,
  SEGMENTED_GROUP_CLASS,
  SEGMENTED_ITEM_ACTIVE_CLASS,
  SEGMENTED_ITEM_CLASS,
} from "@/components/ui-domain/controlStyles";
import { cn } from "@/lib/utils";
import type { InflationSeries } from "@/types/domain";

interface InflationControlsProps {
  /**
   * ВСЕ ряды, включая архивные. Архивные не попадают в ОПЦИИ (§2.10), но обязаны
   * быть здесь: по прямой ссылке выбранным может оказаться архивный, и без него
   * триггер не смог бы назвать ряд, которым приведены показанные числа.
   */
  series: InflationSeries[];
  /** Выбранный ряд. `null` — «Выберите ряд»: умолчательного ряда не существует. */
  selectedSeriesId: number | null;
  /**
   * Название выбранного ряда ИЗ ОТВЕТА СРАВНЕНИЯ — запасной источник для триггера.
   *
   * Нужен, когда `series` пуст, потому что запрос списка упал: `find` промахивается,
   * и триггер печатал «Выберите ряд» при выбранном ряде и приведённых числах — то
   * есть говорил неправду о том, чем приведены показанные суммы. Название при этом
   * известно: его вернул сервер вместе с числами, и берётся оно оттуда, а не
   * собирается клиентом.
   */
  selectedSeriesName?: string;
  /**
   * Запрос списка рядов упал. Молчать нельзя: без списка нельзя выбрать другой ряд,
   * и пустой селектор без объяснения читается как «рядов нет».
   */
  listFailed?: boolean;
  /** Приведение включено, то есть параметры лежат в URL. */
  enabled: boolean;
  /** Месяц, РАЗРЕШЁННЫЙ сервером. Пусто, пока сервер не ответил (§2.7). */
  targetMonth: string;
  onSelectSeries: (id: number | null) => void;
  onToggle: (enabled: boolean) => void;
  onChangeMonth: (month: string) => void;
  /**
   * Полоса уровней — ВНУТРИ группы, под тремя контролами (макет, `#levels` внутри
   * `fieldset.group`).
   *
   * Слотом, а не своим запросом: полоса живёт только при сосчитанном приведении, и
   * решение «рисовать или нет» принимает страница — отсутствующий узел вместо
   * `hidden` (см. докстроку `InflationLevelsBar`). Соседним узлом снаружи полоса
   * легла бы на белую подложку карточки и оторвалась бы от группы, которую
   * объясняет.
   */
  children?: ReactNode;
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
 *
 * **Группа выделена акцентной подложкой** (макет, `fieldset.group`), а чипы класса
 * объекта рядом — приглушённой (`.group.plain`). Разница несёт смысл, а не вкус:
 * поправка меняет САМИ ЧИСЛА и обязана объявлять себя на поверхности
 * (`AGENTS.md` §10 v6.10), фильтр по классу меняет только состав выборки.
 */
export function InflationControls({
  series,
  selectedSeriesId,
  selectedSeriesName,
  listFailed = false,
  enabled,
  targetMonth,
  onSelectSeries,
  onToggle,
  onChangeMonth,
  children,
}: InflationControlsProps) {
  const NO_SERIES = "none";

  return (
    <fieldset className="mt-4 rounded-lg border border-accent-border bg-accent-soft px-4 py-3">
      <legend className={cn(CONTROL_LABEL_CLASS, "px-1.5 font-bold text-accent-text")}>
        Поправка на инфляцию
      </legend>

      <div className={CONTROLS_ROW_CLASS}>
        <div className={CONTROL_CELL_CLASS}>
          <span className={CONTROL_LABEL_CLASS}>Режим</span>
          <div role="group" aria-label="Режим приведения" className={SEGMENTED_GROUP_CLASS}>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={cn(SEGMENTED_ITEM_CLASS, !enabled && SEGMENTED_ITEM_ACTIVE_CLASS)}
              aria-pressed={!enabled}
              onClick={() => onToggle(false)}
            >
              Номинал
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={cn(SEGMENTED_ITEM_CLASS, enabled && SEGMENTED_ITEM_ACTIVE_CLASS)}
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

        <div className={CONTROL_CELL_CLASS}>
          <Label htmlFor="inflation-series-select" className={CONTROL_LABEL_CLASS}>
            Ряд индексов
          </Label>
          <Select
            value={selectedSeriesId === null ? NO_SERIES : String(selectedSeriesId)}
            onValueChange={(value) =>
              onSelectSeries(value === NO_SERIES ? null : Number(value))
            }
          >
            <SelectTrigger id="inflation-series-select" className="w-64 bg-surface dark:bg-surface">
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
                    : /*
                        Ряд ВЫБРАН, а названия может не быть: список рядов — отдельный
                        запрос, и на упавшем `find` промахивается. «Выберите ряд» здесь
                        было бы неправдой — приведение работает, числа приведены, ряд
                        назван и подписью, и полосой уровней. Запасное название берётся
                        из ответа сравнения; когда нет и его (отказ приведения плюс
                        упавший список), триггер говорит о себе прямо.
                      */
                      (series.find((row) => String(row.id) === raw)?.name ??
                      selectedSeriesName ??
                      "Название ряда не загрузилось")
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_SERIES}>Выберите ряд</SelectItem>
              {/*
                Архивные ряды в опциях НЕ предлагаются (§2.10) — кроме уже
                выбранного: он пришёл по ссылке, и убрать его из списка значило бы
                показать селектор, в котором нет того, что в нём стоит.
              */}
              {series
                .filter((row) => row.is_active || row.id === selectedSeriesId)
                .map((row) => (
                  <SelectItem key={row.id} value={String(row.id)}>
                    {row.name}
                    {!row.is_active && " (в архиве)"}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          {/*
            Отказ списка называется ЗДЕСЬ, у самого селектора: пустой список опций без
            объяснения читается как «рядов не заведено», а это другой факт — и он
            приглашал бы завести ряд заново. Приведение при этом продолжает работать
            по тому ряду, что уже в адресе: считает его сервер, и список ему не нужен.
          */}
          {listFailed && (
            <p role="status" className="text-2xs text-warning-text">
              Список рядов не загрузился — выбрать другой ряд сейчас нельзя.
            </p>
          )}
        </div>

        <div className={CONTROL_CELL_CLASS}>
          <Label htmlFor="inflation-target-month" className={CONTROL_LABEL_CLASS}>
            В ценах
          </Label>
          <Input
            id="inflation-target-month"
            type="month"
            className="w-40 bg-surface dark:bg-surface"
            value={targetMonth}
            disabled={selectedSeriesId === null}
            onChange={(event) => onChangeMonth(event.target.value)}
          />
        </div>
      </div>

      {children}
    </fieldset>
  );
}
