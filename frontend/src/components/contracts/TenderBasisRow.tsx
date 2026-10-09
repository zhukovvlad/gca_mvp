import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { EstimateOrigin, TenderBasis } from "@/types/domain";

import { ESTIMATE_ORIGIN_LABEL, ESTIMATE_ORIGIN_TITLE } from "./tenderBasisText";

const ORIGIN_TONE: Record<EstimateOrigin, string> = {
  from_offer: "border-accent-border bg-accent-soft text-accent-text",
  uploaded_separately: "",
  no_estimate: "border-warning-border bg-warning-soft text-warning-text",
};

/**
 * Поля «Основание» и «Смета договора» карточки договора (дизайн Б2 §2.4, макет
 * экран 7). Пометка называет только ПРОИСХОЖДЕНИЕ основной сметы — не её
 * сравнение с КП: сравнения содержимого в фиче нет. Без основания — ничего:
 * у договора без тендера строки нет вовсе.
 *
 * Вставляется внутрь `<dl>` карточки, поэтому отдаёт пары `dt`/`dd`.
 */
export function TenderBasisRow({
  basis,
  origin,
}: {
  basis: TenderBasis | null;
  origin: EstimateOrigin | null;
}) {
  if (basis === null) return null;
  return (
    <>
      <div>
        <dt className="text-xs text-fg-tertiary">Основание</dt>
        <dd className="mt-0.5 text-sm text-fg">
          <Link to={`/tenders/${basis.tender_id}`} className="text-accent-text underline">
            Тендер № {basis.tender_number}
          </Link>
          , этап {basis.stage_no} · финал
        </dd>
      </div>
      {origin !== null && (
        <div>
          <dt className="text-xs text-fg-tertiary">Смета договора</dt>
          <dd className="mt-0.5 text-sm text-fg">
            <Badge
              variant="outline"
              title={ESTIMATE_ORIGIN_TITLE[origin]}
              className={cn(ORIGIN_TONE[origin])}
            >
              {ESTIMATE_ORIGIN_LABEL[origin]}
            </Badge>
          </dd>
        </div>
      )}
    </>
  );
}
