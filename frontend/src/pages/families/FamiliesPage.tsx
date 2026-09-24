import { useState } from "react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ContextCard } from "./ContextCard";
import { ContextsTab } from "./ContextsTab";
import { FamiliesTab } from "./FamiliesTab";

/**
 * Экран «Семьи и контексты» (спека 2026-09-22-catalog-families-design.md
 * §2.10), маршрут `/families`, право `admin` — обёрнут `RequireAdmin` в
 * `App.tsx`, тем же входом, что и `/standards`.
 *
 * Три области: «Семьи» (жизненный цикл семей работ), «Контексты» (очередь и
 * поиск), «Операции» (карточка выбранного контекста и действия над ним —
 * карточка сама не мутирует, кроме как через эти действия).
 */
export default function FamiliesPage() {
  const [selectedContextId, setSelectedContextId] = useState<number | null>(null);
  const [tab, setTab] = useState("families");

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Семьи и контексты"
        subtitle="Семьи работ, семантика контекстов каталога и операции над ними"
      />

      <Tabs value={tab} onValueChange={(v) => v && setTab(v)} className="mt-6">
        <TabsList>
          <TabsTrigger value="families">Семьи</TabsTrigger>
          <TabsTrigger value="contexts">Контексты</TabsTrigger>
          <TabsTrigger value="operations">Операции</TabsTrigger>
        </TabsList>

        <TabsContent value="families" className="mt-4">
          <FamiliesTab />
        </TabsContent>

        <TabsContent value="contexts" className="mt-4">
          <ContextsTab
            selectedContextId={selectedContextId}
            onSelect={(id) => {
              setSelectedContextId(id);
              setTab("operations");
            }}
          />
        </TabsContent>

        <TabsContent value="operations" className="mt-4">
          <ContextCard contextId={selectedContextId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
