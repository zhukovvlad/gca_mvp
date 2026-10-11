import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { formatUsd } from "@/lib/format";
import { useDiscoveryUnits } from "@/services/queries";
import type { ActivationOutcome, DiscoveryUnitRow, PreviewTarget } from "@/types/domain";

import { pluralRu } from "../labels";
import { PreviewDialog } from "../PreviewDialog";
import { DiscoveryDrafts } from "./DiscoveryDrafts";
import { DiscoveryLaunchDialog, type DiscoveryTarget } from "./DiscoveryLaunchDialog";

const LIVE_STATUSES = ["pending", "running"];

interface DiscoveryBlockProps {
  /** Код единицы (`COMPL`) → символ (`компл`); `null` — «без единицы». */
  unitLabel: (code: string | null) => string;
  /** Фильтр единицы очереди: id единицы или `"none"` («без единицы»); не задан — все единицы. */
  unitFilter?: number | "none";
}

function formatCount(n: number): string {
  return n.toLocaleString("ru-RU");
}

/**
 * Из чего состоит охват единицы: числа выделены, как в макете («1 382 системы без семьи»,
 * «305 строк с «новой семьёй» и 1 система»). Системы без других строк — «N систем без семьи»;
 * рядом с обычными строками — «и N систем».
 */
function ScopeText({ row }: { row: DiscoveryUnitRow }) {
  const rowsPart = (n: number, tail: string) => (
    <>
      <b className="text-fg">
        {formatCount(n)} {pluralRu(n, "строка", "строки", "строк")}
      </b>{" "}
      {tail}
    </>
  );
  const systemWord = (n: number) => `${formatCount(n)} ${pluralRu(n, "система", "системы", "систем")}`;
  const hasRows = row.new_family > 0 || row.bare > 0;
  const parts: React.ReactNode[] = [];
  if (row.new_family > 0) {
    parts.push(
      <span key="new">
        {rowsPart(row.new_family, "с «новой семьёй»")}
        {row.systems > 0 && !(row.bare > 0) && (
          <>
            {" "}и <b className="text-fg">{systemWord(row.systems)}</b>
          </>
        )}
      </span>
    );
  }
  if (row.bare > 0) {
    parts.push(
      <span key="bare">
        {rowsPart(row.bare, "в единице без активных семей")}
        {row.systems > 0 && (
          <>
            {" "}и <b className="text-fg">{systemWord(row.systems)}</b>
          </>
        )}
      </span>
    );
  }
  if (row.systems > 0 && !hasRows) {
    parts.push(
      <span key="systems">
        <b className="text-fg">{systemWord(row.systems)}</b> без семьи
      </span>
    );
  }
  if (row.uncategorized_families > 0) {
    parts.push(
      <span key="uncategorized">
        <b className="text-fg">{formatCount(row.uncategorized_families)}</b>{" "}
        {pluralRu(row.uncategorized_families, "семья", "семьи", "семей")} без категории
      </span>
    );
  }
  return (
    <>
      {parts.map((part, index) => (
        <span key={index}>
          {index > 0 && " · "}
          {part}
        </span>
      ))}
    </>
  );
}

/** Новейшее задание открытия, если оно не выполнено: экран черновиков принадлежит прошлому выполненному. */
function LatestJobNote({ status }: { status: string | null }) {
  const text = status === null || status === "done" ? null : status === "error"
    ? "Последнее открытие закончилось ошибкой — подробности в очереди «Ошибки». Ниже черновики прошлого выполненного открытия."
    : status === "privacy_hold"
      ? "Новое открытие задержано проверкой приватности. Ниже черновики прошлого выполненного открытия."
      : LIVE_STATUSES.includes(status)
        ? "Новое открытие открывается… Ниже черновики прошлого выполненного открытия."
        : null;
  if (text === null) return null;
  return (
    <p
      data-testid="discovery-latest-job"
      role={LIVE_STATUSES.includes(status ?? "") ? "status" : undefined}
      className="mb-2 px-1 text-[13px] text-fg-secondary"
    >
      {text}
    </p>
  );
}

interface UnitRowProps {
  row: DiscoveryUnitRow;
  label: string;
  onOpen: () => void;
}

function UnitRow({ row, label, onOpen }: UnitRowProps) {
  const status = row.last_discovery?.status ?? null;
  const live = status !== null && LIVE_STATUSES.includes(status);
  const held = status === "privacy_hold";
  return (
    <div
      data-testid="discovery-unit-row"
      className="grid grid-cols-[80px_minmax(0,1fr)_auto_auto] items-center gap-3.5 border-t border-border-subtle px-4 py-2.5 text-[13px]"
    >
      <b className="text-fg">{label}</b>
      <span className="text-fg-secondary">
        <ScopeText row={row} />
        {status === "error" && (
          <span className="ml-2 text-danger-text">
            последнее открытие закончилось ошибкой — подробности в очереди «Ошибки»
          </span>
        )}
      </span>
      <span className="text-fg-secondary">≈ {formatUsd(row.expected_cached_usd)}</span>
      {live ? (
        <span role="status" className="text-fg-tertiary">
          открывается…
        </span>
      ) : held ? (
        <span className="text-warning-text">задержано проверкой приватности</span>
      ) : (
        <Button size="sm" onClick={onOpen}>
          Открыть семьи…
        </Button>
      )}
    </div>
  );
}

/**
 * Блок «Открыть семьи» над таблицей очереди «Новая» (экраны 1 и 3 макета): строка на единицу —
 * числа и оценка из `/discovery/units`, кнопка «Открыть семьи…». Пока задание живо — строка
 * «открывается…» и перечитывание; у единицы с открытыми черновиками строку заменяет экран
 * черновиков. По ответу активации открывается обычное окно перезапроса единицы.
 */
export function DiscoveryBlock({ unitLabel, unitFilter }: DiscoveryBlockProps) {
  const unitsQ = useDiscoveryUnits();
  const [launch, setLaunch] = useState<DiscoveryTarget | null>(null);
  const [reask, setReask] = useState<{ target: PreviewTarget; unitLabel: string } | null>(null);

  const rows = useMemo(
    () =>
      (unitsQ.data ?? []).filter((row) =>
        unitFilter === undefined
          ? true
          : unitFilter === "none"
            ? row.unit_id === null
            : row.unit_id === unitFilter
      ),
    [unitsQ.data, unitFilter]
  );

  function handleActivated(row: DiscoveryUnitRow, outcome: ActivationOutcome) {
    // Единица перезапроса — та, что вернул сервер (`reask_unit_id`), а не та, что показана.
    setReask({
      target: { kind: "unit", unitId: outcome.reask_unit_id, unitCode: row.unit_code },
      unitLabel: unitLabel(row.unit_code),
    });
  }

  const reaskDialog = (
    <PreviewDialog
      target={reask?.target ?? null}
      unitLabel={reask?.unitLabel}
      onClose={() => setReask(null)}
    />
  );

  if (rows.length === 0) return reaskDialog;

  return (
    <>
      <div
        data-testid="discovery-block"
        className="overflow-hidden rounded-[10px] border border-border-subtle bg-surface"
      >
        <div className="px-4 pt-3 pb-2">
          <div className="font-semibold text-fg">Открыть семьи</div>
          <div className="text-[12.5px] text-fg-secondary">
            Модель сведёт имена без семьи в черновики семей с определениями. Ничего не активируется
            само — черновики придут на просмотр.
          </div>
        </div>
        {rows.map((row) => {
          const label = unitLabel(row.unit_code);
          // Экран черновиков виден, пока в последнем выполненном открытии есть что решать (сервер
          // считает `actionable`: открытый или возвращаемый черновик, категории, «Не работа»).
          const hasDrafts = row.last_discovery?.actionable === true;
          return hasDrafts ? (
            <div key={row.unit_id ?? "none"} className="border-t border-border-subtle p-3">
              <LatestJobNote status={row.last_discovery?.status ?? null} />
              <DiscoveryDrafts
                unitId={row.unit_id}
                unitLabel={label}
                onActivated={(outcome) => handleActivated(row, outcome)}
                onRelaunch={() => setLaunch({ unitId: row.unit_id, unitLabel: label })}
              />
            </div>
          ) : (
            <UnitRow
              key={row.unit_id ?? "none"}
              row={row}
              label={label}
              onOpen={() => setLaunch({ unitId: row.unit_id, unitLabel: label })}
            />
          );
        })}
      </div>
      <DiscoveryLaunchDialog target={launch} onClose={() => setLaunch(null)} />
      {reaskDialog}
    </>
  );
}
