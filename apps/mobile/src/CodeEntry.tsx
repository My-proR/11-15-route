import { useState } from "react";
import { Modal, TextInput, View } from "react-native";
import { digitsOnly } from "./credentials";
import { Btn, Txt, useTheme } from "./ui";

/** Bottom-sheet style numeric entry used for the trip start PIN (6) and passenger boarding code (4). */
export function CodeEntry({ visible, title, hint, length, busy, onCancel, onSubmit }: { visible: boolean; title: string; hint: string; length: number; busy?: boolean; onCancel: () => void; onSubmit: (v: string) => void }) {
  const t = useTheme();
  const [v, setV] = useState("");
  const close = () => { setV(""); onCancel(); };
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={close}>
      <View style={{ flex: 1, justifyContent: "flex-end", backgroundColor: "#0008" }}>
        <View style={{ backgroundColor: t.bg, padding: 20, gap: 12, borderTopLeftRadius: 18, borderTopRightRadius: 18 }}>
          <Txt bold>{title}</Txt>
          <Txt muted>{hint}</Txt>
          <TextInput accessibilityLabel={title} value={v} onChangeText={(x) => setV(digitsOnly(x, length))} keyboardType="number-pad" maxLength={length} autoFocus
            style={{ borderWidth: 1, borderColor: t.line, borderRadius: 10, padding: 12, fontSize: 28, letterSpacing: 8, color: t.text, backgroundColor: t.card, textAlign: "center" }} />
          <Btn label="Confirm" busy={busy} disabled={v.length !== length} onPress={() => { onSubmit(v); setV(""); }} />
          <Btn kind="secondary" label="Cancel" onPress={close} />
        </View>
      </View>
    </Modal>
  );
}
