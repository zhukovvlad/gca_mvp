import { useState } from "react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ContextsTab } from "./ContextsTab";
import { FamiliesTab } from "./FamiliesTab";
import { SOURCE_EXPLANATION } from "./labels";
import { SourceChip } from "./SourceChip";

/**
 * Экран «Семьи и контексты» (спека `2026-09-25-families-screen-design.md`
 * §2.1), маршрут `/families`, право `admin` — обёрнут `RequireAdmin` в
 * `App.tsx`, тем же входом, что и `/standards`.
 *
 * Две вкладки: «Семьи» (жизненный цикл семей работ, панель правки справа от
 * списка) и «Контексты» (очередь, поиск и карточка выбранного контекста
 * панелью справа от списка, той же вкладкой — переключаться некуда).
 * Вкладка «Операции» фичи 1 упразднена (спека §2.1): карточка встала рядом со
 * списком внутри {@link ContextsTab}, а не отдельной областью экрана.
 */
export default function FamiliesPage() {
  const [tab, setTab] = useState("families");

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Семьи и контексты"
        subtitle="Семьи работ, семантика контекстов каталога и операции над ними"
      />

      {/* Легенда источника подписи (спека §2.3) — текстом, не только в
          подсказке чипа: та же пара `SourceChip`/`SOURCE_EXPLANATION`, что
          несут строки списков и карточка, здесь только пересказана словами. */}
      <div className="mt-4 flex flex-wrap items-center gap-4 text-sm text-fg-secondary">
        <div className="flex items-center gap-2">
          <SourceChip kind="classifier" />
          <span>{SOURCE_EXPLANATION.classifier}</span>
        </div>
        <div className="flex items-center gap-2">
          <SourceChip kind="estimate" />
          <span>{SOURCE_EXPLANATION.estimate}</span>
        </div>
      </div>

      <Tabs value={tab} onValueChange={(v) => v && setTab(v)} className="mt-6">
        <TabsList>
          <TabsTrigger value="families">Семьи</TabsTrigger>
          <TabsTrigger value="contexts">Контексты</TabsTrigger>
        </TabsList>

        <TabsContent value="families" className="mt-4">
          <FamiliesTab />
        </TabsContent>

        <TabsContent value="contexts" className="mt-4">
          <ContextsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
