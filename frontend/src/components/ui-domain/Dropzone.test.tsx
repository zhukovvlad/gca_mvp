import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { Dropzone } from "./Dropzone";

describe("Dropzone", () => {
  it("renders the prompt and hint text", () => {
    renderWithProviders(<Dropzone onDrop={() => {}} />);
    expect(
      screen.getByText(/Перетащите файлы сюда или нажмите для выбора/)
    ).toBeInTheDocument();
    expect(screen.getByText(/PDF, JPG, PNG до 20 МБ/)).toBeInTheDocument();
  });

  it("calls onDrop when a PDF file is selected", async () => {
    const onDrop = vi.fn();
    const user = userEvent.setup();
    const { container } = renderWithProviders(
      <Dropzone onDrop={onDrop} accept={{ "application/pdf": [".pdf"] }} />
    );
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    const file = new File(["dummy"], "test.pdf", { type: "application/pdf" });
    await user.upload(input, file);
    expect(onDrop).toHaveBeenCalledTimes(1);
    expect(onDrop.mock.calls[0][0][0].name).toBe("test.pdf");
  });

  it("respects custom hint", () => {
    renderWithProviders(<Dropzone onDrop={() => {}} hint="Только .pdf" />);
    expect(screen.getByText("Только .pdf")).toBeInTheDocument();
  });

  it("becomes non-interactive when disabled", () => {
    const { container } = renderWithProviders(<Dropzone onDrop={() => {}} disabled />);
    // root Dropzone — это родитель file input. Использовать container.firstChild
    // нельзя: это wrapper от ThemeProvider/QueryClient/Router.
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    const dropzoneRoot = input.parentElement!;
    expect(dropzoneRoot).toHaveClass("cursor-not-allowed");
  });
});
