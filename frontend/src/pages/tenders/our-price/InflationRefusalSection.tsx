import { useState } from "react";

import {
  InflationSeriesDialog,
  type InflationSeriesTarget,
} from "@/components/inflation/InflationSeriesDialog";
import { InflationRefusalBanner } from "@/components/inflation/InflationRefusalBanner";
import { apiErrorContext, apiErrorDetail, useInflationSeries } from "@/services/queries";

import { REFUSAL_REASON_UNNAMED } from "./ourPriceCopy";

export interface InflationRefusalSectionProps {
  /** Код отказа приведения; `undefined` — отказа нет и баннера нет. */
  refusalCode: string | undefined;
  /** Ошибка запроса с рядом: из неё берутся текст и контекст отказа. */
  error: unknown;
  seriesId: number | null;
  /** Правка ряда доступна только администратору; роль читает страница до первой загрузки цифр. */
  canEdit: boolean;
  /** Ряд закрыли после правки: цифры экрана надо перезапросить. */
  onSeriesEdited: () => void;
}

/**
 * Баннер отказа приведения по ИПЦ и диалог правки ряда (спека 4а §2.10). Общий у
 * экрана этапа и разбора участника: отказ не роняет экран, а баннер ведёт к правке
 * ряда там, где её может сделать администратор.
 */
export function InflationRefusalSection({
  refusalCode,
  error,
  seriesId,
  canEdit,
  onSeriesEdited,
}: InflationRefusalSectionProps) {
  const seriesListQ = useInflationSeries(true);
  const [editingSeries, setEditingSeries] = useState<number | null>(null);
  const [missingYears, setMissingYears] = useState<number[] | undefined>(undefined);

  const editedSeries = seriesListQ.data?.find((row) => row.id === editingSeries) ?? null;
  const dialogTarget: InflationSeriesTarget = editedSeries
    ? { mode: "edit", series: editedSeries }
    : seriesListQ.isError
      ? { mode: "edit", series: null, reason: "failed", onRetry: () => void seriesListQ.refetch() }
      : { mode: "edit", series: null, reason: "pending" };

  return (
    <>
      {refusalCode !== undefined && (
        <InflationRefusalBanner
          code={refusalCode}
          message={apiErrorDetail(error) ?? REFUSAL_REASON_UNNAMED}
          seriesName={seriesListQ.data?.find((row) => row.id === seriesId)?.name}
          missingYears={apiErrorContext<{ missing_years?: number[] }>(error)?.missing_years}
          canEdit={canEdit}
          monthInAddress={false}
          onFillMissingYears={(years) => {
            setMissingYears(years);
            setEditingSeries(seriesId);
          }}
        />
      )}
      <InflationSeriesDialog
        open={editingSeries !== null}
        target={dialogTarget}
        missingYears={missingYears}
        onOpenChange={(open) => {
          if (open) return;
          setEditingSeries(null);
          setMissingYears(undefined);
          onSeriesEdited();
        }}
      />
    </>
  );
}
