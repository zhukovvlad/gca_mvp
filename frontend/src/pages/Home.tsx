import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";

/**
 * Временная главная страница (фаза 1, AGENTS.md §9).
 * В фазе 5 здесь появится экран «Договоры/Объекты».
 */
export default function Home() {
  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="База расценок генподряда"
        subtitle="Единая база расценок договоров ГП 2024–2027"
      />
      <div className="mt-8">
        <EmptyState
          title="Договоры появятся здесь"
          description="Экран договоров и загрузки смет — в фазе 5 (AGENTS.md §9). Сейчас доступна админ-консоль пользователей."
        />
      </div>
    </div>
  );
}
