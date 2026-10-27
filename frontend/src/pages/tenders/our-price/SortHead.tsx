import { ArrowDown, ArrowUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { SortState } from "./sorting";

/** Кнопка-заголовок сортируемой колонки таблиц «Против нашей цены». */
export function SortHead<K>({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: K;
  sort: SortState<K>;
  onSort: (key: K) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === sortKey;
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("h-auto px-1 py-0.5 font-medium", align === "right" && "ml-auto flex")}
      onClick={() => onSort(sortKey)}
    >
      {label}
      {active && (sort.dir === "asc" ? <ArrowUp aria-hidden /> : <ArrowDown aria-hidden />)}
    </Button>
  );
}
