import type { ID, ISODate, ISODateTime } from "./common";

/** Поставщик в реестре (список). */
export interface SupplierListItem {
  id: ID;
  name: string;
  inn: string | null;
  created_at: ISODateTime | null;
  invoice_count: number;
  turnover: number;
  project_count: number;
  first_invoice_date: ISODate | null;
  categories: string[];
}

/** Детальная шапка поставщика (карточка). Поля идентичны SupplierListItem. */
export type SupplierDetail = SupplierListItem;

/** Строка таблицы «По объектам» в карточке поставщика. */
export interface SupplierProjectRow {
  project_id: ID;
  project_name: string;
  contract_number: string | null;
  invoice_count: number;
  turnover: number;
  volume_m3: number;
  deviation_pct: number | null;
  deviation_amount: number | null;
}

/** Строка счёта в табе «Счета» карточки поставщика. */
export interface SupplierInvoiceRow {
  id: ID;
  document_id: ID;
  number: string;
  date: ISODate;
  verified: boolean;
  verified_at: ISODateTime | null;
  ai_confidence: number | null;
  project_id: ID;
  project_name: string;
  amount: number;
}
