import type { ReactNode } from "react";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useInflationSeries } from "@/services/queries";

import type { VatView } from "./money";
import {
  ARCHIVED_SUFFIX,
  INFLATION_SERIES_LABEL,
  INFLATION_SWITCH_LABEL,
  seriesFallbackLabel,
  VAT_SWITCH_LABEL,
} from "./ourPriceCopy";

export interface OurPriceHeaderProps {
  title: ReactNode;
  /** «цены без НДС» / «цены с НДС 22 %» — решает страница по ответу. */
  vatCaption: string;
  /** Подпись приведения: `sentence` ответа либо «цены без приведения»; `null` — без ИПЦ. */
  inflationCaption: string | null;
  vat: VatView;
  onVat: (v: VatView) => void;
  seriesId: number | null;
  onSeries: (id: number | null) => void;
  /** Нет ни одного участника с известной базой НДС — выбирать нечего. */
  vatToggleEnabled: boolean;
}

/**
 * Шапка экрана «Против нашей цены» (спека 4а §2.13): заголовок, подписи цен и
 * переключатели НДС и ИПЦ. Переключатель НДС выбирает готовое поле ответа и
 * запроса не делает; ряд ИПЦ меняет запрос, поэтому им управляет страница. Ряд по
 * умолчанию — первый активный по `id`, а не по имени (§2.10).
 */
export function OurPriceHeader({
  title,
  vatCaption,
  inflationCaption,
  vat,
  onVat,
  seriesId,
  onSeries,
  vatToggleEnabled,
}: OurPriceHeaderProps) {
  const seriesQ = useInflationSeries(true);
  const series = seriesQ.data ?? [];
  const active = series.filter((row) => row.is_active).sort((a, b) => a.id - b.id);
  const firstActiveId = active[0]?.id ?? null;
  // Архивный ряд из адреса остаётся в списке: убрать его — показать селектор без того, что в нём стоит.
  const options = series
    .filter((row) => row.is_active || row.id === seriesId)
    .sort((a, b) => a.id - b.id);

  return (
    <div data-testid="round-header" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">{title}</div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <div className="flex items-center gap-2 text-sm text-fg-secondary">
            <Switch
              aria-label={VAT_SWITCH_LABEL}
              checked={vat === "shown"}
              disabled={!vatToggleEnabled}
              onCheckedChange={(checked) => onVat(checked ? "shown" : "net")}
            />
            <span aria-hidden>{VAT_SWITCH_LABEL}</span>
          </div>
          <div className="flex items-center gap-2 text-sm text-fg-secondary">
            <Switch
              aria-label={INFLATION_SWITCH_LABEL}
              checked={seriesId !== null}
              disabled={seriesId === null && firstActiveId === null}
              onCheckedChange={(checked) => onSeries(checked ? firstActiveId : null)}
            />
            <span aria-hidden>{INFLATION_SWITCH_LABEL}</span>
          </div>
          {seriesId !== null && (
            <div className="flex items-center gap-2 text-sm text-fg-secondary">
              <Label htmlFor="our-price-series">{INFLATION_SERIES_LABEL}</Label>
              <Select value={String(seriesId)} onValueChange={(value) => onSeries(Number(value))}>
                <SelectTrigger id="our-price-series" className="w-64 bg-surface dark:bg-surface">
                  <SelectValue>
                    {(raw) =>
                      options.find((row) => String(row.id) === raw)?.name ?? seriesFallbackLabel(raw)
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {options.map((row) => (
                    <SelectItem key={row.id} value={String(row.id)}>
                      {row.name}
                      {!row.is_active && ARCHIVED_SUFFIX}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
      </div>
      <p className="text-sm font-medium text-fg">
        {vatCaption}
        {inflationCaption !== null && <span className="text-fg-secondary"> · {inflationCaption}</span>}
      </p>
    </div>
  );
}
