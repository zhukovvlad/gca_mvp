import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useFamilyVariants } from "@/services/queries";
import type { FamilyVariant } from "@/types/domain";

import { VARIANT_STATUS_LABEL, VARIANT_VALUE_UNSPECIFIED } from "./labels";

/**
 * Набор значений варианта по порядку параметров схемы. Пустое значение и вариант
 * без параметров читаются как «не уточнено» (спека
 * `2026-10-02-catalog-variants-design.md` §2.12).
 */
function variantSetLabel(values: FamilyVariant["values"]): string {
  if (values.length === 0) return VARIANT_VALUE_UNSPECIFIED;
  return values.map((value) => value ?? VARIANT_VALUE_UNSPECIFIED).join(" · ");
}

/** Таблица вариантов семьи: набор значений, число контекстов, статус (активные и архивные). */
export function VariantsTable({ familyId }: { familyId: number }) {
  const variantsQ = useFamilyVariants(familyId);

  if (variantsQ.isPending) return <Skeleton className="h-16 w-full" />;
  if (variantsQ.isError) {
    return <p className="text-sm text-fg-tertiary">Не удалось получить варианты семьи.</p>;
  }
  const variants = variantsQ.data;
  if (variants.length === 0) {
    return <p className="text-sm text-fg-tertiary">Вариантов пока нет.</p>;
  }

  return (
    <Table aria-label="Варианты семьи">
      <TableHeader>
        <TableRow>
          <TableHead className="text-xs font-normal text-fg-tertiary">Набор значений</TableHead>
          <TableHead className="text-right text-xs font-normal text-fg-tertiary">Контекстов</TableHead>
          <TableHead className="text-xs font-normal text-fg-tertiary">Статус</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {variants.map((variant) => (
          <TableRow key={variant.id}>
            <TableCell className="whitespace-normal">{variantSetLabel(variant.values)}</TableCell>
            <TableCell className="text-right tabular-nums">{variant.contexts}</TableCell>
            <TableCell>
              <Badge variant="outline">{VARIANT_STATUS_LABEL[variant.status]}</Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
