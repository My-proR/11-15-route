import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", () => ({ api: vi.fn(async () => ({ token: "rp1.abc.def", ttl_seconds: 60 })), errMsg: (e: unknown) => String(e), newKey: () => "k" }));
vi.mock("qrcode", () => ({ default: { toDataURL: vi.fn(async (t: string) => `data:image/png;base64,${btoa(t)}`) } }));
import { TripQrModal } from "../components/TripQr";

describe("TripQrModal", () => {
  it("renders a QR image for the token and explains refresh", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><TripQrModal allocationId="a1" open onClose={() => {}} /></QueryClientProvider>);
    const img = await screen.findByAltText("Trip QR code");
    expect(img.getAttribute("src")).toContain(btoa("rp1.abc.def"));
    expect(screen.getByText(/refreshes automatically/i)).toBeInTheDocument();
  });
});
