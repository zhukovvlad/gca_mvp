import { useState } from "react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAppSettings, useUpdateAppSettings } from "@/services/queries";

/**
 * Настройки приложения (AGENTS.md §7.4: «N хранится в БД … экран Settings»).
 *
 * Право — `admin` (проверяется и маршрутом, и сервером). Раскладка взята у
 * `Settings.tsx` источника — секции слева, форма справа; содержимое источника
 * (настройки LLM) к проекту не относится, LLM-домен удалён фазой 1.
 *
 * **Границы диапазона приходят с сервера.** Они выражают `CHECK` в БД (миграция
 * 0004), и зашить их здесь значило бы завести второе представление ограничения,
 * которое разъедется с первым. Локальная проверка нужна, чтобы форма не отправляла
 * заведомо отвергаемое, но авторитетен сервер: его отказ объясняет ещё и причину
 * границы (подобрана под раскладку экрана паспорта фазы 6, а не взята произвольно).
 */
export default function SettingsPage() {
  const settingsQ = useAppSettings();
  const update = useUpdateAppSettings();
  const settings = settingsQ.data;

  /**
   * Черновик поля: `null` — «человек не правил», и тогда показывается серверное
   * значение. Эффекта, синхронизирующего поле с ответом, здесь намеренно нет —
   * он и не нужен, и вреден: `setState` внутри эффекта даёт каскадный рендер
   * (на это справедливо ругается `react-hooks/set-state-in-effect`), а главное —
   * перетёр бы набранное, если ответ обновится во время правки.
   */
  const [draft, setDraft] = useState<string | null>(null);
  const topN = draft ?? (settings ? String(settings.passport_top_n) : "");

  if (settingsQ.isPending) {
    return (
      <div className="container-page py-8">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-4 h-40 w-full" />
      </div>
    );
  }

  const min = settings?.passport_top_n_min ?? 1;
  const max = settings?.passport_top_n_max ?? 20;
  const parsed = Number(topN);
  const valid = Number.isInteger(parsed) && parsed >= min && parsed <= max;
  const unchanged = settings !== undefined && parsed === settings.passport_top_n;

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Настройки"
        subtitle="Значения, влияющие на печатные формы и выгрузки. Хранятся в базе, а не в конфиге."
      />

      <div className="mt-6 grid gap-6 lg:grid-cols-[220px_1fr]">
        <nav className="text-sm">
          <span className="block rounded-md bg-surface-hover px-3 py-1.5 font-medium text-fg">
            Паспорт объекта
          </span>
        </nav>

        <Surface>
          <form
            className="max-w-md"
            onSubmit={(event) => {
              event.preventDefault();
              // После сохранения черновик снимается: поле снова показывает то, что
              // реально лежит в базе, — в том числе если сервер что-то поправил.
              if (valid) update.mutate(parsed, { onSuccess: () => setDraft(null) });
            }}
          >
            <Label htmlFor="passport-top-n">Ключевых расценок в паспорте</Label>
            <Input
              id="passport-top-n"
              // type=number с шагом: значение целое, а раскладка с запятой здесь
              // не при чём — `normalizeDecimalInput` нужен деньгам, не счётчику.
              type="number"
              inputMode="numeric"
              min={min}
              max={max}
              step={1}
              value={topN}
              onChange={(event) => setDraft(event.target.value)}
              className="mt-1.5"
              aria-describedby="passport-top-n-hint"
            />
            <p id="passport-top-n-hint" className="mt-2 text-xs text-fg-tertiary">
              Сколько самых дорогих работ последней сметы попадает в паспорт. Допустимо от {min} до{" "}
              {max}: верхняя граница подобрана под раскладку экрана паспорта фазы 6, а не взята
              произвольно.
            </p>

            {!valid && topN !== "" && (
              <p role="alert" className="mt-2 text-xs text-warning-text">
                Введите целое число от {min} до {max}.
              </p>
            )}

            <Button type="submit" className="mt-4" disabled={!valid || unchanged || update.isPending}>
              {update.isPending ? "Сохраняю…" : "Сохранить"}
            </Button>
          </form>
        </Surface>
      </div>
    </div>
  );
}
