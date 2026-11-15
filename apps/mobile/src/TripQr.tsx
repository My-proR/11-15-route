import { useEffect, useRef, useState } from "react";
import { Alert, Modal, View } from "react-native";
import QRCode from "react-native-qrcode-svg";
import { CameraView, useCameraPermissions } from "expo-camera";
import { useQuery } from "@tanstack/react-query";
import { api, errMsg } from "./api";
import { isTripQr } from "./credentials";
import { Btn, Txt, useTheme } from "./ui";

/** Driver: short-lived trip QR for passengers to scan. Refreshes every 45 s so screenshots go stale. */
export function TripQrSheet({ allocationId, visible, onClose }: { allocationId: string; visible: boolean; onClose: () => void }) {
  const t = useTheme();
  const q = useQuery({ queryKey: ["trip-qr", allocationId], enabled: visible, refetchInterval: 45_000, gcTime: 0, queryFn: () => api<{ token: string }>(`/allocations/${allocationId}/trip-qr`) });
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, justifyContent: "flex-end", backgroundColor: "#0008" }}>
        <View style={{ backgroundColor: t.bg, padding: 20, gap: 12, borderTopLeftRadius: 18, borderTopRightRadius: 18, alignItems: "center" }}>
          <Txt bold>Trip QR for passengers</Txt>
          {q.data ? <View style={{ backgroundColor: "#fff", padding: 12, borderRadius: 12 }} accessibilityLabel="Trip QR code"><QRCode value={q.data.token} size={240} /></View> : <Txt muted>{q.isError ? errMsg(q.error) : "Loading…"}</Txt>}
          <Txt muted>Passengers scan this in their app to board. It refreshes automatically. Passengers without the app can read you their 4-digit code.</Txt>
          <Btn kind="secondary" label="Close" onPress={onClose} />
        </View>
      </View>
    </Modal>
  );
}

/** Passenger: scan the driver's trip QR. The camera is opened only while this sheet is visible and is never used for anything else. */
export function ScanSheet({ visible, onClose, onToken, busy }: { visible: boolean; onClose: () => void; onToken: (token: string) => void; busy?: boolean }) {
  const t = useTheme();
  const [perm, ask] = useCameraPermissions();
  const handled = useRef(false);
  useEffect(() => { if (visible) handled.current = false; }, [visible]);
  useEffect(() => { if (visible && perm && !perm.granted && perm.canAskAgain) void ask(); }, [visible, perm, ask]);
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: t.bg, padding: 16, gap: 12, justifyContent: "center" }}>
        <Txt bold>Scan the driver's QR</Txt>
        {perm?.granted ? (
          <View style={{ height: 360, borderRadius: 14, overflow: "hidden" }}>
            <CameraView style={{ flex: 1 }} facing="back" barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
              onBarcodeScanned={busy ? undefined : ({ data }) => {
                if (handled.current) return;
                if (!isTripQr(data)) { handled.current = true; Alert.alert("Not a RoutePilot QR", "Scan the QR shown on your driver's phone.", [{ text: "OK", onPress: () => { handled.current = false; } }]); return; }
                handled.current = true; onToken(data);
              }} />
          </View>
        ) : <Txt muted>{perm && !perm.canAskAgain ? "Camera permission is off. Enable it in Settings, or show your 4-digit code to the driver." : "Camera permission is needed to scan. You can also show your 4-digit code to the driver."}</Txt>}
        <Btn kind="secondary" label="Cancel" onPress={onClose} />
      </View>
    </Modal>
  );
}
