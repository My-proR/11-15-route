import * as Notifications from "expo-notifications";
import Constants from "expo-constants";
import { Platform } from "react-native";
import { api } from "./api";

Notifications.setNotificationHandler({ handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }) });

/** Registers this device for push. Silent no-op when permission is refused or no EAS project id is configured. */
export async function registerForPush(): Promise<void> {
  try {
    if (Platform.OS === "android") await Notifications.setNotificationChannelAsync("default", { name: "Trips & alerts", importance: Notifications.AndroidImportance.HIGH });
    const cur = await Notifications.getPermissionsAsync();
    const granted = cur.granted || (await Notifications.requestPermissionsAsync()).granted;
    if (!granted) return;
    const projectId = (Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined)?.eas?.projectId ?? Constants.easConfig?.projectId;
    if (!projectId) return;
    const { data } = await Notifications.getExpoPushTokenAsync({ projectId });
    await api("/notifications/push-subscriptions", { method: "POST", body: { kind: "expo", token: data } });
  } catch { /* push is best-effort; in-app notifications still work */ }
}
