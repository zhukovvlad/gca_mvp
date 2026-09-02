import type { ReactNode } from "react";

import { StatusPill, type StatusTone } from "@/components/ui-domain/StatusPill";
import { TableCell } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDecimalMoney, roundDecimalPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CellState, StageSummaryCell, StageSummaryChange, StageSummaryTotalCell } from "@/types/domain";

import { KIND_LABEL, REASON_LABEL, STATE_LABEL } from "./cellCopy";
import { cellAlignClass } from "./cellLayout";

/**
 * Ячейка таблицы свода и её исчерпывающий значок изменения (спека
 * 2026-08-27-stage-summary-design.md §2.5–§2.7, §2.9, §2.13; задача 8 плана).
 *
 * Рендер — строго ПО ДАННЫМ (§2.14, Global 13): состояние ячейки, вид
 * изменения, его направление и причина недоступности суммы приходят с сервера
 * каждое своим полем и рисуются своей веткой — ни одно не выводится из
 * другого. В частности состояние (`state`) выигрывает у суммы (`amount`)
 * всегда, даже если они противоречат друг другу (тест
 * `StageSummaryTable.test.tsx`, «по полю, а не по сумме»).
 */

/**
 * Тон пилюли состояния — «снято» тревожнее «не оценивалась». `absent` сюда не
 * входит (fix round 3, п.2): на макете «отсутствует» — плоский прочерк, не
 * пилюля, потому что это факт о ФАЙЛЕ (статьи там нет вовсе), а не о цене —
 * пилюля рядом с «снято»/«не оценивалась» читалась бы как утверждение того же
 * рода про цену, которого здесь нет.
 */
const CELL_STATE_TONE: Record<Exclude<CellState, "amount" | "absent">, StatusTone> = {
  removed: "warning",
  not_evaluated: "neutral",
};

/** Направление → тон текста. `flat` НЕ окрашивается как рост — своим тоном. */
function directionToneClass(direction: StageSummaryChange["direction"]): string | undefined {
  if (direction === "up") return "text-accent-text";
  if (direction === "down") return "text-danger-text";
  if (direction === "flat") return "text-fg-tertiary";
  return undefined;
}

/**
 * Значок изменения — используется в ячейках таблицы, в KPI «Последний к
 * первому» и под столбиками трассы: тот же компонент, а не копия под другую
 * подпись.
 *
 * `dashOnNone` — что рисовать, когда изменения НЕТ по построению (`kind =
 * 'none'` без пилюли): прочерк или ничего. Проп назван по тому, что делает, а
 * не по месту вызова (прежнее имя `inKpi` описывало ОДНОГО вызывающего, и
 * второму — трассе, где прочерк нужен у первой колонки, — пришлось бы
 * передавать «я KPI», не будучи им).
 *
 * `switch` по `kind` завершается веткой `never` — если контракт добавит новый
 * вид изменения, а эта ветка не будет расширена, сборка типов упадёт здесь, а
 * не отрисует новый вид пустотой.
 */
export function ChangeBadge({ change, dashOnNone }: { change: StageSummaryChange; dashOnNone?: boolean }) {
  const toneClass = directionToneClass(change.direction);

  let content: ReactNode;
  switch (change.kind) {
    case "percent": {
      const pct = roundDecimalPercent(change.value);
      content = pct?.text ?? "—";
      break;
    }
    case "abs_only":
      content = (
        <>
          {formatDecimalMoney(change.value)} Δ, без %
        </>
      );
      break;
    case "appeared":
    case "reappeared":
    case "disappeared":
      content = <StatusPill tone="neutral" label={KIND_LABEL[change.kind]} />;
      break;
    case "removed":
      // Тон «снято» — тревожный, как у ОДНОИМЁННОГО состояния ячейки
      // (`CELL_STATE_TONE.removed`) и как на макете гейта 1: там «снято»
      // печатается `pill warn` и в ячейке этапа, и в колонке «Торг»
      // (`table.pass`, строки статей «14» и «12»), а «появилась» / «не
      // оценивалась» — обычной пилюлей. Реализация красила ВСЕ структурные
      // виды нейтрально, и «Торг» расходился с макетом тоном; в ячейке этого
      // видно не было, потому что там значок гасится как повтор состояния.
      // Остальные три вида остаются нейтральными: они сообщают о движении, а
      // не о потере.
      //
      // Тон меняется у ОБЩЕГО компонента, то есть достаёт до всех его
      // вызывающих: ячейка статьи (там значок гасится как повтор состояния),
      // «Торг», трасса и KPI «Последний к первому». Для трассы и KPI это
      // сегодня недостижимо — там изменение считается числовым правилом
      // (`_numeric_change`) и структурных видов не бывает вовсе, — но если
      // контракт это изменит, тон приедет туда осознанно, а не случайно.
      content = <StatusPill tone="warning" label={KIND_LABEL.removed} />;
      break;
    case "none":
      content = dashOnNone ? "—" : null;
      break;
    default: {
      // Исчерпывающая проверка компилятором: новый ChangeKind обязан получить
      // свою ветку выше — иначе сборка типов падает здесь.
      const exhaustive: never = change.kind;
      throw new Error(`Неизвестный вид изменения: ${String(exhaustive)}`);
    }
  }

  return (
    <span
      data-testid="change"
      className={toneClass}
      title={change.reason ? REASON_LABEL[change.reason] : undefined}
    >
      {content}
    </span>
  );
}

/**
 * Повторяет ли значок изменения СЛОВО, которое уже сказало состояние ячейки.
 *
 * Найдено пользователем на стенде 28.08.2026: ячейка, где статью сняли на этом
 * шаге, показывала «снято» ДВАЖДЫ — пилюлей состояния (`state = 'removed'`,
 * §2.5) и под ней пилюлей вида изменения (`kind = 'removed'`, §2.6). Факты
 * разные (состояние говорит «цены нет сейчас, а была раньше»; вид — «сняли
 * именно на этом шаге»), но НАПИСАНЫ они одним и тем же словом, и читатель
 * видит две одинаковые пилюли. Макет гейта 1 в такой ячейке рисует ОДНУ, тоном
 * состояния (`table.pass`, `pill warn`), — реализация от макета отошла.
 *
 * Для `removed` гашение в ячейке статьи — ПРАВИЛО, а не редкий случай:
 * `KIND_REMOVED` сервер возвращает единственной веткой `amount → removed`
 * (`backend/services/stage_summary.py`, `change_between`), то есть
 * `kind = 'removed'` влечёт `state = 'removed'` всегда, и второй пилюли в
 * ячейке не бывает никогда. Видимым этот вид остаётся в колонке «Торг», где
 * состояния рядом нет.
 *
 * Правило сформулировано через САМИ СЛОВАРИ, а не списком пар: совпало слово —
 * второй раз не печатаем. Так подпись, переименованная в `cellCopy.ts`, не
 * заведёт молча новый дубль и не отключит нужный значок. Сегодня совпадение
 * ровно одно — `STATE_LABEL.removed === KIND_LABEL.removed === "снято"`, и это
 * проверено тестом (`StageSummaryTable.test.tsx`), а не глазами.
 *
 * Виды без пилюли (`percent`, `abs_only`, `none`) сюда не попадают: они печатают
 * число или ничего, повторить слово состояния им нечем. Состояние `amount` тоже:
 * у него нет подписи вовсе, печатается сумма.
 *
 * Оговорка о границе самого приёма: литерал «—» рождается на экране и ВНЕ
 * словарей — `ChangeBadge` печатает его при `kind = 'percent'`, если
 * `roundDecimalPercent` вернул `null`, а `STATE_LABEL.absent` — тоже «—».
 * Сравнение по словарям такую пару не увидит. Сегодня она недостижима (у
 * `absent` нет суммы, процент из неё не строится, да и `percent` отсекается
 * ветками выше), но правило по словарям — не про ВЕСЬ экран, а про подписи, и
 * это его граница.
 */
function changeRepeatsState(state: CellState, change: StageSummaryChange): boolean {
  if (state === "amount") return false;
  if (change.kind === "percent" || change.kind === "abs_only" || change.kind === "none") return false;
  return KIND_LABEL[change.kind] === STATE_LABEL[state];
}

/** Подпись подсказки неполноты (§2.1 контракта): обе величины и, если есть,
 *  отдельный счётчик неконечных значений — одна причина не заменяет другую. */
function incompletenessLabel(rows: StageSummaryCell["rows"]): string {
  const base = `Сумма неполна: учтено ${rows.rows_with_amount} из ${rows.row_count} строк`;
  return rows.rows_not_finite > 0 ? `${base}; неконечных значений: ${rows.rows_not_finite}` : base;
}

/**
 * Ячейка таблицы свода — статья классификатора в колонке одного этапа.
 *
 * Прежде компонент принимал ещё и `extra` (дополнительную строку под значком
 * изменения) — им пользовалась строка «Итого». С ревизии §2.16 у итога СВОЙ
 * компонент {@link SummaryTotalCell}, и проп остался мёртвым вместе с абзацем
 * докстроки, который объяснял несуществующий механизм: снят 28.08.2026.
 *
 * Задача 13 плана этапного разноса (спека
 * 2026-09-01-round-unallocated-design.md §2.8) возвращает `extra` — ТЕМ ЖЕ
 * слотом, что уже несёт {@link SummaryTotalCell}: строка «Нераспределённое»
 * кладёт туда ссылку «разнести →», когда у ячейки есть нераспределённые
 * строки (`StageSummaryTable` решает условие показа, эта функция ничего о
 * разносе не знает — только рисует переданный узел третьей строкой ячейки).
 */
export function SummaryCell({ cell, extra }: { cell: StageSummaryCell; extra?: ReactNode }) {
  const { state, amount, amount_unavailable_reason, rows, change } = cell;
  const incomplete = rows.rows_with_amount < rows.row_count;
  const repeatsState = changeRepeatsState(state, change);
  // Число ячейка показывает только в состоянии `amount` и только когда сумма не
  // погашена неизвестной базой НДС — в остальных случаях на её месте пилюля или
  // прочерк, и выравнивать по верхней строке нечего.
  const showsNumber = state === "amount" && !amount_unavailable_reason;
  // Вторая ВИДИМАЯ строка: значок изменения (при `kind = 'none'` он в ячейке не
  // рисует ничего, поэтому строкой не считается) либо подпись `extra`. Условие
  // здесь ОБЯЗАНО быть тем же, каким ниже проверяется САМ рендер (`{extra &&
  // …}`) — `Boolean(extra)`, а не `extra !== undefined`: render-prop, вызванный
  // с условием (как `allocateLink` строки «Нераспределённое», задача 13
  // этапного разноса), законно возвращает `null` — не только `undefined` —
  // когда решает не рисовать ничего, и `null !== undefined` разошёлся бы с
  // тем, что на самом деле легло в DOM (найдено внешним ревью: `hasSecondLine`
  // резервировал вторую строку разметки на пустом слоте на каждом полном
  // рендере таблицы, где `allocateLink` тестов отвечает `null`).
  const hasSecondLine = (!amount_unavailable_reason && !repeatsState && change.kind !== "none") || Boolean(extra);

  /*
    Примитив `TableCell`, а не сырой `<td>`: у примитива `p-2`, и до этой правки
    ячейки этапов были единственными в строке БЕЗ отступов — «Торг», «Вклад» и
    колонка классификатора идут через примитив. Пока всё нечисловое
    центрировалось, разница пряталась; после перехода на «число по верхней
    строке» она вылезла числом — замер: первая строка ячейки этапа на 8 px выше
    первой строки «Вклада в итог», ровно на величину чужого `padding-top`.
    Выравниванием такое не лечится — лечится одинаковой коробкой.
  */
  return (
    <TableCell
      className={cn("text-right tabular-nums", cellAlignClass(showsNumber, hasSecondLine))}
      title={amount_unavailable_reason ? REASON_LABEL[amount_unavailable_reason] : undefined}
    >
      <div className="flex items-center justify-end gap-1">
        {state === "amount" ? (
          // `amount_unavailable_reason` гасит ТОЛЬКО показанную сумму — ровно
          // то, чего база НДС лишает (спека §2.5, §2.8; AGENTS.md §10): для
          // остальных состояний числа никогда не было, поэтому у них нечего
          // withhold-ить, и ветка ниже их не касается (fix round review PR,
          // дефект «состояние пропадает в недоступной колонке»).
          // Условие — тот же `showsNumber`, что выбирает выравнивание: два
          // независимых чтения одного признака умеют разойтись, одно — нет.
          showsNumber ? (
            <span>{formatDecimalMoney(amount)}</span>
          ) : (
            <StatusPill tone="neutral" label="нет базы НДС" />
          )
        ) : state === "absent" ? (
          // Плоский прочерк — как на макете (`table.pass`, статья "15"):
          // «отсутствует» говорит, что статьи нет в файле вовсе, а не что-то
          // о её цене, и пилюля здесь читалась бы неверно (fix round 3, п.2).
          // Неизвестная база НДС ничего не меняет: у `absent` и так нет суммы.
          <span data-testid="cell-dash" className="text-fg-tertiary">
            {STATE_LABEL.absent}
          </span>
        ) : (
          // `removed`/`not_evaluated`: состояние рисуется как обычно и при
          // неизвестной базе НДС — сумма и состояние гасятся раздельно (§2.5:
          // «состояние не зависит от ставки НДС»). Причина недоступности суммы
          // уже несётся `title` самой `<td>` (ниже) — второй, видимой рядом с
          // пилюлей подписи макет не показывает ни для одного состояния.
          <StatusPill tone={CELL_STATE_TONE[state]} label={STATE_LABEL[state]} />
        )}
        {incomplete && (
          <Tooltip>
            <TooltipTrigger aria-label={incompletenessLabel(rows)} className={cn("cursor-help text-warning")}>
              ◐
            </TooltipTrigger>
            <TooltipContent>{incompletenessLabel(rows)}</TooltipContent>
          </Tooltip>
        )}
      </div>
      {/*
        `text-2xs` (11 px против 13 px у суммы) — как на макете гейта 1, где
        строка изменения объявлена `.dp { font-size: 11px }` при 13 px у самой
        таблицы; реализация это ограничение потеряла, и процент читался таким же
        крупным, как сумма над ним. Просьба пользователя 28.08.2026 совпала с
        макетом. Пилюли размер не меняют: `StatusPill` и так несёт `text-2xs`.
      */}
      {!amount_unavailable_reason && !repeatsState && (
        <div className="mt-0.5 text-2xs font-normal">
          <ChangeBadge change={change} />
        </div>
      )}
      {extra && <div className="mt-0.5 text-2xs font-normal">{extra}</div>}
    </TableCell>
  );
}

/**
 * Ячейка строки «Итого» — СВОЙ компонент, не переиспользование {@link SummaryCell}
 * с другим типом входа (спека §2.16, ревизия 28.08.2026, задача 9 плана):
 * `StageSummaryTotalCell` не несёт `state` вовсе, и подгонять его под
 * четырёхветочный `switch` `SummaryCell` значило бы либо придумывать пятое
 * состояние агрегату, которому состояния неприменимы структурно, либо
 * симулировать `state: "amount"` литералом — та же подмена типа, которую и
 * правит эта ревизия, только на одну функцию ближе к рендеру. `SummaryCell`
 * при этом не тронут и продолжает делать всё, что делал для ячеек статей.
 *
 * Общее с `SummaryCell` — показ суммы или причины недоступности, значок
 * изменения, подсказка неполноты счётчиков — читается тем же кодом, что и
 * ветка `state === "amount"` там: при известной оси сумма «Итого» ВСЕГДА
 * число (в том числе `"0.00"`), поэтому здесь нет ветвления вовсе — только
 * `amount_unavailable_reason`, которая гасит показ ровно как у статьи.
 */
export function SummaryTotalCell({ cell, extra }: { cell: StageSummaryTotalCell; extra?: ReactNode }) {
  const { amount, amount_unavailable_reason, rows, change } = cell;
  const incomplete = rows.rows_with_amount < rows.row_count;
  // У итога состояний нет (§2.16): число он не показывает единственно тогда,
  // когда сумма погашена неизвестной базой НДС.
  const showsNumber = !amount_unavailable_reason;
  const hasSecondLine = (!amount_unavailable_reason && change.kind !== "none") || extra !== undefined;

  return (
    <TableCell
      className={cn("text-right tabular-nums", cellAlignClass(showsNumber, hasSecondLine))}
      title={amount_unavailable_reason ? REASON_LABEL[amount_unavailable_reason] : undefined}
    >
      <div className="flex items-center justify-end gap-1">
        {amount_unavailable_reason ? (
          <StatusPill tone="neutral" label="нет базы НДС" />
        ) : (
          // Агрегату состояние неприменимо структурно (§2.16): нулевой итог —
          // такое же число, как любое другое, а не пилюля «снято»/«не
          // оценивалась» — ровно дефект, который правит эта ревизия.
          <span>{formatDecimalMoney(amount)}</span>
        )}
        {incomplete && (
          <Tooltip>
            <TooltipTrigger aria-label={incompletenessLabel(rows)} className={cn("cursor-help text-warning")}>
              ◐
            </TooltipTrigger>
            <TooltipContent>{incompletenessLabel(rows)}</TooltipContent>
          </Tooltip>
        )}
      </div>
      {!amount_unavailable_reason && (
        <div className="mt-0.5 text-2xs font-normal">
          <ChangeBadge change={change} />
        </div>
      )}
      {/* `font-normal` — макет держит подпись обычной даже в полужирной строке
          «Итого» (`.conv { font-weight: 400 }`, `.dp { font-weight: 400 }`). */}
      {extra && <div className="mt-0.5 text-2xs font-normal">{extra}</div>}
    </TableCell>
  );
}
