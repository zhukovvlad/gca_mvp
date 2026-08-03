import { useState } from "react";
import { Check, ChevronsUpDown, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

interface EntityComboboxProps<T extends { id: number }> {
  items: T[];
  value: number | null;
  onChange: (id: number | null) => void;
  getLabel: (item: T) => string;
  getHint?: (item: T) => string | undefined;
  placeholder: string;
  searchPlaceholder: string;
  emptyText: string;
  /** Текст поиска наружу — форма создания подставляет его в название. */
  onQueryChange?: (query: string) => void;
  /** Не задан — кнопки «создать» нет (у пользователя нет права, §6.2). */
  onCreateRequest?: (query: string) => void;
  createLabel?: string;
  disabled?: boolean;
  id?: string;
}

/**
 * Комбобокс «выбрать или создать» — решение фазы 5 §6.1.
 *
 * Объект и подрядчик заводятся **по месту**, в форме договора: пока договора нет,
 * объект в системе ничего не значит. Отдельных экранов-справочников у них поэтому
 * нет, и создание живёт здесь.
 *
 * Собран из shadcn `Command` + `Popover` — это штатный способ сделать комбобокс;
 * своей реализации выпадающего списка с поиском не пишем.
 */
export function EntityCombobox<T extends { id: number }>({
  items,
  value,
  onChange,
  getLabel,
  getHint,
  placeholder,
  searchPlaceholder,
  emptyText,
  onQueryChange,
  onCreateRequest,
  createLabel = "Создать",
  disabled,
  id,
}: EntityComboboxProps<T>) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selected = items.find((item) => item.id === value);

  function handleQuery(next: string) {
    setQuery(next);
    onQueryChange?.(next);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            id={id}
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            disabled={disabled}
            className="w-full justify-between font-normal"
          >
            <span className={cn("truncate", !selected && "text-muted-foreground")}>
              {selected ? getLabel(selected) : placeholder}
            </span>
            <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
          </Button>
        }
      />
      <PopoverContent className="w-[--anchor-width] min-w-72 p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder={searchPlaceholder}
            value={query}
            onValueChange={handleQuery}
          />
          <CommandList>
            <CommandEmpty>{emptyText}</CommandEmpty>
            <CommandGroup>
              {items.map((item) => (
                <CommandItem
                  key={item.id}
                  value={String(item.id)}
                  onSelect={() => {
                    onChange(item.id);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      "mr-2 size-4",
                      item.id === value ? "opacity-100" : "opacity-0"
                    )}
                  />
                  <span className="truncate">{getLabel(item)}</span>
                  {getHint?.(item) && (
                    <span className="ml-auto truncate pl-2 text-xs text-fg-tertiary">
                      {getHint(item)}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
            {onCreateRequest && (
              <CommandGroup>
                <CommandItem
                  value="__create__"
                  onSelect={() => {
                    onCreateRequest(query);
                    setOpen(false);
                  }}
                >
                  <Plus className="mr-2 size-4" />
                  {query.trim() ? `${createLabel}: «${query.trim()}»` : createLabel}
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
