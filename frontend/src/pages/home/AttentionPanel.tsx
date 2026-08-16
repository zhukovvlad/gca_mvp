import type { DashboardAttention } from "@/types/domain";

/**
 * Пять строк решения 12 макета. Порядок — от самого дорогого к самому дешёвому
 * последствию, как в макете.
 */
const ROWS: {
  key: keyof DashboardAttention;
  title: string;
  detail: string;
  tone: "high" | "mid" | "low";
}[] = [
  {
    key: "estimates_without_vat_rate",
    title: "Сметы без ставки НДС",
    detail: "Нетто вывести не из чего: суммы скрыты, отклонения не считаются",
    tone: "high",
  },
  {
    key: "objects_with_several_contracts",
    title: "Объекты с несколькими договорами ГП",
    detail:
      "Признака архивного договора в базе нет — объект считается дважды в матрице и в отчёте «для банка»",
    tone: "mid",
  },
  {
    key: "contracts_without_estimate",
    title: "Договоры без загруженной сметы",
    detail: "Карточка есть, расценок нет — в аналитику договор не входит",
    tone: "mid",
  },
  {
    key: "objects_without_area",
    title: "Объекты без площади (ТЭП)",
    detail: "₽/м² не выводится — колонка честно пустая",
    tone: "low",
  },
  {
    key: "failed_imports_30d",
    title: "Импорты, завершившиеся ошибкой",
    detail: "За последние 30 дней. Файлы хранятся, загрузку можно повторить",
    tone: "low",
  },
];

const TONE_CLASS: Record<"high" | "mid" | "low", string> = {
  high: "bg-warning",
  mid: "bg-accent",
  low: "bg-border-default",
};

/**
 * ДЕЙСТВИЙ В СТРОКАХ НЕТ — отступление от макета, решение пользователя на
 * гейте 3.
 *
 * В макете у каждой строки нарисована кнопка («Назначить ставки», «Загрузить»,
 * «Заполнить ТЭП»), но у всех `href="#"`: адресаты в макете не решены. Одна
 * кнопка на строку и не может работать — диагностика называет N договоров, а
 * `/contracts/:id/passport` ведёт к одному. Годных вариантов было два
 * (раскрытие строки в список затронутых сущностей либо фильтр «проблема» на
 * списке договоров), оба отложены; второй к тому же не покрывает две объектные
 * строки — у объектов нет ни экрана, ни маршрута.
 *
 * v1 показывает СЧЁТЧИКИ БЕЗ ДЕЙСТВИЙ. Мёртвая кнопка `href="#"` запрещена
 * прямо: она обещает работу, которой нет.
 */
export function AttentionPanel({ data }: { data: DashboardAttention }) {
  const shown = ROWS.filter((row) => data[row.key] > 0);

  if (shown.length === 0) {
    // Пустое состояние — ОДНА строка. Нулевые строки рядом с ней не
    // показываются: «всё сходится» и список нулей — два разных сообщения, и
    // вместе они противоречат друг другу.
    return (
      <div
        data-testid="attention-ok"
        className="rounded-lg border border-border-subtle bg-surface px-4 py-4 text-sm text-fg-secondary"
      >
        Всё, что база умеет проверить, сходится
      </div>
    );
  }

  return (
    <div
      data-testid="attention-issues"
      className="overflow-hidden rounded-lg border border-border-subtle bg-surface"
    >
      {shown.map((row) => (
        <div
          key={row.key}
          data-testid={`attention-${row.key}`}
          className="flex items-start gap-3 border-b border-border-subtle px-4 py-3 last:border-b-0"
        >
          <span className={`mt-1 h-8 w-0.5 flex-none rounded-full ${TONE_CLASS[row.tone]}`} />
          <div className="min-w-0 flex-1">
            <div className="text-sm text-fg">{row.title}</div>
            <div className="mt-0.5 text-xs text-fg-tertiary">{row.detail}</div>
          </div>
          <span className="font-mono text-lg text-fg">{data[row.key]}</span>
        </div>
      ))}
    </div>
  );
}
