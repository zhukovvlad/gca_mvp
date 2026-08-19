import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { normalizeDecimalInput } from "@/lib/decimal";
import { coefficientLevel } from "@/lib/inflation";
import {
  useCreateInflationSeries,
  useInflationSeriesValues,
  useUpdateInflationSeries,
} from "@/services/queries";
import type { InflationSeries, InflationSeriesValueInput } from "@/types/domain";

/**
 * Что правит окно. Режим ЗАЯВЛЯЕТСЯ вызывающим, а не выводится из того, нашёлся ли
 * ряд в списке.
 *
 * Прежний контракт («`series === null` — создание») сворачивал два разных состояния
 * в одно значение, и промах поиска молча становился режимом создания. Промах при
 * этом ЗАКОНЕН: список рядов — отдельный запрос, он может ещё не разрешиться, когда
 * сравнение уже пришло, или упасть вовсе. Дефект нашёлся дважды — сначала на
 * архивном ряде (его нет в списке для выбора), потом на порядке завершения запросов,
 * — и оба раза причина была одна: `undefined ?? null`.
 *
 * `mode: "edit"` без объекта означает «правим, но объект ещё не получен» — окно
 * показывает загрузку либо отказ, а НЕ форму создания.
 *
 * **`reason` обязателен, и это продолжение того же урока.** Первая правка развела
 * «создаём» и «объект не получен», но «не получен» осталось ОДНИМ значением на два
 * разных факта: запрос списка ещё идёт — и запрос списка упал. Пока они не
 * различались, упавший список оставлял окно на «Загружаем…» навсегда: `data`
 * никогда не появится, а ждать нечего. Вызывающий знает, какой из двух фактов
 * настал, и обязан его назвать — вывести это из `series === null` нельзя, как
 * нельзя было вывести режим.
 *
 * **Вместе с отказом вызывающий обязан дать `onRetry`, и это тоже не украшение.**
 * Запросом списка владеет он, поэтому повторить его окно не может ничем: закрытие и
 * повторное открытие оставляют упавший запрос как есть — он смонтирован на СТРАНИЦЕ,
 * `refetchOnWindowFocus` выключен, `staleTime` минута. Прежняя редакция звала
 * «попробовать снова», не имея чем, — то есть обещала действие, которого нет. Тип
 * требует способ восстановления рядом с объявлением отказа: иначе следующий
 * вызывающий снова объявит отказ без выхода из него. Найдено третьим кругом ревью.
 */
export type InflationSeriesTarget =
  | { mode: "create" }
  | { mode: "edit"; series: InflationSeries }
  | { mode: "edit"; series: null; reason: "pending" }
  | { mode: "edit"; series: null; reason: "failed"; onRetry: () => void };

interface InflationSeriesDialogProps {
  open: boolean;
  target: InflationSeriesTarget;
  /**
   * Годы, которых не хватило приведению. Вход из баннера отказа открывает окно с
   * уже добавленными пустыми строками этих годов: система знает и ряд, и годы, и
   * отправлять человека набирать их руками значило бы перекладывать на него
   * работу, которую она сделала сама (§2.9).
   */
  missingYears?: number[];
  onOpenChange: (open: boolean) => void;
}

/** Стартовый год у пустой формы — первый год разбега договоров стенда. */
const FIRST_YEAR_SUGGESTION = 2024;

/**
 * Год в ФОРМЕ может быть незаполненным, поэтому здесь он `number | null`, а не
 * `number`, как в теле запроса. `Number("") || 0` превращал пустое поле в ноль —
 * то есть ввести год заново, стерев прежний, было нельзя: поле сразу показывало «0».
 */
type FormYear = number | null;

function parseYear(raw: string): FormYear {
  const digits = raw.replace(/\D/g, "");
  return digits === "" ? null : Number(digits);
}

interface YearRow extends Omit<InflationSeriesValueInput, "year"> {
  year: FormYear;
  /**
   * Ключ строки для React, выданный ОДИН раз при её появлении.
   *
   * Ключом НЕ может быть год: он редактируемый, и при первом же введённом символе
   * ключ менялся бы, React размонтировал строку, а поле теряло фокус — ввести год
   * целиком становилось невозможно. Тесты этого не поймали, потому что нажимали
   * «Добавить год» и правили только коэффициент с источником, а сам год оставляли
   * посчитанным. Найдено внешним ревью.
   */
  key: string;
  /** Уже сохранённый год нельзя убрать из формы: `DELETE` запрещён (§2.10). */
  persisted: boolean;
}

/** Счётчик ключей строк. Монотонный, чтобы ключ не повторился после удаления. */
let rowKeySeq = 0;

function nextRowKey(): string {
  rowKeySeq += 1;
  return `row-${rowKeySeq}`;
}

/**
 * Окно правки ряда индексов инфляции — ОДИН компонент на ТРИ входа (спека §2.12,
 * DoD 34): строка ряда на экране нормативов, полоса уровней на `/compare` и баннер
 * отказа. Вторая форма разошлась бы с первой — тот же довод, которым §2.12 требует
 * одной функции трансляции отказа.
 *
 * **Почему входы с `/compare` вообще нужны:** аналитик видит уровни там же, где
 * числа, и отправлять его в другой раздел за правкой значит рвать задачу пополам.
 * **Почему они требуют осторожности:** ряд ОБЩИЙ, версий у него нет, и правка меняет
 * числа у всех, кто в этот момент смотрит сравнение. Кнопка рядом с числами
 * приглашает подкрутить индекс «под свою выборку» — то самое желание, которое
 * вынесено в границу «персональный гипотетический ряд» (§4). Рамку держит ЭТОТ
 * текст в окне, поэтому окно и обязано быть тем же, а не облегчённой формой на
 * месте.
 */
export function InflationSeriesDialog({
  open,
  target,
  missingYears,
  onOpenChange,
}: InflationSeriesDialogProps) {
  const series = target.mode === "edit" ? target.series : null;
  const values = useInflationSeriesValues(series?.id ?? null);
  // Форма готова, когда это создание — либо когда правка И объект ряда, И его годы
  // уже пришли. Промах поиска больше не может превратиться в создание: режим задан
  // снаружи, и при `mode: "edit"` без объекта окно показывает загрузку.
  const ready = target.mode === "create" || (series !== null && values.data !== undefined);
  /*
    ТРЕТЬЕ состояние окна, а не два. `ready` отвечает только на «данные есть»;
    терминальная ошибка от ожидания им не отличается, и без отдельной ветки окно
    висит на «Загружаем ряд и его годы…» бесконечно. Оба пути ошибки — свои:

    * ряд не получен и получен не будет (`reason: "failed"`) — упал запрос СПИСКА,
      и знает об этом только вызывающий;
    * ряд получен, а его ГОДЫ нет (`values.isError`) — запросом годов владеет само
      окно, поэтому этот путь оно читает само. Ревью назвало только первый.
  */
  const failed =
    target.mode === "edit" && (target.series === null ? target.reason === "failed" : values.isError);

  /*
    Кто владеет упавшим запросом, тот и повторяет его. У списка это вызывающий — он
    отдаёт `onRetry` вместе с самим объявлением отказа; у годов ряда владелец — окно,
    и `refetch` оно зовёт своим. Обе ветки ведут в ОДНУ кнопку: человеку всё равно,
    чей это запрос, ему нужно, чтобы «Повторить» повторяло.

    Пока повтор идёт, окно показывает загрузку, а не продолжает утверждать отказ, — и
    добавлять к `isError` проверку `isFetching` для этого НЕ НАДО. У запроса, который
    ни разу не отдал данных, любой новый `fetch` сам сбрасывает `status` в `pending` и
    гасит `error`: `fetchState()` в `@tanstack/query-core` делает это при
    `data === undefined`. Проверено снятием — добавленное условие не роняло ни одного
    теста, то есть было мёртвой защитой, читающейся как живая (§11 `AGENTS.md`).
    Промежуточное состояние при этом закреплено тестом на воротах: оно обещано
    поверхностью, и чьё оно, читателю неважно.
  */
  const retry =
    target.mode === "edit" && target.series === null && target.reason === "failed"
      ? target.onRetry
      : () => void values.refetch();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        {/*
          Форма — отдельный компонент, и в дереве её нет, пока окно закрыто либо
          годы ещё не пришли. Значит на каждое открытие она монтируется заново, а
          начальные значения задаёт `useState`: эффект, сбрасывающий поля, вызвал
          бы каскадный рендер (тот же приём, что в `ReapproveDialog`).
        */}
        {open && failed ? (
          <>
            <DialogHeader>
              <DialogTitle>Ряд индексов инфляции</DialogTitle>
              <DialogDescription>
                Не удалось загрузить ряд и его годы. Форма не открыта намеренно: она
                показала бы ряд без сохранённых годов, и правка выглядела бы как их
                потеря.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Закрыть
              </Button>
              {/*
                «Повторить» повторяет НАСТОЯЩИЙ запрос, а не закрывает окно с надеждой.
                Прежний текст звал закрыть и попробовать снова — для годов ряда это
                сработало бы (запрос выключается вместе с окном), а для списка нет: он
                живёт на странице и переоткрытием окна не перезапрашивается.
              */}
              <Button type="button" onClick={retry}>
                Повторить
              </Button>
            </DialogFooter>
          </>
        ) : open && ready ? (
          <InflationSeriesForm
            key={series?.id ?? "new"}
            series={series}
            savedYears={values.data ?? []}
            missingYears={missingYears ?? []}
            onOpenChange={onOpenChange}
          />
        ) : (
          <DialogHeader>
            <DialogTitle>Ряд индексов инфляции</DialogTitle>
            <DialogDescription>Загружаем ряд и его годы…</DialogDescription>
          </DialogHeader>
        )}
      </DialogContent>
    </Dialog>
  );
}

function InflationSeriesForm({
  series,
  savedYears,
  missingYears,
  onOpenChange,
}: {
  series: InflationSeries | null;
  savedYears: InflationSeriesValueInput[];
  missingYears: number[];
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState(series?.name ?? "");
  const [note, setNote] = useState(series?.note ?? "");
  const [rows, setRows] = useState<YearRow[]>(() => initialRows(savedYears, missingYears));

  const create = useCreateInflationSeries();
  const update = useUpdateInflationSeries();
  const pending = create.isPending || update.isPending;

  const archived = series !== null && !series.is_active;
  // Неполная строка блокирует «Сохранить», а не уезжает на сервер молча: сервер
  // отверг бы её `422` (год задаётся ТРЕМЯ полями), но человек уже нажал кнопку и
  // получил бы отказ вместо подсказки. Это проверка ФОРМЫ; доменную она не
  // дублирует — год без источника отвергает домен, и его тест живёт на бэкенде.
  const incomplete = rows.some(
    (row) =>
      row.year === null || !Number.isInteger(row.year) || row.year < 1000 ||
      !row.coefficient.trim() || !row.source.trim()
  );
  const canSubmit = name.trim() !== "" && !incomplete && !archived && !pending;

  function patchRow(index: number, patch: Partial<YearRow>) {
    setRows((current) =>
      current.map((row, position) => (position === index ? { ...row, ...patch } : row))
    );
  }

  function addRow() {
    setRows((current) => [
      ...current,
      {
        key: nextRowKey(),
        year: nextYear(current),
        coefficient: "",
        source: "",
        is_forecast: false,
        persisted: false,
      },
    ]);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    // Годы уходят В ТОМ ЖЕ ЗАПРОСЕ, что название и примечание: отправлять их
    // порознь значило бы допустить ряд, исправленный наполовину, — а он общий и
    // без версий, так что «наполовину» означает неверные числа у всех, кто в
    // этот момент смотрит сравнение (§2.12).
    const values: InflationSeriesValueInput[] = rows.map((row) => ({
      year: Number(row.year),
      coefficient: normalizeDecimalInput(row.coefficient),
      source: row.source.trim(),
      is_forecast: row.is_forecast,
    }));
    try {
      if (series === null) {
        await create.mutateAsync({ name: name.trim(), note: note.trim() || null, values });
      } else {
        await update.mutateAsync({
          id: series.id,
          input: { name: name.trim(), note: note.trim() || null, values },
        });
      }
      onOpenChange(false);
    } catch {
      // Отказ уже в тосте: занятое название, повтор года, архивный ряд.
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{series === null ? "Новый ряд индексов" : "Изменить ряд индексов"}</DialogTitle>
        <DialogDescription>
          Ряд общий: правка немедленно меняет числа у всех, кто смотрит сравнение, и
          версий у ряда нет — прежний расчёт останется только в уже выгруженных файлах.
          Подкручивать индекс под свою выборку нельзя.
          {archived && " Ряд в архиве: сначала верните его в активные, потом правьте."}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="inflation-series-name">Название</Label>
          <Input
            id="inflation-series-name"
            value={name}
            placeholder="Росстат, ИПЦ, декабрь к декабрю"
            onChange={(event) => setName(event.target.value)}
          />
          <p className="text-xs text-fg-tertiary">
            Название несёт конкретный показатель, а не «официальный»: им подписывается
            ось сравнения.
          </p>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="inflation-series-note">Примечание</Label>
          <Input
            id="inflation-series-note"
            value={note}
            placeholder="официальная публикация, по РФ"
            onChange={(event) => setNote(event.target.value)}
          />
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Год</TableHead>
              <TableHead>Коэффициент</TableHead>
              <TableHead>Уровень</TableHead>
              <TableHead>Источник</TableHead>
              <TableHead>Прогноз</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, index) => {
              // Расшифровка считается по ВВОДИМОМУ значению, а не по сохранённому:
              // иначе она не защита (§2.4, DoD 25).
              const level = coefficientLevel(row.coefficient);
              return (
                <TableRow key={row.key}>
                  <TableCell>
                    <Input
                      aria-label={`Год строки ${index + 1}`}
                      inputMode="numeric"
                      className="w-20"
                      value={row.year === null ? "" : row.year}
                      disabled={row.persisted}
                      onChange={(event) => patchRow(index, { year: parseYear(event.target.value) })}
                    />
                  </TableCell>
                  <TableCell>
                    <Input
                      aria-label={`Коэффициент за ${row.year}`}
                      inputMode="decimal"
                      className="w-28"
                      placeholder="1.083"
                      value={row.coefficient}
                      onChange={(event) => patchRow(index, { coefficient: event.target.value })}
                    />
                  </TableCell>
                  <TableCell
                    data-testid={`level-${row.year}`}
                    className={
                      level.tone === "bad"
                        ? "text-danger-text font-medium"
                        : level.tone === "ok"
                          ? "text-fg-secondary"
                          : "text-fg-tertiary"
                    }
                  >
                    {level.text}
                  </TableCell>
                  <TableCell>
                    <Input
                      aria-label={`Источник за ${row.year}`}
                      value={row.source}
                      placeholder="бюллетень 01.2026"
                      onChange={(event) => patchRow(index, { source: event.target.value })}
                    />
                  </TableCell>
                  <TableCell>
                    <Checkbox
                      aria-label={`Прогноз за ${row.year}`}
                      checked={row.is_forecast}
                      onCheckedChange={(checked) =>
                        patchRow(index, { is_forecast: checked === true })
                      }
                    />
                  </TableCell>
                  <TableCell>
                    {/*
                      Убрать можно ТОЛЬКО несохранённую строку: удаления годов не
                      существует (§2.10), и кнопка у сохранённого года обещала бы
                      операцию, которой нет. Год, убранный из формы, просто не
                      уйдёт в запрос — а сохранённый останется в ряду.
                    */}
                    {!row.persisted && (
                      <Button
                        type="button"
                        variant="ghost"
                        aria-label={`Убрать строку ${row.year}`}
                        onClick={() =>
                          setRows((current) => current.filter((_, position) => position !== index))
                        }
                      >
                        Убрать
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>

        {/*
          Строка «добавить год» макетом не показана, но без неё вход «Заполнить
          недостающие годы» нечем исполнить: недостающих годов в ряду по
          определению ещё нет (решение плана №3).
        */}
        <div>
          <Button type="button" variant="outline" onClick={addRow} disabled={archived}>
            Добавить год
          </Button>
        </div>

        <p className="text-xs text-fg-tertiary">
          Коэффициент изменения цен, декабрь к декабрю. Например: 1.083 означает рост на
          8,3 %.
        </p>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSubmit}>
            Сохранить
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}

function initialRows(
  savedYears: InflationSeriesValueInput[],
  missingYears: number[]
): YearRow[] {
  const saved: YearRow[] = savedYears.map((value) => ({
    key: nextRowKey(),
    year: value.year,
    coefficient: value.coefficient,
    source: value.source,
    is_forecast: value.is_forecast,
    persisted: true,
  }));
  const known = new Set(saved.map((row) => row.year));
  const added: YearRow[] = missingYears
    .filter((year) => !known.has(year))
    .sort((left, right) => left - right)
    .map((year) => ({
      key: nextRowKey(),
      year,
      coefficient: "",
      source: "",
      is_forecast: false,
      persisted: false,
    }));
  return [...saved, ...added];
}

function nextYear(rows: YearRow[]): number {
  // Год новой строки — следующий за максимальным: годы вводят подряд. Клиентские
  // часы для этого не годятся (`Date.now()` — часы читателя, §2.7), поэтому у
  // пустой формы стартовое значение просто ЗАДАНО; это отправная точка, которую
  // человек правит, а не утверждение о текущем годе.
  // `null` (незаполненный год) в максимум не входит: `Number(null)` дал бы 0, и
  // следующая добавленная строка предложила бы «1».
  const years = rows
    .map((row) => row.year)
    .filter((year): year is number => year !== null && Number.isFinite(year));
  return years.length > 0 ? Math.max(...years) + 1 : FIRST_YEAR_SUGGESTION;
}
