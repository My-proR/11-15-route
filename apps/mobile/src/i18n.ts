import * as SecureStore from "expo-secure-store";
import { create } from "zustand";

export type Lang = "en" | "ta";
const KEY = "rp_lang";

const en = {
  language: "Language", tabTrips: "Trips", tabSos: "SOS", tabAlerts: "Alerts", tabAccount: "Account",
  signIn: "Sign in", signOut: "Sign out", email: "Email", password: "Password",
  google: "Continue with Google", stayIn: "You will stay signed in on this phone.", orEmail: "Or use email and password",
  loginHelp: "Sign in with Google, or with the account your administrator created for you.",
  googleFailed: "Google sign-in did not complete. Please try again.",
  joinTitle: "You are invited", joinBody: "{org} invited you to join as {role}.", joinSetup: "Setting up your account…", joinDone: "All set! Opening your app…",
  joinSignInFirst: "Sign in with Google to accept this invitation.", joinInvalid: "This invite link is not valid. Ask your admin for a new one.", joinTryAgain: "Try again",
  pasteInvite: "Have an invite link? Paste it here", pasteInviteGo: "Open invite",
  role_driver: "driver", role_employee: "employee", role_dispatcher: "dispatcher",
  paused: "Access for your organization is paused. Please contact your organization admin.",
  pausedTitle: "Access paused", refresh: "Check again",
  webOnly: "Dispatch and administration are done in the web console. This app is for drivers and employees.",
  noTripsDriver: "No trips assigned yet.", noTripsEmployee: "No confirmed trips yet. You'll get a notification when one is confirmed.",
  availOn: "You are available for trips", availOff: "You are off duty", availOnHint: "You can be assigned new trips.", availOffHint: "You will not be assigned new trips.",
  goOff: "Go off duty", goAvail: "Go available",
  accept: "Accept", decline: "Decline", acceptPrompt: "Please accept or decline this trip.", startTrip: "Start trip", completeTrip: "Complete trip", showQr: "Show trip QR", scanQr: "Scan trip QR to board",
  boardCode: "Show this code to your driver to board.", boarded: "You are marked as boarded.",
  myRequests: "My ride requests", reqPickup: "Request a ride for tomorrow (home → office)", reqDrop: "Request tomorrow's drop (office → home)",
  caughtUp: "You're all caught up.", markRead: "Mark read", organization: "Organization", version: "App version",
  couldNot: "Couldn't complete that",
} as const;
export type Key = keyof typeof en;

// Tamil strings should be reviewed by a native speaker before wide release (see docs/KNOWN_LIMITATIONS.md).
const ta: Record<Key, string> = {
  language: "மொழி", tabTrips: "பயணங்கள்", tabSos: "அவசரம்", tabAlerts: "அறிவிப்புகள்", tabAccount: "கணக்கு",
  signIn: "உள்நுழை", signOut: "வெளியேறு", email: "மின்னஞ்சல்", password: "கடவுச்சொல்",
  google: "Google மூலம் தொடரவும்", stayIn: "இந்த தொலைபேசியில் நீங்கள் உள்நுழைந்தே இருப்பீர்கள்.", orEmail: "அல்லது மின்னஞ்சல் மற்றும் கடவுச்சொல் பயன்படுத்தவும்",
  loginHelp: "Google மூலம் உள்நுழையுங்கள், அல்லது உங்கள் நிர்வாகி உருவாக்கிய கணக்கைப் பயன்படுத்துங்கள்.",
  googleFailed: "Google உள்நுழைவு முடியவில்லை. மீண்டும் முயற்சிக்கவும்.",
  joinTitle: "உங்களுக்கு அழைப்பு வந்துள்ளது", joinBody: "{org} உங்களை {role} ஆகச் சேர அழைத்துள்ளது.", joinSetup: "உங்கள் கணக்கை அமைக்கிறோம்…", joinDone: "தயார்! உங்கள் செயலியைத் திறக்கிறோம்…",
  joinSignInFirst: "இந்த அழைப்பை ஏற்க Google மூலம் உள்நுழையுங்கள்.", joinInvalid: "இந்த அழைப்பு இணைப்பு செல்லாது. உங்கள் நிர்வாகியிடம் புதிய இணைப்பு கேளுங்கள்.", joinTryAgain: "மீண்டும் முயற்சி",
  pasteInvite: "அழைப்பு இணைப்பு உள்ளதா? இங்கே ஒட்டவும்", pasteInviteGo: "அழைப்பைத் திற",
  role_driver: "ஓட்டுநர்", role_employee: "பணியாளர்", role_dispatcher: "அனுப்புநர்",
  paused: "உங்கள் நிறுவனத்துக்கான அணுகல் நிறுத்தி வைக்கப்பட்டுள்ளது. உங்கள் நிறுவன நிர்வாகியைத் தொடர்பு கொள்ளவும்.",
  pausedTitle: "அணுகல் நிறுத்தம்", refresh: "மீண்டும் பார்",
  webOnly: "அனுப்புதல் மற்றும் நிர்வாகம் வலை கன்சோலில் செய்யப்படும். இந்தச் செயலி ஓட்டுநர்கள் மற்றும் பணியாளர்களுக்கானது.",
  noTripsDriver: "இன்னும் பயணங்கள் ஒதுக்கப்படவில்லை.", noTripsEmployee: "உறுதி செய்யப்பட்ட பயணங்கள் இல்லை. உறுதியானதும் அறிவிப்பு வரும்.",
  availOn: "நீங்கள் பயணங்களுக்குக் கிடைக்கிறீர்கள்", availOff: "நீங்கள் பணியில் இல்லை", availOnHint: "புதிய பயணங்கள் ஒதுக்கப்படலாம்.", availOffHint: "புதிய பயணங்கள் ஒதுக்கப்பட மாட்டாது.",
  goOff: "பணியிலிருந்து விலகு", goAvail: "கிடைக்கிறேன்",
  accept: "ஏற்கிறேன்", decline: "மறுக்கிறேன்", acceptPrompt: "இந்தப் பயணத்தை ஏற்கவும் அல்லது மறுக்கவும்.", startTrip: "பயணத்தைத் தொடங்கு", completeTrip: "பயணத்தை முடி", showQr: "பயண QR காட்டு", scanQr: "ஏற QR ஸ்கேன் செய்",
  boardCode: "ஏறுவதற்கு இந்தக் குறியீட்டை ஓட்டுநரிடம் காட்டுங்கள்.", boarded: "நீங்கள் ஏறியதாகப் பதிவாகியுள்ளது.",
  myRequests: "என் பயணக் கோரிக்கைகள்", reqPickup: "நாளைக்கு பயணம் கோரு (வீடு → அலுவலகம்)", reqDrop: "நாளை திரும்பும் பயணம் கோரு (அலுவலகம் → வீடு)",
  caughtUp: "எல்லாம் பார்த்துவிட்டீர்கள்.", markRead: "படித்ததாக குறி", organization: "நிறுவனம்", version: "செயலி பதிப்பு",
  couldNot: "அதைச் செய்ய முடியவில்லை",
};
const dict = { en, ta } as const;

const guess = (): Lang => { try { return Intl.DateTimeFormat().resolvedOptions().locale.toLowerCase().startsWith("ta") ? "ta" : "en"; } catch { return "en"; } };

interface L { lang: Lang; explicit: boolean; load: () => Promise<void>; setLang: (l: Lang) => void; adopt: (l?: string | null) => void }
export const useLang = create<L>((set, get) => ({
  lang: guess(), explicit: false,
  async load() { try { const v = await SecureStore.getItemAsync(KEY); if (v === "en" || v === "ta") set({ lang: v, explicit: true }); } catch { /* keystore unavailable: keep device default */ } },
  setLang(l) { void SecureStore.setItemAsync(KEY, l).catch(() => undefined); set({ lang: l, explicit: true }); },
  /** Use the language on the server profile unless this device already has an explicit choice. */
  adopt(l) { if ((l === "en" || l === "ta") && !get().explicit) set({ lang: l }); },
}));

export function translate(lang: Lang, key: Key, vars?: Record<string, string>): string {
  let s: string = dict[lang][key] ?? en[key];
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(v);
  return s;
}
export function useT() { const lang = useLang((s) => s.lang); return (key: Key, vars?: Record<string, string>) => translate(lang, key, vars); }
