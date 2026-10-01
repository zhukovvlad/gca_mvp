import { useState } from "react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ContextsTab } from "./ContextsTab";
import { FamiliesTab } from "./FamiliesTab";
import { SOURCE_EXPLANATION } from "./labels";
import { SourceChip } from "./SourceChip";
import { SuggestionsTab } from "./SuggestionsTab";

/**
 * Экран «Семьи и контексты» (спека `2026-09-25-families-screen-design.md`
 * §2.1), маршрут `/families`, право `admin` — обёрнут `RequireAdmin` в
 * `App.tsx`, тем же входом, что и `/standards`.
 *
 * Три вкладки: «Семьи» (жизненный цикл семей работ, панель правки справа от
 * списка), «Контексты» (очередь, поиск и карточка выбранного контекста
 * панелью справа от списка, той же вкладкой — переключаться некуда) и
 * «Предложения» (ответы модели о семье контекстов и очередь заданий, спека
 * `2026-09-28-semantic-suggestions-design.md` §2.12).
 * Вкладка «Операции» фичи 1 упразднена (спека §2.1): карточка встала рядом со
 * списком внутри {@link ContextsTab}, а не отдельной областью экрана.
 */
export default function FamiliesPage() {
  const [tab, setTab] = useState("families");
  // Семья, к которой перешли по ссылке «Открыть семью» из вкладки «Предложения».
  const [focusFamilyId, setFocusFamilyId] = useState<number | null>(null);

  function openFamily(familyId: number) {
    setFocusFamilyId(familyId);
    setTab("families");
  }

  return (
    <div className="container-page py-8">
      {/* Вкладки верхнего уровня — сегментный переключатель СПРАВА от
          заголовка (сверка с макетом 27.09.2026, `.head`/`.seg`), легенда
          источника подписи — ПОД заголовком слева (спека §2.1, §2.3).
          `PageHeader` — компонент всего приложения (засечки заголовка, шапка
          навигации) и здесь не правится: сегментный переключатель занимает
          его штатный слот `actions` (та же позиция, что действия любой
          другой страницы), а не собственная вёрстка заголовка. */}
      <Tabs value={tab} onValueChange={(v) => v && setTab(v)}>
        <PageHeader
          serif
          title="Семьи и контексты"
          subtitle="Семьи работ, семантика контекстов каталога и операции над ними"
          actions={
            <TabsList className="h-auto gap-1 rounded-lg bg-border-subtle p-1">
              <TabsTrigger value="families" className="rounded-md px-3.5 py-1.5 text-sm text-fg-secondary data-active:bg-background data-active:font-medium data-active:text-foreground data-active:shadow-sm">
                Семьи
              </TabsTrigger>
              <TabsTrigger value="contexts" className="rounded-md px-3.5 py-1.5 text-sm text-fg-secondary data-active:bg-background data-active:font-medium data-active:text-foreground data-active:shadow-sm">
                Контексты
              </TabsTrigger>
              <TabsTrigger value="suggestions" className="rounded-md px-3.5 py-1.5 text-sm text-fg-secondary data-active:bg-background data-active:font-medium data-active:text-foreground data-active:shadow-sm">
                Предложения
              </TabsTrigger>
            </TabsList>
          }
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

        <TabsContent value="families" className="mt-6">
          <FamiliesTab focusFamilyId={focusFamilyId} onFocusShown={() => setFocusFamilyId(null)} />
        </TabsContent>

        <TabsContent value="contexts" className="mt-6">
          <ContextsTab />
        </TabsContent>

        <TabsContent value="suggestions" className="mt-6">
          <SuggestionsTab onOpenFamily={openFamily} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
