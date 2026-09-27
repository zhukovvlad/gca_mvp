import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AllProviders } from "@/test/utils";

import { SOURCE_EXPLANATION } from "./labels";
import { SourceChip } from "./SourceChip";

/**
 * Плашки источника подписи (спека §2.3): «статья СМР» (классификатор,
 * `work_categories`) / «в смете» (раздел конкретной сметы). Пояснения
 * экспортированы отдельно (`SOURCE_EXPLANATION`), чтобы легенда экрана
 * переиспользовала ТОТ ЖЕ текст, а не свою копию.
 */
describe("SourceChip", () => {
  it('kind="classifier" печатает «статья СМР» и несёт пояснение классификатора', () => {
    render(<SourceChip kind="classifier" />, { wrapper: AllProviders });

    expect(screen.getByText("статья СМР")).toBeInTheDocument();
    expect(screen.getByTitle(SOURCE_EXPLANATION.classifier)).toBeInTheDocument();
    expect(SOURCE_EXPLANATION.classifier).toBe("классификатор, общий для всех смет");
  });

  it('kind="estimate" печатает «в смете» и несёт пояснение сметы', () => {
    render(<SourceChip kind="estimate" />, { wrapper: AllProviders });

    expect(screen.getByText("в смете")).toBeInTheDocument();
    expect(screen.getByTitle(SOURCE_EXPLANATION.estimate)).toBeInTheDocument();
    expect(SOURCE_EXPLANATION.estimate).toBe("разделы конкретной сметы");
  });

  // Сверка с макетом 27.09.2026 (`mockup.html`, `.src-c`/`.src-e`): чипы
  // «статья СМР» (фиолетовый/индиго) и «в смете» (жёлтый/янтарный) обязаны
  // нести РАЗНЫЕ классы заливки — иначе они схлопнутся в один и тот же вид,
  // а jsdom вычисленных цветов не видит (`docs/pitfalls/frontend.md`), только
  // класс.
  it("«статья СМР» и «в смете» несут РАЗНЫЕ классы заливки — не могут визуально совпасть", () => {
    render(<SourceChip kind="classifier" />, { wrapper: AllProviders });
    render(<SourceChip kind="estimate" />, { wrapper: AllProviders });

    const classifierBadge = screen.getByText("статья СМР");
    const estimateBadge = screen.getByText("в смете");
    expect(classifierBadge.className).not.toBe(estimateBadge.className);
  });

  // `title` выше — только атрибут; визуальную подсказку несёт `TooltipContent`,
  // и её текст появляется в DOM лишь по наведению (портал base-ui). Проверяем
  // жестом, а не наличием строки: без наведения текста подсказки в DOM нет.
  it("наведение показывает всплывающую подсказку с пояснением", async () => {
    const user = userEvent.setup();
    render(<SourceChip kind="estimate" />, { wrapper: AllProviders });

    expect(screen.queryByText(SOURCE_EXPLANATION.estimate)).not.toBeInTheDocument();
    await user.hover(screen.getByText("в смете"));
    const popup = await screen.findByText(SOURCE_EXPLANATION.estimate);
    expect(popup.closest('[data-slot="tooltip-content"]')).not.toBeNull();
  });
});
