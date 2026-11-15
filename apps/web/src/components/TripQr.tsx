import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import QRCode from "qrcode";
import { api } from "../lib/api";
import { useAction } from "../lib/hooks";
import { Button, Modal, Notice } from "./ui";

/** Driver: shows the short-lived trip QR. Refreshes before it expires so a screenshot goes stale. */
export function TripQrModal({ allocationId, open, onClose }: { allocationId: string; open: boolean; onClose: () => void }) {
  const q = useQuery({ queryKey: ["trip-qr", allocationId], enabled: open, refetchInterval: 45_000, gcTime: 0, queryFn: () => api<{ token: string; ttl_seconds: number }>(`/allocations/${allocationId}/trip-qr`) });
  const [img, setImg] = useState<string | null>(null);
  useEffect(() => { if (q.data?.token) void QRCode.toDataURL(q.data.token, { margin: 1, width: 280, errorCorrectionLevel: "M" }).then(setImg); }, [q.data?.token]);
  return (
    <Modal open={open} onClose={onClose} title="Trip QR for passengers">
      <div className="space-y-3 text-center">
        {q.isError ? <Notice tone="bad">Couldn't load the QR. Check the trip is in progress.</Notice> : img ? <img src={img} alt="Trip QR code" width={280} height={280} className="mx-auto rounded-lg border border-line bg-white p-2" /> : <p className="text-sm text-muted">Loading…</p>}
        <p className="text-sm text-muted">Passengers scan this in their RoutePilot app to board. It refreshes automatically about every 45 seconds. Passengers without a phone camera can give you their 4-digit code instead.</p>
        <Button variant="secondary" onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}

interface Detector { detect(src: CanvasImageSource): Promise<{ rawValue: string }[]> }
type DetectorCtor = new (o: { formats: string[] }) => Detector;

/** Passenger: scan the driver's trip QR with the device camera (needs the browser's BarcodeDetector; otherwise use the 4-digit code). */
export function ScanToBoard({ allocationId }: { allocationId: string }) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const supported = typeof window !== "undefined" && "BarcodeDetector" in window && Boolean(navigator.mediaDevices?.getUserMedia);
  const board = useAction((token: string) => api(`/me/trips/${allocationId}/board-by-qr`, { method: "POST", body: { token } }), { success: "You're boarded. Have a safe trip!", invalidate: [["boarding-code", allocationId], ["my-trips"]], onSuccess: () => { setDone(true); setOpen(false); } });
  useEffect(() => {
    if (!open || !supported) return;
    let stop = false; let stream: MediaStream | undefined; let timer: number | undefined;
    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (video.current) { video.current.srcObject = stream; await video.current.play(); }
        const det = new (window as unknown as { BarcodeDetector: DetectorCtor }).BarcodeDetector({ formats: ["qr_code"] });
        const tick = async () => {
          if (stop || !video.current) return;
          try { const found = await det.detect(video.current); const v = found[0]?.rawValue; if (v && v.startsWith("rp1.")) { stop = true; board.mutate(v); return; } } catch { /* keep scanning */ }
          timer = window.setTimeout(() => void tick(), 400);
        };
        void tick();
      } catch { setOpen(false); }
    })();
    return () => { stop = true; if (timer) clearTimeout(timer); stream?.getTracks().forEach((t) => t.stop()); };
  }, [open, supported]); // eslint-disable-line react-hooks/exhaustive-deps
  if (done) return null;
  if (!supported) return <p className="mt-2 text-xs text-muted">Scanning isn't supported in this browser. Show your 4-digit code to the driver, or use the mobile app to scan.</p>;
  return (
    <>
      <Button className="mt-2" variant="secondary" onClick={() => setOpen(true)}>Scan trip QR to board</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Scan the driver's QR"><div className="space-y-3"><video ref={video} muted playsInline className="w-full rounded-lg bg-black" /><p className="text-sm text-muted">Point your camera at the QR code on the driver's phone.</p></div></Modal>
    </>
  );
}
