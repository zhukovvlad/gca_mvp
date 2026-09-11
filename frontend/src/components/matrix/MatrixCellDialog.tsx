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
import type { CellItemExcludedReason, MatrixColumn, MatrixRow } from "@/types/domain";

/**
 * Четыре подписи невхождения строки в ставку (`excluded_reason`, спека §2.8,
 * задача 9 плана правила цены). Строка присутствует в списке (носитель —
 * `_all_positions_select`, задача 3), но её вклад не вошёл в среднюю ставку
 * ячейки — причины разные и не сводятся к одному прочерку, тем же принципом,
 * что и `CELL_RATE_REASON_LABEL` в `MatrixPage.tsx`.
 */
const EXCLUDED_REASON_LABEL: Record<CellItemExcludedReason, string> = {
  no_price: "цены нет",
  negative: "цена отрицательна",
  not_finite: "не число",
  no_weight: "нет веса",
};

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
 * Показывает ВСЕ позиции работы последней сметы договора — не только те, что вошли в
 * среднюю ставку (правило цены, спека §2.8, задача 3/9 плана 2026-09-09): носитель
 * сменился на `_all_positions_select`, и строка без пригодной цены или веса тоже
 * видна, с признаком `included` и причиной `excluded_reason` вместо отклонения.
 * Смысл экрана — **проверяемость**: человек, которому ставка кажется странной (или
 * пустой — см. `rate_reason` ячейки), должен увидеть именно те строки работы, которые
 * дали (или не дали) ставку, а не всю смету. Список отдаёт сервер целиком, без
 * фильтрации здесь.
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

  /**
   * Число строк, ВОШЕДШИХ в ставку — не `detail.items.length` (ревью задачи 9):
   * список теперь несёт и невошедшие строки (`_all_positions_select`, задача 3),
   * и «работа встречается N раз» больше не равно «в ставку вошло N раз».
   */
  const includedCount = detail?.items.filter((item) => item.included).length ?? 0;

  /**
   * Есть ли у ЯЧЕЙКИ вообще ставка — по `rate_reason` той же ячейки в
   * `row.cells` (ревью задачи 9): footnote не вправе обещать «средневзвешенную
   * ставку», когда её нет вовсе (`rate_reason` непуст — например, ставка
   * погашена неизвестной базой НДС, хотя отдельные строки числятся `included`
   * по СВОЕМУ, построчному предикату). `undefined` (нет `row`, либо контракта
   * нет среди `row.cells`) читается как «ставки нет» — безопасный дефолт: без
   * доказательства обратного текст ничего не обещает.
   */
  const cellHasRate = row?.cells.find((c) => c.contract_id === contractId)?.rate_reason === null;

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
            {/* Список больше не фильтрует по цене (задача 3 плана правила
                цены) — пустой список теперь значит «строк вовсе нет», а не
                «нет расценённых строк» (ревью задачи 9). */}
            В последней смете этого договора нет строк по данной работе.
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

                      Невошедшая строка (`included` ложно) сюда приходит с
                      `deviation_pct`/`deviation_reason`, ОБА пустыми (носитель —
                      причина невхождения, не отклонения) — `DeviationCell` без
                      различителя подписал бы её как «нет норматива», что для
                      такой строки неверно (норматив мог и БЫТЬ). Различитель —
                      `included`, читаемое первым (`docs/insights/
                      one-value-two-states.md`).
                    */}
                    <TableCell className="text-right" data-testid="item-deviation">
                      {item.included ? (
                        <DeviationCell
                          value={item.deviation_pct}
                          reason={item.deviation_reason ?? undefined}
                        />
                      ) : (
                        <span
                          className="text-xs text-fg-tertiary"
                          title="Строка не вошла в среднюю ставку ячейки"
                        >
                          {/*
                            `excluded_reason` пуст ТОЛЬКО когда контракт нарушен
                            (`included` ложно обязано нести причину) — ревью
                            задачи 9: `?? "no_price"` тут раньше МОЛЧА выдумывал
                            причину вместо честного признания дефекта данных.
                            Прочерк не называет ничего, чего сервер не сказал.
                          */}
                          {item.excluded_reason ? EXCLUDED_REASON_LABEL[item.excluded_reason] : "—"}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={item.total_cost_total} currency="" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {includedCount > 1 && cellHasRate && (
              <p className="text-xs text-fg-tertiary">
                В матрице стоит средневзвешенная ставка: она сложена из {includedCount} строк
                сметы — сумма «ставка × объём», делённая на сумму объёмов (§6).
              </p>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
