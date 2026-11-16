import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import {
  apiErrorCode,
  apiErrorContext,
  usePutReferenceTarget,
  useReferenceSearch,
} from "@/services/queries";
import type {
  OurPriceLineView,
  ReferenceLinkGroup,
  ReferenceOrigin,
  ReferenceUnits,
  ReferenceZoneGroup,
  RefRow,
} from "@/types/domain";

import type { VatView } from "./money";
import {
  REF_NOTE,
  REF_OTHER_CLASS,
  REF_SEARCH,
  REF_ZONES_TITLE,
  refLinkTitle,
  refZoneBadge,
  workRungLabel,
} from "./ourPriceCopy";
import { ReferenceRows } from "./ReferenceRows";
import { ReferenceSearch } from "./ReferenceSearch";
import { needsConversion } from "./needsConversion";
import { ReferenceTargetDialog, type ReferenceTarget } from "./ReferenceTargetDialog";

export interface ReferencePanelProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  seriesId: number | null;
  data: OurPriceLineView;
  vat: VatView;
  /** Запись цели и диалог выбора — только `admin`; остальные видят блоки и поиск без кнопок. */
  canEdit: boolean;
}

/**
 * Справочные аналоги панели строки-работы (спека справочных аналогов §2.18): блок А («та же работа в другой
 * зоне»), блок Г (семья по связи), «Найти в договорах» и диалог выбора справочной цели. У системы ничего
 * этого нет. Запись цели идёт под серым слоем панели: у неё тот же ключ мутации, что у решений 4б.
 */
export function ReferencePanel({ tenderId, roundId, offerId, seriesId, data, vat, canEdit }: ReferencePanelProps) {
  const { line } = data;
  const [committed, setCommitted] = useState("");
  const [units, setUnits] = useState<ReferenceUnits>("all");
  const [target, setTarget] = useState<ReferenceTarget | null>(null);
  const search = useReferenceSearch(tenderId, roundId, offerId, line.position_item_id, committed, units, seriesId);
  const put = usePutReferenceTarget(tenderId, roundId, offerId, seriesId);
  if (line.kind !== "work") return null;

  const kpUnit = line.unit;
  const choose = (row: RefRow, origin: ReferenceOrigin) => {
    // Диалог нужен, когда единицы различны (пересчёт) или строка помечена выбросом (подтверждение).
    if (needsConversion(row, kpUnit) || row.outlier_ratio !== null) {
      setTarget({ row, origin });
      return;
    }
    put.mutate(
      { positionItemId: line.position_item_id, analogId: row.analog_id, origin, conversion: null, confirmOutlier: false },
      {
        onError: (error) => {
          if (apiErrorCode(error) === "outlier_confirmation_required") {
            setTarget({ row, origin, ratio: apiErrorContext<{ ratio: string }>(error)?.ratio });
          }
        },
      }
    );
  };

  const freshRow = (analogId: number): RefRow | null => {
    const pools: RefRow[][] = [
      ...data.reference_zones.map((g) => g.rows),
      ...data.reference_links.map((g) => g.rows),
      search.data?.rows ?? [],
    ];
    for (const rows of pools) {
      const found = rows.find((r) => r.analog_id === analogId);
      if (found !== undefined) return found;
    }
    return null;
  };

  const blocks = data.reference_zones.length > 0 || data.reference_links.length > 0;
  return (
    <>
      {data.reference_zones.length > 0 && (
        <RefSection title={REF_ZONES_TITLE} testId="sheet-reference-zones">
          {data.reference_zones.map((group) => (
            <ZoneGroup
              key={group.zone}
              group={group}
              vat={vat}
              kpUnit={kpUnit}
              canChoose={canEdit}
              busy={put.isPending}
              onChoose={(row) => choose(row, "zone")}
            />
          ))}
        </RefSection>
      )}
      {data.reference_links.map((group) => (
        <RefSection key={group.link_id} title={linkTitle(group, kpUnit)} testId={`sheet-reference-link-${group.link_id}`}>
          <LinkGroup
            group={group}
            vat={vat}
            kpUnit={kpUnit}
            canChoose={canEdit}
            busy={put.isPending}
            onChoose={(row) => choose(row, "link")}
          />
        </RefSection>
      ))}
      {blocks && <p className="text-xs text-fg-tertiary">{REF_NOTE}</p>}
      <RefSection title={REF_SEARCH.title} testId="sheet-reference-search">
        <ReferenceSearch
          committed={committed}
          units={units}
          onSearch={setCommitted}
          onUnits={setUnits}
          search={search}
          vat={vat}
          kpUnit={kpUnit}
          canChoose={canEdit}
          busy={put.isPending}
          onChoose={(row) => choose(row, "search")}
        />
      </RefSection>
      {canEdit && (
        <ReferenceTargetDialog
          target={target}
          freshRow={freshRow}
          tenderId={tenderId}
          roundId={roundId}
          offerId={offerId}
          positionItemId={line.position_item_id}
          kpUnit={kpUnit}
          kpFamilyId={line.family_id}
          vat={vat}
          mutation={put}
          onClose={() => {
            setTarget(null);
            put.reset();
          }}
        />
      )}
    </>
  );
}

function linkTitle(group: ReferenceLinkGroup, kpUnit: string | null): string {
  const withConversion = group.rows.find((r) => r.conversion !== null)?.conversion ?? null;
  return refLinkTitle(
    group.family,
    group.kind,
    group.kind === "constant" && withConversion !== null ? { factorShown: withConversion.factor_shown, unit: kpUnit } : null
  );
}

function RefSection({ title, testId, children }: { title: string; testId: string; children: ReactNode }) {
  return (
    <section data-testid={testId} className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">{title}</h4>
      {children}
    </section>
  );
}

interface GroupProps<G> {
  group: G;
  vat: VatView;
  kpUnit: string | null;
  canChoose: boolean;
  busy: boolean;
  onChoose: (row: RefRow) => void;
}

function ZoneGroup({ group, vat, kpUnit, canChoose, busy, onChoose }: GroupProps<ReferenceZoneGroup>) {
  const rung = workRungLabel({ no: group.rung_no as 1 | 2 | 3 | 4, released: group.released, other_class: group.other_class });
  return (
    <div
      data-testid={`reference-zone-${group.zone}`}
      className="space-y-2 rounded-lg border border-dashed border-border bg-surface-sunken p-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="flex flex-wrap items-center gap-1.5 text-xs">
          <Badge variant="outline">{rung}</Badge>
        </span>
        <Badge variant="secondary">{refZoneBadge(group.zone)}</Badge>
      </div>
      <ReferenceRows
        rows={group.rows}
        vat={vat}
        kpUnit={kpUnit}
        canChoose={canChoose}
        busy={busy}
        testPrefix={`zone-${group.zone}`}
        onChoose={onChoose}
      />
    </div>
  );
}

function LinkGroup({ group, vat, kpUnit, canChoose, busy, onChoose }: GroupProps<ReferenceLinkGroup>) {
  return (
    <div
      data-testid={`reference-link-${group.link_id}`}
      className="space-y-2 rounded-lg border border-dashed border-border bg-surface-sunken p-3"
    >
      {group.other_class && (
        <div className="text-xs">
          <Badge variant="outline">{REF_OTHER_CLASS}</Badge>
        </div>
      )}
      <ReferenceRows
        rows={group.rows}
        vat={vat}
        kpUnit={kpUnit}
        canChoose={canChoose}
        busy={busy}
        testPrefix={`link-${group.link_id}`}
        onChoose={onChoose}
      />
    </div>
  );
}
