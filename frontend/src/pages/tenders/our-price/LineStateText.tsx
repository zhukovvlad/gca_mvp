import { Link } from "react-router-dom";

import type { OurPriceLine } from "@/types/domain";

import { isWaitingAdminLine } from "./ourPriceCopy";

interface LineStateTextProps {
  line: OurPriceLine;
  /** Подпись состояния строки (`lineStateLabel`). */
  label: string;
  /** Решает администратор: подпись «ждёт решения» ведёт на карточку контекста. */
  canResolve: boolean;
}

/**
 * Адрес решения на странице «Семьи» (спека 4а §2.11): при открытом предложении
 * семьи — очередь «Предложения» (`list` или `new`), где оно лежит, с единицей строки и
 * самим контекстом; иначе
 * (задание в ошибке, удержание, пауза) — карточка контекста.
 */
function resolveHref(line: OurPriceLine, contextId: number): string {
  if (line.suggestion_queue === null) return `/families?context=${contextId}`;
  return `/families?tab=suggestions&queue=${line.suggestion_queue}&unit=${line.unit_id ?? "none"}&context=${contextId}`;
}

/**
 * Подпись состояния строки. «Ждёт решения администратора» у `admin` — ссылка на
 * место решения на странице «Семьи» ({@link resolveHref}); у остальных и у строки
 * без контекста — тот же текст. Щелчок по ссылке не открывает панель строки.
 */
export function LineStateText({ line, label, canResolve }: LineStateTextProps) {
  if (!canResolve || !isWaitingAdminLine(line) || line.context_id === null) return <>{label}</>;
  return (
    <Link
      to={resolveHref(line, line.context_id)}
      className="underline underline-offset-2 hover:text-fg"
      onClick={(event) => event.stopPropagation()}
    >
      {label}
    </Link>
  );
}
