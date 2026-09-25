import { useCallback, useState } from "react";

import { PAGE_SIZE_OPTIONS } from "@/components/domain/pageSize";

/** Отсутствующий ключ: `Number(null)` — `0`, он и так вне {@link PAGE_SIZE_OPTIONS}. */
function readPersisted(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    const parsed = Number(raw);
    if (!(PAGE_SIZE_OPTIONS as readonly number[]).includes(parsed)) return fallback;
    return parsed;
  } catch {
    return fallback;
  }
}

/**
 * Размер страницы списка, запомненный в `localStorage` (план Task 6,
 * «Решения плана» п. 5 — ключ `gca.families.<список>.pageSize`, умолчание
 * 20 для обоих списков). Недопустимое сохранённое значение или исключение
 * при доступе к `localStorage` (приватный режим браузера, квота) —
 * умолчание; ошибка ЗАПИСИ (сеттер) тоже не бросается наружу — экран не
 * обязан падать из-за диска, которым не управляет.
 */
export function usePersistedPageSize(
  key: string,
  fallback: number
): [number, (n: number) => void] {
  const [size, setSize] = useState<number>(() => readPersisted(key, fallback));

  const persist = useCallback(
    (n: number) => {
      setSize(n);
      try {
        localStorage.setItem(key, String(n));
      } catch {
        // Диск/приватный режим недоступны — значение живёт только в состоянии
        // этой сессии (докстрока выше), явный no-op.
      }
    },
    [key]
  );

  return [size, persist];
}
