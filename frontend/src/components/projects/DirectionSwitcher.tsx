import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

interface DirectionSwitcherProps {
  directions: { code: string; name: string }[];
  /** 'all' | code направления */
  value: string;
  onChange: (code: string) => void;
}

/**
 * Переключатель направлений (спека §3.1, §7.1). Скрыт у пустого объекта.
 * Сегментированный фильтр на shadcn-примитиве ToggleGroup (одиночный выбор):
 * семантика toggle (`aria-pressed`), а не tabs — у фильтра нет панелей.
 * Вид — «утопленный» контейнер + приподнятая активная пилюля на токенах темы
 * (дефолтная палитра shadcn под фильтр на тёмном фоне не годится).
 */
export function DirectionSwitcher({ directions, value, onChange }: DirectionSwitcherProps) {
  if (directions.length === 0) return null;
  const items = [{ code: "all", name: "Все направления" }, ...directions];
  return (
    <ToggleGroup
      aria-label="Направления"
      data-testid="direction-switcher"
      spacing={1}
      value={[value]}
      onValueChange={(vals) => {
        const next = vals[0];
        // Клик по уже активному сегменту снимает выбор (vals=[]) — игнорируем:
        // у фильтра всегда ровно одно активное направление.
        if (next) onChange(next);
      }}
      className="rounded-lg border border-border-subtle bg-surface-sunken p-1"
    >
      {items.map((d) => (
        <ToggleGroupItem
          key={d.code}
          value={d.code}
          data-testid={`direction-${d.code}`}
          className={cn(
            "text-fg-secondary hover:bg-transparent hover:text-fg",
            // активный сегмент — светлая «пилюля» (primary) на тёмном треке: пара
            // primary/primary-foreground даёт явный контраст (surface≈surface-sunken
            // в тёмной теме неразличимы)
            "aria-pressed:bg-primary aria-pressed:text-primary-foreground aria-pressed:shadow-sm aria-pressed:hover:bg-primary",
          )}
        >
          {d.name}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
