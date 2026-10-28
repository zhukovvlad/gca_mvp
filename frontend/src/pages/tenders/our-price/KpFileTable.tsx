import { useState } from "react";
import { Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { roundDecimalPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { apiErrorDetail, isFieldRefusal, usePutComment } from "@/services/queries";
import type { KpFileHeader, KpFileLineRow, KpFilePreview } from "@/types/domain";

import { formatMoney, formatQuantity } from "./money";
import {
  COMMENT_PLACEHOLDER,
  KP_FILE_CANCEL_LABEL,
  KP_FILE_EDIT_COMMENT_LABEL,
  KP_FILE_FORECAST_TAG,
  KP_FILE_OURS_COLUMNS,
  KP_FILE_YEARS_TITLE,
  SAVE_LABEL,
} from "./ourPriceCopy";

export interface KpFileTableProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  preview: KpFilePreview;
  /** Правка комментария в ячейке — только `admin`; у остальных тот же текст без кнопок. */
  canEdit: boolean;
}

/** Числовые столбцы листа: деньги печатаются строкой ответа с двумя знаками, объём - до трёх, как в книге. */
const MONEY_KEYS: ReadonlySet<string> = new Set(["kp_unit_price", "kp_total", "ours"]);
const NUMERIC_KEYS: ReadonlySet<string> = new Set([...MONEY_KEYS, "quantity", "deviation_pct"]);
/** Текстовые столбцы, которые переносятся по словам. */
const WRAPPED_KEYS: ReadonlySet<string> = new Set(["title", "source", "inflation", "comment"]);

/**
 * Текст ячейки по ключу столбца. Значение берётся из ответа как есть: деньги округляются строкой
 * до копеек (`formatMoney`), процент отклонения — общим `roundDecimalPercent` (запятая, знак,
 * десятая доля, как в остальных ячейках отклонения), `null` - пустая ячейка.
 */
function cellText(row: KpFileLineRow, key: string): string {
  const value = (row as unknown as Record<string, unknown>)[key];
  if (typeof value !== "string" || value === "") return "";
  if (MONEY_KEYS.has(key)) return formatMoney(value, "");
  if (key === "quantity") return formatQuantity(value);
  if (key === "deviation_pct") return roundDecimalPercent(value)?.text ?? "";
  return value;
}

/** Подписи листа и блок лет ИПЦ: то, что стоит над таблицей в книге. */
function SheetHeader({ header }: { header: KpFileHeader }) {
  const years = header.inflation_years;
  return (
    <div data-testid="kp-file-sheet" className="space-y-1 text-sm">
      <p className="font-medium text-fg">
        {header.tender} · {header.object}
      </p>
      <p className="text-fg-secondary">
        {header.round} · {header.contractor}
      </p>
      <p>{header.vat_label}</p>
      <p>{header.source_label}</p>
      <p>{header.level_label}</p>
      {years !== null && (
        <div className="pt-2">
          <p className="text-fg-secondary">{KP_FILE_YEARS_TITLE}</p>
          <Table data-testid="kp-file-years" className="w-auto">
            <TableBody>
              <TableRow>
                <TableHead>Год</TableHead>
                <TableHead className="text-right">Коэффициент</TableHead>
                <TableHead>Источник</TableHead>
              </TableRow>
              {years.map((year) => (
                <TableRow key={year.year}>
                  <TableCell>
                    {year.year}
                    {year.is_forecast && (
                      <span className="ml-1.5 rounded-sm border border-warning-border bg-warning-soft px-1 text-xs text-warning-text">
                        {KP_FILE_FORECAST_TAG}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{year.coefficient}</TableCell>
                  <TableCell>{year.source}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

interface CommentCellProps {
  tenderId: number;
  roundId: number;
  offerId: number;
  row: KpFileLineRow;
  canEdit: boolean;
}

/**
 * Ячейка комментария: текст как в файле; у `admin` - ещё кнопка правки, по нажатию текст
 * становится полем. Сохранение - та же мутация, что у панели строки; отказ уровня поля
 * (слишком длинный текст) стоит здесь же, остальные отказы показывает тост мутации.
 */
function CommentCell({ tenderId, roundId, offerId, row, canEdit }: CommentCellProps) {
  const put = usePutComment(tenderId, roundId, offerId);
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const saved = row.comment ?? "";
  const id = row.position_item_id;

  const save = () => {
    if (draft === null || draft === saved || put.isPending) return;
    put.mutate(
      { positionItemId: id, text: draft },
      {
        onSuccess: () => setDraft(null),
        onError: (failure) => {
          if (isFieldRefusal(failure)) setError(apiErrorDetail(failure) ?? "");
        },
      }
    );
  };

  if (draft === null) {
    return (
      <div className="flex items-start gap-1">
        <span className="whitespace-pre-wrap">{saved}</span>
        {canEdit && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            data-testid={`comment-edit-${id}`}
            aria-label={KP_FILE_EDIT_COMMENT_LABEL}
            title={KP_FILE_EDIT_COMMENT_LABEL}
            onClick={() => {
              setError(null);
              setDraft(saved);
            }}
          >
            <Pencil aria-hidden />
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <Textarea
        value={draft}
        placeholder={COMMENT_PLACEHOLDER}
        aria-label={KP_FILE_EDIT_COMMENT_LABEL}
        aria-invalid={error !== null || undefined}
        onChange={(event) => {
          setDraft(event.target.value);
          setError(null);
        }}
      />
      {error !== null && (
        <p role="alert" className="text-xs text-danger-text">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          data-testid={`comment-save-${id}`}
          disabled={draft === saved || put.isPending}
          onClick={save}
        >
          {SAVE_LABEL}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => setDraft(null)}>
          {KP_FILE_CANCEL_LABEL}
        </Button>
      </div>
    </div>
  );
}

/**
 * Таблица «как в файле» (спека 4б §2.8, §2.9): столбцы и строки - ровно из ответа предпросмотра,
 * без своей логики строк. Раздел подрядчика - строка на всю ширину; наши столбцы выделены
 * фоном. Сортировок и фильтров нет: порядок строк - порядок книги.
 */
export function KpFileTable({ tenderId, roundId, offerId, preview, canEdit }: KpFileTableProps) {
  const { columns, rows } = preview;
  return (
    <div className="space-y-4">
      <SheetHeader header={preview.header} />
      <Table data-testid="kp-file-table">
        <TableHeader>
          <TableRow>
            {columns.map((column) => {
              const ours = KP_FILE_OURS_COLUMNS.has(column.key);
              return (
                <TableHead
                  key={column.key}
                  data-ours={ours ? "true" : undefined}
                  className={cn(
                    "whitespace-normal align-bottom",
                    NUMERIC_KEYS.has(column.key) && "text-right",
                    ours && "bg-accent-soft"
                  )}
                >
                  {column.title}
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) =>
            row.kind === "section" ? (
              <TableRow key={`section-${index}`} data-testid={`kp-section-${index}`} className="bg-surface-sunken">
                <TableCell colSpan={columns.length} className="whitespace-normal font-medium">
                  {[row.number, row.title].filter((part) => part !== null && part !== "").join(" ")}
                </TableCell>
              </TableRow>
            ) : (
              <TableRow key={row.position_item_id} data-testid={`kp-line-${row.position_item_id}`}>
                {columns.map((column) => {
                  const ours = KP_FILE_OURS_COLUMNS.has(column.key);
                  return (
                    <TableCell
                      key={column.key}
                      data-testid={`cell-${row.position_item_id}-${column.key}`}
                      data-ours={ours ? "true" : undefined}
                      className={cn(
                        "align-top",
                        NUMERIC_KEYS.has(column.key) && "text-right tabular-nums",
                        // Текстовые столбцы переносятся: иначе «Источник» растягивает таблицу
                        // шире экрана и «Комментарий» уезжает за прокрутку.
                        WRAPPED_KEYS.has(column.key) && "whitespace-normal",
                        column.key === "title" && "min-w-56",
                        column.key === "comment" && "min-w-56",
                        ours && "bg-accent-soft"
                      )}
                    >
                      {column.key === "comment" ? (
                        <CommentCell
                          tenderId={tenderId}
                          roundId={roundId}
                          offerId={offerId}
                          row={row}
                          canEdit={canEdit}
                        />
                      ) : (
                        cellText(row, column.key)
                      )}
                    </TableCell>
                  );
                })}
              </TableRow>
            )
          )}
        </TableBody>
      </Table>
    </div>
  );
}
