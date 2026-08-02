import type { ID, ISODate, ISODateTime } from "./common";

export interface MaterialClassRef {
  id: ID;
  name: string;
}

export interface InvoiceItem {
  id?: ID;
  raw_name: string;
  item_type: "material" | "delivery" | "other";
  /**
   * Класс материала, привязанный к позиции. Бэкенд сериализует как {id, name} либо null.
   */
  material_class: MaterialClassRef | null;
  material_class_id?: ID | null;
  quantity: number;
  raw_unit: string;
  unit_price: number;
  amount: number;
  vat_amount?: number | null;
}

/** Позиция счёта в сокращённом виде, как возвращает /dashboard/invoices. */
export interface DashboardInvoiceItem {
  raw_name: string | null;
  item_type: "material" | "delivery" | "other";
  /** Имя класса материала или null — бэкенд сериализует как строку. */
  material_class: string | null;
  quantity: number;
  raw_unit: string | null;
  unit_price: number;
  amount: number;
  vat_amount?: number | null;
}

/** Счёт в формате dashboard/invoices (позиции без id). */
export interface DashboardInvoiceRow {
  id: ID;
  document_id: ID;
  number: string;
  date: ISODate;
  supplier_name: string | null;
  supplier_inn: string | null;
  vat_rate: number;
  ai_confidence: number | null;
  has_issues: boolean;
  /** Коды направлений, которых касается счёт (ADR #9: other не направление). [] = прочий. */
  directions: string[];
  verified: boolean;
  verified_at: ISODateTime | null;
  items: DashboardInvoiceItem[];
}

export interface InvoiceRow {
  id: ID;
  document_id: ID;
  number: string;
  date: ISODate;
  supplier_name: string | null;
  supplier_inn?: string | null;
  vat_rate: number;
  ai_confidence: number | null;
  has_issues: boolean;
  verified: boolean;
  verified_at: ISODateTime | null;
  items: InvoiceItem[];
}

export interface DocumentSummary {
  id: ID;
  project_id: ID;
  filename: string;
  doc_type: string;
  status: string;
  /** Причина последней ошибки парсинга/деcкью (S0-7); заполнена только при status="error". */
  last_error?: string | null;
  uploaded_at: ISODateTime;
  invoice_count: number;
  has_issues: boolean;
  ai_confidence: number | null;
  /** Накопленная стоимость ИИ-разбора, USD (usage-репорт провайдера; в контурном режиме недоступна — см. capabilities). */
  parse_cost_usd: number;
  /** Число платных вызовов ИИ-разбора по документу (usage-репорт провайдера; в контурном режиме недоступна — см. capabilities). */
  parse_count: number;
}

export interface DocumentDetail extends DocumentSummary {
  invoices: InvoiceRow[];
}

/** Ответ POST /upload: 202 (создан, duplicate=false) или 200 (дубликат по file_hash, Q6). */
export type UploadResponse = DocumentDetail & { duplicate: boolean };

export interface InvoiceUpdateInput {
  number?: string;
  date?: ISODate;
  supplier_name?: string | null;
  supplier_inn?: string | null;
  vat_rate?: number;
  items?: InvoiceItem[];
}

export interface InvoiceUpdateWarning {
  field: string;
  code: string;
  message: string;
}

export interface InvoiceUpdateResult {
  message: string;
  invoice_id: ID;
  warnings: InvoiceUpdateWarning[];
}
