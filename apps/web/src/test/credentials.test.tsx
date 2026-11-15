import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BoardingCodeModal } from "../components/TripCredentials";

describe("BoardingCodeModal", () => {
  it("requires exactly 4 digits", async () => {
    const onSubmit = vi.fn();
    render(<BoardingCodeModal name="Asha" open onClose={() => {}} onSubmit={onSubmit} busy={false} />);
    const btn = screen.getByRole("button", { name: "Mark boarded" });
    const input = screen.getByLabelText("Boarding code");
    await userEvent.type(input, "123");
    expect(btn).toBeDisabled();
    await userEvent.type(input, "4");
    await userEvent.click(btn);
    expect(onSubmit).toHaveBeenCalledWith("1234");
  });
});
