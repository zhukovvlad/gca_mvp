import { useState } from "react";
import { Download, Search } from "lucide-react";

import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDebounce } from "@/lib/useDebounce";
import {
  useBankComparisonReport,
  useContractSummaryReport,
  useContracts,
  useRateClasses,
} from "@/services/queries";

const ALL_CLASSES = "all";

/**
 * Отчёты (AGENTS.md §7.6). Паттерн экрана взят у `Reports.tsx` источника: набор
 * параметров плюс кнопка скачивания.
 *
 * **Два отчёта — два файла** (решение §6.6, `docs/phase6-analytics.md` §1.6): у них
 * разный охват и разные параметры. Свод берёт один договор, отчёт «для банка» —
 * выборку из многих, поэтому и блока параметров два, а не один общий.
 *
 * Макет отчёта «для банка» согласован с пользователем (§6.1): класс → работа,
 * колонки с отклонением в деньгах, итоги по каждому классу и общий, шапка с
 * реквизитами выборки и блоком подписей.
 *
 * Фильтры отчёта «для банка» — те же, что у матрицы, и это намеренно: экран и файл
 * обязаны показывать одно и то же, иначе расхождение придётся объяснять банку.
 */
export default function ReportsPage() {
  const [contractQuery, setContractQuery] = useState("");
  const [contractId, setContractId] = useState<number | null>(null);
  const [rateClass, setRateClass] = useState<string>(ALL_CLASSES);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const debouncedQuery = useDebounce(contractQuery.trim(), 300);
  const contractsQ = useContracts({ q: debouncedQuery || undefined, page: 1, page_size: 50 });
  const classesQ = useRateClasses();

  const summary = useContractSummaryReport();
  const bank = useBankComparisonReport();

  const contracts = contractsQ.data?.items ?? [];
  const selected = contracts.find((c) => c.id === contractId);

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Отчёты"
        subtitle="Выгрузки в Excel: свод расценок по договору и сравнение с нормативами для банка."
      />

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Surface>
          <h2 className="text-md font-medium text-fg">Свод расценок по договору</h2>
          <p className="mt-1 text-sm text-fg-secondary">
            Все расценённые работы последней сметы с отклонениями от норматива. Работы без
            норматива остаются в своде — это предмет торга, и он должен быть виден целиком.
          </p>

          <div className="mt-4">
            <Label htmlFor="report-contract-search">Поиск договора</Label>
            <InputGroup className="mt-1.5">
              <InputGroupAddon>
                <Search className="size-4" />
              </InputGroupAddon>
              <InputGroupInput
                id="report-contract-search"
                placeholder="номер, объект или подрядчик"
                value={contractQuery}
                onChange={(event) => setContractQuery(event.target.value)}
              />
            </InputGroup>
          </div>

          <div className="mt-3">
            <Label htmlFor="report-contract">Договор</Label>
            <EntitySelect
              id="report-contract"
              className="mt-1.5 w-full"
              items={contracts}
              value={contractId}
              onChange={(value) => setContractId(value === null ? null : Number(value))}
              getLabel={(c) => `${c.contract_number} · ${c.object_title}`}
              placeholder={contractsQ.isPending ? "Загружаю…" : "выберите договор"}
            />
            {contracts.length === 0 && !contractsQ.isPending && (
              <p className="mt-2 text-xs text-fg-tertiary">
                Договоров не найдено. Уточните поиск.
              </p>
            )}
          </div>

          <Button
            className="mt-4"
            disabled={contractId === null || summary.isPending}
            onClick={() => {
              if (contractId === null) return;
              summary.mutate({
                contractId,
                // Имя файла собирается на клиенте: номер договора уже известен, а
                // читать `Content-Disposition` из ответа сложнее и без выгоды.
                filename: `Свод расценок ${selected?.contract_number ?? contractId}.xlsx`,
              });
            }}
          >
            <Download className="size-4" />
            {summary.isPending ? "Готовлю файл…" : "Скачать свод"}
          </Button>
        </Surface>

        <Surface>
          <h2 className="text-md font-medium text-fg">Сравнение с нормативами (для банка)</h2>
          <p className="mt-1 text-sm text-fg-secondary">
            Разрез класс → работа, итоги по каждому классу и общий, блок подписей. Позиции без
            норматива в отклонение не входят и показаны отдельным счётчиком.
          </p>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Label htmlFor="report-class">Класс объектов</Label>
              <EntitySelect
                id="report-class"
                className="mt-1.5 w-full"
                items={[{ id: ALL_CLASSES, title: "Все классы" }, ...(classesQ.data ?? [])]}
                value={rateClass}
                onChange={(value) => setRateClass(value === null ? ALL_CLASSES : String(value))}
                getLabel={(item) => item.title}
              />
            </div>
            <div>
              <Label htmlFor="report-date-from">Смета с</Label>
              <Input
                id="report-date-from"
                type="date"
                className="mt-1.5"
                value={dateFrom}
                onChange={(event) => setDateFrom(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="report-date-to">Смета по</Label>
              <Input
                id="report-date-to"
                type="date"
                className="mt-1.5"
                value={dateTo}
                onChange={(event) => setDateTo(event.target.value)}
              />
            </div>
          </div>

          <p className="mt-3 text-xs text-fg-tertiary">
            Период — по дате сметы (при её отсутствии по дате договора): по этой же дате
            подбирается норматив, поэтому отчёт и матрица отбирают одно и то же.
          </p>

          <Button
            className="mt-4"
            disabled={bank.isPending}
            onClick={() =>
              bank.mutate({
                rate_class_id: rateClass === ALL_CLASSES ? undefined : Number(rateClass),
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
              })
            }
          >
            <Download className="size-4" />
            {bank.isPending ? "Готовлю файл…" : "Скачать отчёт"}
          </Button>
        </Surface>
      </div>
    </div>
  );
}
