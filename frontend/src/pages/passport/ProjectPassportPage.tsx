import { useParams } from "react-router-dom";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { useProjectPassport } from "@/services/queries";

import { CategoryTable } from "./CategoryTable";
import { PassportHeader } from "./PassportHeader";
import { StructureRing } from "./StructureRing";

/**
 * Паспорт проекта по статьям классификатора (Ф6 фазы 7, спека §2.6, §2.9; план,
 * задача 7).
 *
 * **Ещё НЕ подключена к маршруту.** `/contracts/:contractId/passport` пока держит
 * `PassportPage` фазы 6 — одна задача не может занять два элемента на одном пути.
 * Тесты монтируют экран напрямую внутри `Routes` с тем же шаблоном пути. Задача 11
 * переключит маршрут и удалит старый экран.
 *
 * **Четыре состояния блока данных** (AGENTS.md §11, урок Ф5 §4a): загрузка, отказ,
 * пусто, заполнено — и они обязаны звучать РАЗНО. «Пусто» здесь означает
 * КОНКРЕТНЫЙ факт о данных: у договора есть карточка, но смета ещё не загружена
 * (`estimate === null`). При отказе запроса этот факт нам НЕИЗВЕСТЕН — сервер мог
 * упасть и по договору со сметой, и без неё, — поэтому отказ говорит о причине
 * попытки, а не о состоянии данных.
 */
export default function ProjectPassportPage() {
  const { contractId } = useParams();
  const id = contractId ? Number(contractId) : undefined;

  const passportQ = useProjectPassport(id);
  const passport = passportQ.data;

  if (passportQ.isPending) {
    return (
      <div className="container-page py-8">
        {/* Текст только для теста/скринридера: визуально состояние несут скелетоны. */}
        <p role="status" className="sr-only">
          Загрузка паспорта проекта…
        </p>
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-4 h-40 w-full" />
      </div>
    );
  }

  if (passportQ.isError || !passport) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Не удалось построить паспорт проекта"
          description="Запрос к серверу завершился отказом. Обновите страницу или попробуйте позже — сказать, загружена ли смета к договору, отсюда нельзя."
        />
      </div>
    );
  }

  if (passport.estimate === null) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Смета к договору ещё не загружена"
          description="Данных нет: паспорт по статьям классификатора появится после первой загрузки сметы. Реквизиты договора заведены и не пострадали."
        />
      </div>
    );
  }

  return (
    // data-print="sheet" — документ целиком (задача 10, спека §2.11): шапка,
    // кольцо структуры и таблица по статьям печатаются как один лист/свод, а
    // не по отдельности. Ровно один узел во всём дереве несёт эту метку.
    <div className="container-page py-8" data-print="sheet">
      <PassportHeader passport={passport} />
      <div className="mt-6">
        <StructureRing passport={passport} />
      </div>
      <div className="mt-6">
        <CategoryTable passport={passport} />
      </div>
    </div>
  );
}
