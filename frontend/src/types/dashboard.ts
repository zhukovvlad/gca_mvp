import type { ISODate } from "./common";
import type { DashboardInvoiceRow } from "./invoice";

export interface DirectionSummary {
  code: string;
  name: string;
  /** Оборот направления по позициям, ₽ с НДС (спека §5.1). */
  turnover: number;
  /** Σ compensation_amount классов направления (компенсация за пределами
   * коридора); null — ни один класс направления не компенсируется. */
  overpayment: number | null;
  /** Объём в родной единице направления (только base-классы, §5.2). */
  volume: number | null;
  volume_unit: string | null;
  /** Base-позиции, не вошедшие в объём (другая размерность / нет нормализации). */
  volume_excluded_count: number;
  invoice_count: number;
  mixed_invoice_count: number;
}

export interface DashboardSummary {
  doc_count: number;
  invoice_count: number;
  total_amount: number;
  material_amount: number;
  delivery_amount: number;
  other_amount: number;
  /** @deprecated «попугаи» при миксе единиц — не использовать (TECH_DEBT). */
  total_qty: number;
  first_invoice_date: ISODate | null;
  last_invoice_date: ISODate | null;
  /** Σ compensation_amount всех классов за весь период; null — ничего не
   * компенсируется. Используется в KPI «Переплата за весь период». */
  full_compensation_amount: number | null;
  /** Направления с данными, без типа other (ADR #9); порядок — по id типа. */
  directions: DirectionSummary[];
  /** Готовые строки расчёта за полный период (без direction-фильтра). Опционально:
   * старый бэкенд поля не отдаёт → фронт фолбэчит на сетевой /calculations (§2). */
  calculations?: DashboardCalculation[];
  /** Счета с позициями ≥2 направлений (§5.5). */
  mixed_invoice_count: number;
  /** Счета без единой direction-позиции — хвост «· N проч.» в KPI. */
  other_invoice_count: number;
  delivery_total: number;
  /** item_type='other' + классы типа other + позиции без класса (§5.1). */
  other_total: number;
}

export interface DashboardCalculation {
  material_class_id: number;
  material_class_name: string;
  period_start: ISODate;
  period_end: ISODate;
  avg_price: number;
  reference_price: number | null;
  deviation_pct: number | null;
  deviation_amount: number | null;
  /** Процент коридора компенсации; null → класс некомпенсируемый. */
  corridor_pct: number | null;
  /** Компенсация на единицу объёма; null → не применимо, 0 → внутри коридора. */
  compensation_per_unit: number | null;
  /** Компенсация за период по классу (₽); null → не применимо. */
  compensation_amount: number | null;
  material_total: number | null;
  /** Доставка + присадки (calc_role="additive") за период по классу, с НДС. */
  delivery_total: number | null;
  total_qty: number;
  invoice_count: number;
  /** Code типа материала класса ('concrete' | 'rebar' | 'other' | ...). */
  direction: string;
}

export type DashboardInvoices = DashboardInvoiceRow[];

export interface MonthlyBucketRaw {
  year: number;
  month: number;
  total_amount: number;
  total_qty: number;
  invoice_count: number;
  volume_unit: string | null;
}
