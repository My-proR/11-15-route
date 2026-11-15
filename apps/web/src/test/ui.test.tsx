import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeatureNotice } from "../components/FeatureNotice";
import { Confirm, DataTable, StatusBadge } from "../components/ui";

describe("feature notice", () => {
  const base = { deployment_enabled: false, platform_enabled: false, organization_enabled: false, effective: false };
  it("explains why a feature is off", () => {
    render(<FeatureNotice name="Live tracking" state={{ ...base, blocked_by: "deployment" }} />);
    expect(screen.getByText(/live tracking is off/i)).toBeInTheDocument();
    expect(screen.getByText(/whole deployment/i)).toBeInTheDocument();
  });
  it("renders nothing when effective", () => {
    const { container } = render(<FeatureNotice name="X" state={{ ...base, effective: true, blocked_by: null }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ui primitives", () => {
  it("DataTable renders rows and handles clicks", async () => {
    const onClick = vi.fn();
    render(<DataTable caption="t" rows={[{ id: "1", name: "Alpha" }]} columns={[{ key: "name", header: "Name" }]} onRowClick={onClick} />);
    await userEvent.click(screen.getByText("Alpha"));
    expect(onClick).toHaveBeenCalledWith({ id: "1", name: "Alpha" });
  });
  it("StatusBadge humanises states", () => {
    render(<StatusBadge status="REQUIRES_REASSIGNMENT" />);
    expect(screen.getByText("requires reassignment")).toBeInTheDocument();
  });
  it("Confirm calls back only on confirm", async () => {
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.removeAttribute("open"); };
    const ok = vi.fn(), close = vi.fn();
    render(<Confirm open title="Sure?" message="msg" confirmLabel="Do it" onConfirm={ok} onClose={close} />);
    await userEvent.click(screen.getByRole("button", { name: "Do it" }));
    expect(ok).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(close).toHaveBeenCalled();
  });
});
