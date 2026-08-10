import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";

import { TopNav } from "./TopNav";
import { renderWithProviders } from "@/test/utils";

/**
 * Навигация верхнего меню (Ф6 фазы 7, задача 11, §2.13).
 *
 * До этого файла ни один тест не рендерил `AppShell`/`TopNav` целиком: снятие
 * трёх пунктов (Матрица, Отчёты, Настройки) из `NAV` не завалило бы ни одной
 * существующей проверки, и требование §2.13 осталось бы без исполнителя.
 *
 * Второй тест ниже — не дублирование первого, а его необходимое дополнение:
 * первый тест прошёл бы и на ПУСТОЙ навигации (там тоже нет «Матрица»,
 * «Отчёты», «Настройки»), поэтому ожидание «эти пункты остались» не выводимо
 * из того, что ломает счётчик-пример первого теста, — его нужно проверять
 * отдельно и явно.
 */
describe("TopNav", () => {
  it("в навигации нет «Матрица», «Отчёты», «Настройки»", () => {
    renderWithProviders(<TopNav />);

    expect(screen.queryByText("Матрица")).not.toBeInTheDocument();
    expect(screen.queryByText("Отчёты")).not.toBeInTheDocument();
    expect(screen.queryByText("Настройки")).not.toBeInTheDocument();
  });

  it("в навигации есть «Главная», «Договоры», «Ручной матчинг», «Нормативы», «Пользователи»", () => {
    // Нормативы и Пользователи — adminOnly: видны благодаря дефолтному
    // тестовому пользователю (admin) из renderWithProviders.
    renderWithProviders(<TopNav />);

    expect(screen.getByText("Главная")).toBeInTheDocument();
    expect(screen.getByText("Договоры")).toBeInTheDocument();
    expect(screen.getByText("Ручной матчинг")).toBeInTheDocument();
    expect(screen.getByText("Нормативы")).toBeInTheDocument();
    expect(screen.getByText("Пользователи")).toBeInTheDocument();
  });
});
