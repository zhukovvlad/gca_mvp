import { cn } from "@/lib/utils";
import type { OurPriceArticleRef, OurPriceChapterStep } from "@/types/domain";

import { WHERE_ARTICLE, WHERE_PATH } from "./ourPriceCopy";

export interface LineWhereProps {
  /** Статья раздела строки; `null` - «Нераспределённое», `undefined` - ответ её не принёс. */
  article: OurPriceArticleRef | null | undefined;
  /** Путь по смете от её корня; может быть пустым. */
  chapterPath: OurPriceChapterStep[];
  /** Чья смета: строка КП (по умолчанию) или договор аналога. */
  scope?: "kp" | "contract";
  className?: string;
}

const LABEL = "text-[0.6875rem] font-medium uppercase tracking-wide text-fg-tertiary";

/**
 * «Где строка» (спека упрощения каталога §2.11): цепочка статей классификатора с кодом и названием
 * и полный путь по смете с номерами разделов. Общий для строки КП и карточки аналога: у аналога —
 * статья и путь ЕГО раздела в смете договора, а не строки КП. Нет ни статьи, ни пути — не рисуется.
 */
export function LineWhere({ article, chapterPath, scope = "kp", className }: LineWhereProps) {
  const hasArticle = article !== null && article !== undefined;
  if (!hasArticle && chapterPath.length === 0) return null;
  return (
    <div data-testid="line-where" className={cn("space-y-1.5", className)}>
      {hasArticle && (
        <div className="space-y-0.5">
          <p className={LABEL}>{WHERE_ARTICLE}</p>
          <ol data-testid="where-article" className="flex flex-wrap items-center gap-x-1 text-xs">
            {article.chain.map((step, index) => (
              <li
                key={step.id}
                className={cn(
                  "after:ml-1 after:text-fg-tertiary after:content-['›'] last:after:content-none",
                  index === article.chain.length - 1 && "rounded-md bg-accent-soft px-1.5 py-0.5 text-accent-text"
                )}
              >
                {step.code === null ? step.title : `${step.code} ${step.title}`}
              </li>
            ))}
          </ol>
        </div>
      )}
      {chapterPath.length > 0 && (
        <div className="space-y-0.5">
          <p className={LABEL}>{WHERE_PATH[scope]}</p>
          <ul data-testid="where-path" className="text-xs text-fg-secondary">
            {chapterPath.map((step, depth) => (
              <li key={step.chapter_item_id} style={{ paddingLeft: depth * 14 }}>
                {step.number !== null && (
                  <>
                    <span className="font-mono text-fg-tertiary">п. {step.number}</span>
                    {" — "}
                  </>
                )}
                {step.title}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
