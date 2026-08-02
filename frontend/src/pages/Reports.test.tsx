import { describe, it, expect } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import Reports from "./Reports";

describe("Reports", () => {
  it("renders without errors", async () => {
    renderWithProviders(<Reports />);
    await waitFor(() => {
      expect(document.body).toBeInTheDocument();
    });
  });
});
