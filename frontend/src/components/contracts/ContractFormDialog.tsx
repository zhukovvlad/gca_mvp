import { useState } from "react";

import { EntityCombobox } from "@/components/domain/EntityCombobox";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useContractors,
  useCreateContract,
  useCreateContractor,
  useCreateObject,
  useObjects,
  useRateClasses,
  useUpdateContract,
} from "@/services/queries";
import type { ContractCard, ContractInput } from "@/types/domain";

interface ContractFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Задан — правка карточки, иначе создание. */
  contract?: ContractCard;
  onCreated?: (contract: ContractCard) => void;
}

interface FormState {
  object_id: number | null;
  contractor_id: number | null;
  rate_class_id: number | null;
  contract_number: string;
  signed_date: string;
  title: string;
  signer: string;
  total_amount: string;
  notes: string;
}

const EMPTY: FormState = {
  object_id: null,
  contractor_id: null,
  rate_class_id: null,
  contract_number: "",
  signed_date: "",
  title: "",
  signer: "",
  total_amount: "",
  notes: "",
};

function fromContract(contract: ContractCard): FormState {
  return {
    object_id: contract.object_id,
    contractor_id: contract.contractor_id,
    rate_class_id: contract.rate_class_id,
    contract_number: contract.contract_number,
    signed_date: contract.signed_date,
    title: contract.title ?? "",
    signer: contract.signer ?? "",
    total_amount: contract.total_amount ?? "",
    notes: contract.notes ?? "",
  };
}

/**
 * Форма договора (§7.1).
 *
 * Объект и подрядчик выбираются или **создаются по месту** — решение §6.1.
 * Класс договора можно не указывать: сервер подставит класс объекта, потому что
 * это снимок на момент создания (§4), а `objects.rate_class_id` для того и
 * существует. Подсказка об этом стоит рядом с полем.
 *
 * Сумма — текстовое поле, и уходит на сервер **строкой**: §3 запрещает float, а
 * `<input type="number">` отдал бы именно его.
 */
export function ContractFormDialog({
  open,
  onOpenChange,
  contract,
  onCreated,
}: ContractFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        {/*
          Тело формы — отдельный компонент, и в дереве его нет, пока диалог
          закрыт. Поэтому на каждое открытие оно монтируется заново, а начальные
          значения задаёт `useState` — без эффекта, сбрасывающего поля (setState в
          эффекте вызывает каскадный рендер).
        */}
        {open && (
          <ContractForm
            key={contract?.id ?? "new"}
            contract={contract}
            onOpenChange={onOpenChange}
            onCreated={onCreated}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ContractForm({
  contract,
  onOpenChange,
  onCreated,
}: {
  contract?: ContractCard;
  onOpenChange: (open: boolean) => void;
  onCreated?: (contract: ContractCard) => void;
}) {
  const isEdit = contract !== undefined;
  const [form, setForm] = useState<FormState>(contract ? fromContract(contract) : EMPTY);
  /**
   * Черновик нового подрядчика. Отдельный шаг, а не создание одним нажатием:
   * БИН/ИНН обязателен и уникален (`uq_contractors_inn`), и подставить туда
   * заглушку значило бы засорить справочник значением, которое потом никто не
   * отличит от настоящего. У объекта такой проблемы нет — ему достаточно названия.
   */
  const [contractorDraft, setContractorDraft] = useState<{ title: string; inn: string } | null>(
    null
  );

  const objectsQ = useObjects({ page_size: 100 });
  const contractorsQ = useContractors({ page_size: 100 });
  const classesQ = useRateClasses();

  const createObject = useCreateObject();
  const createContractor = useCreateContractor();
  const createContract = useCreateContract();
  const updateContract = useUpdateContract();

  function patch(fields: Partial<FormState>) {
    setForm((prev) => ({ ...prev, ...fields }));
  }

  async function handleCreateObject(query: string) {
    const title = query.trim();
    if (!title) return;
    const created = await createObject.mutateAsync({ title });
    patch({ object_id: created.id });
  }

  async function handleSaveContractorDraft() {
    if (!contractorDraft) return;
    const title = contractorDraft.title.trim();
    const inn = contractorDraft.inn.trim();
    if (!title || !inn) return;
    try {
      const created = await createContractor.mutateAsync({ title, inn });
      patch({ contractor_id: created.id });
      setContractorDraft(null);
    } catch {
      // Причина уже в тосте (чаще всего — БИН/ИНН занят); черновик оставляем.
    }
  }

  const canSubmit =
    form.object_id !== null &&
    form.contractor_id !== null &&
    form.contract_number.trim().length > 0 &&
    form.signed_date.length > 0;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;

    const payload: ContractInput = {
      object_id: form.object_id as number,
      contractor_id: form.contractor_id as number,
      contract_number: form.contract_number.trim(),
      signed_date: form.signed_date,
      rate_class_id: form.rate_class_id,
      title: form.title.trim() || null,
      signer: form.signer.trim() || null,
      total_amount: form.total_amount.trim() || null,
      notes: form.notes.trim() || null,
    };

    try {
      if (isEdit) {
        await updateContract.mutateAsync({ id: contract.id, input: payload });
      } else {
        const created = await createContract.mutateAsync(payload);
        onCreated?.(created);
      }
      onOpenChange(false);
    } catch {
      // Текст отказа уже показан тостом (`toastApiError`); диалог оставляем
      // открытым, чтобы человек исправил введённое, а не набирал заново.
    }
  }

  const pending = createContract.isPending || updateContract.isPending;

  return (
    <>
      <DialogHeader>
        <DialogTitle>{isEdit ? "Правка договора" : "Новый договор"}</DialogTitle>
        <DialogDescription>
          Карточка договора — источник истины при импорте: объект, подрядчик и
          реквизиты берутся отсюда, а не из файла сметы.
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="contract-object">Объект</Label>
          <EntityCombobox
            id="contract-object"
            items={objectsQ.data?.items ?? []}
            value={form.object_id}
            onChange={(id) => patch({ object_id: id })}
            getLabel={(item) => item.title}
            getHint={(item) => item.rate_class_title ?? undefined}
            placeholder="Выберите объект"
            searchPlaceholder="Название объекта"
            emptyText="Объект не найден — его можно создать"
            onCreateRequest={handleCreateObject}
            createLabel="Создать объект"
            disabled={createObject.isPending}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-contractor">Подрядчик</Label>
          <EntityCombobox
            id="contract-contractor"
            items={contractorsQ.data?.items ?? []}
            value={form.contractor_id}
            onChange={(id) => patch({ contractor_id: id })}
            getLabel={(item) => item.title}
            getHint={(item) => item.inn}
            placeholder="Выберите подрядчика"
            searchPlaceholder="Название или БИН/ИНН"
            emptyText="Подрядчик не найден — его можно создать"
            onCreateRequest={(query) =>
              setContractorDraft({ title: query.trim(), inn: "" })
            }
            createLabel="Создать подрядчика"
            disabled={createContractor.isPending}
          />
          {contractorDraft && (
            <div className="grid gap-2 rounded-md border border-border-subtle p-3">
              <p className="text-xs text-fg-secondary">
                Новый подрядчик. БИН/ИНН обязателен и уникален — по нему подрядчик
                и опознаётся в базе.
              </p>
              <Input
                aria-label="Название подрядчика"
                placeholder="Название"
                value={contractorDraft.title}
                onChange={(e) =>
                  setContractorDraft({ ...contractorDraft, title: e.target.value })
                }
              />
              <Input
                aria-label="БИН/ИНН подрядчика"
                placeholder="БИН / ИНН"
                value={contractorDraft.inn}
                onChange={(e) =>
                  setContractorDraft({ ...contractorDraft, inn: e.target.value })
                }
              />
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSaveContractorDraft}
                  disabled={
                    !contractorDraft.title.trim() ||
                    !contractorDraft.inn.trim() ||
                    createContractor.isPending
                  }
                >
                  Сохранить подрядчика
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setContractorDraft(null)}
                >
                  Отмена
                </Button>
              </div>
            </div>
          )}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 sm:gap-4">
          <div className="grid gap-2">
            <Label htmlFor="contract-number">Номер договора</Label>
            <Input
              id="contract-number"
              value={form.contract_number}
              onChange={(e) => patch({ contract_number: e.target.value })}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="contract-signed-date">Дата подписания</Label>
            <Input
              id="contract-signed-date"
              type="date"
              value={form.signed_date}
              onChange={(e) => patch({ signed_date: e.target.value })}
              required
            />
          </div>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-rate-class">Класс объектов</Label>
          <Select
            value={form.rate_class_id === null ? "" : String(form.rate_class_id)}
            onValueChange={(value: string | null) =>
              patch({ rate_class_id: value ? Number(value) : null })
            }
          >
            <SelectTrigger id="contract-rate-class">
              <SelectValue placeholder="Как у объекта">
                {(raw) =>
                  raw
                    ? (classesQ.data?.find((c) => String(c.id) === raw)?.title ??
                      "Как у объекта")
                    : "Как у объекта"
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {(classesQ.data ?? []).map((rateClass) => (
                <SelectItem key={rateClass.id} value={String(rateClass.id)}>
                  {rateClass.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-fg-tertiary">
            Не выбран — возьмётся класс объекта. Класс договора фиксируется снимком:
            позже переклассификация объекта не изменит отклонения этой сметы.
          </p>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 sm:gap-4">
          <div className="grid gap-2">
            <Label htmlFor="contract-signer">Подписант</Label>
            <Input
              id="contract-signer"
              value={form.signer}
              onChange={(e) => patch({ signer: e.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="contract-total">Сумма договора</Label>
            <Input
              id="contract-total"
              inputMode="decimal"
              placeholder="1234567.89"
              value={form.total_amount}
              onChange={(e) => patch({ total_amount: e.target.value })}
            />
          </div>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-title">Название</Label>
          <Input
            id="contract-title"
            value={form.title}
            onChange={(e) => patch({ title: e.target.value })}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-notes">Примечания</Label>
          <Textarea
            id="contract-notes"
            rows={2}
            value={form.notes}
            onChange={(e) => patch({ notes: e.target.value })}
          />
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSubmit || pending}>
            {isEdit ? "Сохранить" : "Создать договор"}
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}
