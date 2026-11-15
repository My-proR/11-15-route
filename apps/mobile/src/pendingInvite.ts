import * as SecureStore from "expo-secure-store";

const KEY = "rp_pending_invite";
/** Remembers an invite token across the Google sign-in round trip. */
export const savePendingInvite = (t: string) => SecureStore.setItemAsync(KEY, t).catch(() => undefined);
export const readPendingInvite = () => SecureStore.getItemAsync(KEY).catch(() => null);
export const clearPendingInvite = () => SecureStore.deleteItemAsync(KEY).catch(() => undefined);
