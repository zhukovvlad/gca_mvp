import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { parseId } from "@/lib/urlParams";
import { isInflationRefusal } from "@/components/inflation/refusalCodes";
import { apiErrorCode } from "@/services/queries";
import type { Decimal } from "@/types/domain";

import type { VatView } from "./money";
import { PRICES_CAPTION_NET, PRICES_CAPTION_SHOWN_MIXED, pricesCaptionShown } from "./ourPriceCopy";

/**
 * Переключатели экранов «Против нашей цены» живут в адресе: `?vat=shown` (по умолчанию
 * нетто) и `?inflation=<id>` (по умолчанию без приведения). Остальные ключи адреса
 * (`?article=`) переключатели не трогают.
 */
export function useUrlSwitches() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlView: VatView = searchParams.get("vat") === "shown" ? "shown" : "net";
  const seriesId = parseId(searchParams.get("inflation"));

  function update(mutate: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next, { replace: true });
  }

  function onVat(next: VatView) {
    update((p) => (next === "shown" ? p.set("vat", "shown") : p.delete("vat")));
  }

  function onSeries(id: number | null) {
    update((p) => (id === null ? p.delete("inflation") : p.set("inflation", String(id))));
  }

  return { searchParams, urlView, seriesId, onVat, onSeries };
}

/** Код отказа приведения из ошибки запроса; `undefined` — это не отказ приведения. */
export function inflationRefusalCode(error: unknown): string | undefined {
  const code = apiErrorCode(error);
  return isInflationRefusal(code) ? code : undefined;
}

/**
 * Откат к номиналу: при отказе приведения цифры берёт второй запрос без ряда, и его
 * же ошибка становится ошибкой экрана; без отказа — первый запрос.
 */
export function withNominalFallback<T>(
  refused: boolean,
  primary: { data: T | undefined; error: unknown; isError: boolean },
  nominal: { data: T | undefined; error: unknown; isError: boolean }
): { current: T | undefined; failure: unknown } {
  const source = refused ? nominal : primary;
  return { current: source.data, failure: source.isError ? source.error : null };
}

/**
 * Цифры, которые экран показывал последними: пока идёт новый запрос (в том числе после
 * отказа приведения, когда у упавшего запроса данных нет), они остаются под серым
 * слоем. `inScope` отсекает цифры чужого этапа или участника — их показывать нельзя.
 */
export function useKeptData<T>(current: T | undefined, inScope: (kept: T) => boolean): T | undefined {
  const [kept, setKept] = useState<T | null>(null);
  if (current !== undefined && current !== kept) setKept(current);
  const keptHere = kept !== null && inScope(kept) ? kept : undefined;
  return current ?? keptHere;
}

/** «цены без НДС» / «цены с НДС 22 %» / «по ставке каждого КП»: подпись под заголовком экрана. */
export function vatCaption(view: VatView, shownRate: Decimal | null): string {
  if (view === "net") return PRICES_CAPTION_NET;
  return shownRate === null ? PRICES_CAPTION_SHOWN_MIXED : pricesCaptionShown(shownRate);
}
