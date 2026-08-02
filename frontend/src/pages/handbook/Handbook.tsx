import { Link } from "react-router-dom";
import { Clock, ChevronRight } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { HANDBOOK_ARTICLES, groupByCategory } from "./articles";

const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" });

export function Handbook() {
  const groups = groupByCategory(HANDBOOK_ARTICLES);

  return (
    <div className="container-page py-8">
      <PageHeader
        title="Справочник"
        subtitle="Как устроено приложение: методики расчётов, правила обработки данных и ответы на частые вопросы."
      />

      {groups.length === 0 ? (
        <div className="mt-12 rounded-xl border border-dashed border-border-default bg-surface-sunken px-6 py-12 text-center text-fg-tertiary">
          Статей пока нет.
        </div>
      ) : (
        groups.map(({ category, items }) => (
          <section key={category} className="mt-8">
            <h2 className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">{category}</h2>
            <div className="mt-3 space-y-3">
              {items.map((a) => (
                <Link
                  key={a.slug}
                  to={`/handbook/${a.slug}`}
                  className="group flex items-center gap-4 rounded-lg border border-border-subtle bg-surface px-5 py-4 transition-colors hover:border-border-default hover:bg-surface-hover"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-fg">{a.title}</p>
                    <p className="mt-1 text-sm leading-6 text-fg-secondary">{a.description}</p>
                    <p className="mt-2 flex items-center gap-1.5 text-xs text-fg-tertiary">
                      <Clock className="h-3.5 w-3.5" />
                      {a.readingMinutes} мин · обновлено {dateFmt.format(new Date(a.updated))}
                    </p>
                  </div>
                  <ChevronRight className="h-5 w-5 shrink-0 text-fg-tertiary transition-transform group-hover:translate-x-0.5" />
                </Link>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}

export default Handbook;
