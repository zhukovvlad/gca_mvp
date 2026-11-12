import { useCallback, useState } from "react";

import { useJournal } from "@/services/queries";

/** Ключ `localStorage`: время последнего просмотра журнала, ISO-строкой. */
export const JOURNAL_SEEN_KEY = "gca.journal.seenAt";

/** Сколько записей запрашивается для счёта: предел страницы сервера; больше — «100+». */
export const JOURNAL_NEW_LIMIT = 100;

/**
 * Прочитанное время просмотра. `undefined` — хранилище недоступно (бросает при чтении: приватный
 * режим, запрет), счётчика тогда нет; `null` — ключа нет (журнал ещё не открывали).
 */
function readSeen(): string | null | undefined {
  try {
    return localStorage.getItem(JOURNAL_SEEN_KEY);
  } catch {
    return undefined;
  }
}

function writeSeen(iso: string) {
  try {
    localStorage.setItem(JOURNAL_SEEN_KEY, iso);
  } catch {
    // Квота или запрет записи: просмотр живёт только в состоянии этой сессии.
  }
}

/**
 * Счётчик новых записей «Журнала ИИ» (спека упрощения каталога §2.7): записи после последнего
 * просмотра. Первого просмотра ещё не было — точкой отсчёта становится нынешний момент, поэтому
 * счётчик не начинается с накопленной за всё время ленты. Хранилище недоступно — `count` равен
 * `null`, страница работает без счётчика.
 */
export function useJournalSeen(): { count: number | null; markSeen: () => void } {
  const [seenAt, setSeenAt] = useState<string | undefined>(() => {
    const stored = readSeen();
    if (stored !== null) return stored;
    const now = new Date().toISOString();
    writeSeen(now);
    return now;
  });
  const recent = useJournal({ limit: JOURNAL_NEW_LIMIT });

  // Стабильная ссылка: вкладка зовёт её из эффекта при открытии, и новая ссылка на каждый рендер
  // зациклила бы запись.
  const markSeen = useCallback(() => {
    const now = new Date().toISOString();
    setSeenAt((prev) => (prev === undefined ? prev : now));
    writeSeen(now);
  }, []);

  if (seenAt === undefined || recent.data === undefined) return { count: null, markSeen };
  const since = Date.parse(seenAt);
  if (Number.isNaN(since)) return { count: null, markSeen };
  const count = recent.data.items.filter((item) => Date.parse(item.created_at) > since).length;
  return { count, markSeen };
}
