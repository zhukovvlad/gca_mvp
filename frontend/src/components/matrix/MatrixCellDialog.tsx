import { DeviationCell } from "@/components/ui-domain/DeviationCell";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { formatSharePercent } from "@/lib/format";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMatrixCell } from "@/services/queries";
import type { MatrixColumn, MatrixRow } from "@/types/domain";

interface MatrixCellDialogProps {
  open: boolean;
  contractId?: number;
  row?: MatrixRow;
  column?: MatrixColumn;
  onClose: () => void;
}

/**
 * Drill-down по ячейке матрицы (§6: «Drill-down в позиции по клику»).
 *
 * Показывает позиции последней сметы, из которых сложилась средневзвешенная ставка.
 * Смысл экрана — **проверяемость**: человек, которому ставка кажется странной, должен
 * увидеть именно те строки, которые её дали, а не всю смету. Поэтому список сюда
 * отдаёт сервер по тому же правилу (последняя смета, `w > 0`), а не фильтруется здесь.
 */
export function MatrixCellDialog({
  open,
  contractId,
  row,
  column,
  onClose,
}: MatrixCellDialogProps) {
  const detailQ = useMatrixCell(
    open ? contractId : undefined,
    open ? row?.catalog_position_id : undefined
  );
  const detail = detailQ.data;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{row?.job_title ?? "Позиции сметы"}</DialogTitle>
          <DialogDescription>
            {column === undefined
              ? null
              : `Договор ${column.contract_number} · ${column.object_title}` +
                (detail?.amendment_no == null
                  ? " · исходная смета"
                  : ` · доп. соглашение № ${detail.amendment_no}`)}
          </DialogDescription>
        </DialogHeader>

        {detailQ.isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : detail === undefined || detail.items.length === 0 ? (
          <p className="text-sm text-fg-secondary">
            В последней смете этого договора нет расценённых строк по данной работе.
          </p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Наименование в смете</TableHead>
                  <TableHead className="w-16">Ед.</TableHead>
                  <TableHead className="w-24 text-right">Объём</TableHead>
                  <TableHead className="w-28 text-right">Ставка из файла</TableHead>
                  <TableHead className="w-28 text-right">Ставка без НДС</TableHead>
                  <TableHead className="w-20 text-right">База НДС</TableHead>
                  <TableHead className="w-24 text-right">Откл.</TableHead>
                  <TableHead className="w-32 text-right">Стоимость</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {detail.items.map((item) => (
                  <TableRow key={item.position_item_id}>
                    {/*
                      Тот же зажим, что в матрице: наименование в смете бывает на
                      килобайты, и без него одна строка распирала бы диалог, вытесняя
                      остальные позиции за пределы окна.
                    */}
                    <TableCell>
                      <span className="line-clamp-3 max-w-[26rem]" title={item.job_title}>
                        {item.job_title}
                      </span>
                    </TableCell>
                    <TableCell className="text-fg-secondary">{item.unit_code ?? "—"}</TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={item.weight} currency="" />
                    </TableCell>
                    <TableCell className="text-right" data-testid="item-gross">
                      <MoneyCell value={item.unit_cost_total} currency="" />
                    </TableCell>
                    {/*
                      Валовое, нетто и база рядом (спека §2.5) — три разные подписи,
                      а не одна «Ставка»: без нетто человек не увидит, из какой
                      величины ФАКТИЧЕСКИ сложилось отклонение (оно всегда от нетто),
                      а без базы не поймёт, каким процентом валовое привели к нетто.
                    */}
                    <TableCell className="text-right" data-testid="item-net">
                      {item.unit_cost_net === null ? (
                        "—"
                      ) : (
                        <MoneyCell value={item.unit_cost_net} currency="" />
                      )}
                    </TableCell>
                    <TableCell className="text-right" data-testid="item-vat-base">
                      {item.vat_rate_base === null ? "—" : formatSharePercent(item.vat_rate_base)}
                    </TableCell>
                    {/*
                      `variant="full"` (по умолчанию) — не `"compact"`, как было
                      раньше: диалог место есть, человек читает документ, а не
                      таблицу («компакт» здесь стирал бы саму причину до
                      прочерка, ровно то различие, ради которого заведён `reason`).
                    */}
                    <TableCell className="text-right">
                      <DeviationCell
                        value={item.deviation_pct}
                        reason={item.deviation_reason ?? undefined}
                      />
                    </TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={item.total_cost_total} currency="" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {detail.items.length > 1 && (
              <p className="text-xs text-fg-tertiary">
                Работа встречается в смете {detail.items.length} раз, поэтому в матрице стоит
                средневзвешенная ставка: сумма «ставка × объём», делённая на сумму объёмов (§6).
              </p>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
