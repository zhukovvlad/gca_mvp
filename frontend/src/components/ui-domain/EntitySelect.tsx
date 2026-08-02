import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

interface EntitySelectProps<T extends { id: number | string }> {
  items: T[] | undefined;
  value: T["id"] | null;
  onChange: (value: T["id"] | null) => void;
  getLabel: (item: T) => string;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}

/**
 * Select по сущностям с id, отображающий человекочитаемые имена
 * вместо id в trigger. Фикс к base-ui Select.Value, который
 * по-умолчанию показывает голое value.
 */
export function EntitySelect<T extends { id: number | string }>({
  items,
  value,
  onChange,
  getLabel,
  placeholder = "—",
  className,
  disabled,
}: EntitySelectProps<T>) {
  const list = items ?? [];

  return (
    <Select
      value={value !== null && value !== undefined ? String(value) : ""}
      onValueChange={(v: string | null) => {
        if (!v) {
          onChange(null);
          return;
        }
        // Сохраняем тип id (number или string) — определяем по первому item
        const sample = list[0];
        const typedValue =
          sample && typeof sample.id === "number" ? Number(v) : v;
        onChange(typedValue as T["id"]);
      }}
      disabled={disabled}
    >
      <SelectTrigger className={cn("w-full", className)}>
        <SelectValue placeholder={placeholder}>
          {(raw) => {
            if (!raw) return placeholder;
            const found = list.find((item) => String(item.id) === raw);
            return found ? getLabel(found) : placeholder;
          }}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {list.map((item) => (
          <SelectItem key={item.id} value={String(item.id)}>
            {getLabel(item)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
