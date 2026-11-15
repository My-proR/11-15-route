import { create } from "zustand";

export type Lang = "en" | "ta";
const KEY = "rp_lang";
const read = (): Lang | null => { try { const v = localStorage.getItem(KEY); return v === "en" || v === "ta" ? v : null; } catch { return null; } };
const guess = (): Lang => (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("ta") ? "ta" : "en");

const en = {
  language: "Language",
  signIn: "Sign in", signOut: "Sign out", email: "Email", password: "Password",
  google: "Continue with Google", stayIn: "You will stay signed in on this phone.", orEmail: "Or use email and password",
  loginHelp: "Sign in with Google, or with the account your administrator created for you.",
  joinTitle: "You are invited", joinBody: "{org} invited you to join as {role}.", joinSetup: "Setting up your account…", joinDone: "All set! Opening your app…",
  joinSignInFirst: "Sign in with Google to accept this invitation.", joinInvalid: "This invite link is not valid. Ask your admin for a new one.", joinTryAgain: "Try again",
  notSetUp: "Your account is not set up yet. Ask your admin to send you an invite link.",
  role_driver: "driver", role_employee: "employee", role_dispatcher: "dispatcher",
  myTransport: "My transport", requestRide: "Request a ride", sos: "Emergency SOS", upcoming: "Upcoming confirmed trips", noTrips: "No confirmed trips yet",
  noTripsHint: "You will be notified as soon as your trip is confirmed.", myRequests: "My requests", cancel: "Cancel", submit: "Submit", date: "Date",
  pickup: "Home → office", drop: "Office → home",
  myTrips: "My trips", accept: "Accept", decline: "Decline", startTrip: "Start trip", completeTrip: "Complete trip", showQr: "Show trip QR",
  noAssigned: "No trips assigned", noAssignedHint: "New offers appear here and as notifications.",
  availOn: "You are available for trips", availOff: "You are off duty", availOnHint: "You can be assigned new trips.", availOffHint: "You will not be assigned new trips.",
  goOff: "Go off duty", goAvail: "Go available",
  boardCode: "Show this code to your driver to board.", boardCodeSafe: "Only share it with the driver of your cab, in person.", boarded: "You are marked as boarded.",
  installTitle: "Install RoutePilot on your phone", installBody: "Open it like an app, with one tap.", install: "Install", notNow: "Not now",
  iosInstall: "To install: tap Share, then “Add to Home Screen”.",
  paused: "Access for your organization is paused. Please contact your organization admin.",
  shareWhatsapp: "Share on WhatsApp", copyLink: "Copy link", copied: "Link copied",
} as const;
export type Key = keyof typeof en;

// Tamil strings should be reviewed by a native speaker before wide release (see docs/KNOWN_LIMITATIONS.md).
const ta: Record<Key, string> = {
  language: "மொழி",
  signIn: "உள்நுழை", signOut: "வெளியேறு", email: "மின்னஞ்சல்", password: "கடவுச்சொல்",
  google: "Google மூலம் தொடரவும்", stayIn: "இந்த தொலைபேசியில் நீங்கள் உள்நுழைந்தே இருப்பீர்கள்.", orEmail: "அல்லது மின்னஞ்சல் மற்றும் கடவுச்சொல் பயன்படுத்தவும்",
  loginHelp: "Google மூலம் உள்நுழையுங்கள், அல்லது உங்கள் நிர்வாகி உருவாக்கிய கணக்கைப் பயன்படுத்துங்கள்.",
  joinTitle: "உங்களுக்கு அழைப்பு வந்துள்ளது", joinBody: "{org} உங்களை {role} ஆகச் சேர அழைத்துள்ளது.", joinSetup: "உங்கள் கணக்கை அமைக்கிறோம்…", joinDone: "தயார்! உங்கள் செயலியைத் திறக்கிறோம்…",
  joinSignInFirst: "இந்த அழைப்பை ஏற்க Google மூலம் உள்நுழையுங்கள்.", joinInvalid: "இந்த அழைப்பு இணைப்பு செல்லாது. உங்கள் நிர்வாகியிடம் புதிய இணைப்பு கேளுங்கள்.", joinTryAgain: "மீண்டும் முயற்சி",
  notSetUp: "உங்கள் கணக்கு இன்னும் அமைக்கப்படவில்லை. அழைப்பு இணைப்பை அனுப்புமாறு உங்கள் நிர்வாகியிடம் கேளுங்கள்.",
  role_driver: "ஓட்டுநர்", role_employee: "ஊழியர்", role_dispatcher: "டிஸ்பாட்சர்",
  myTransport: "என் போக்குவரத்து", requestRide: "பயணம் கோருங்கள்", sos: "அவசர SOS", upcoming: "உறுதிசெய்யப்பட்ட வரவிருக்கும் பயணங்கள்", noTrips: "உறுதிசெய்யப்பட்ட பயணங்கள் இல்லை",
  noTripsHint: "உங்கள் பயணம் உறுதியானவுடன் தெரிவிப்போம்.", myRequests: "என் கோரிக்கைகள்", cancel: "ரத்து செய்", submit: "சமர்ப்பி", date: "தேதி",
  pickup: "வீடு → அலுவலகம்", drop: "அலுவலகம் → வீடு",
  myTrips: "என் பயணங்கள்", accept: "ஏற்கிறேன்", decline: "மறுக்கிறேன்", startTrip: "பயணத்தைத் தொடங்கு", completeTrip: "பயணத்தை முடி", showQr: "பயண QR காட்டு",
  noAssigned: "பயணங்கள் ஒதுக்கப்படவில்லை", noAssignedHint: "புதிய பயண வாய்ப்புகள் இங்கும் அறிவிப்புகளிலும் தோன்றும்.",
  availOn: "நீங்கள் பயணங்களுக்குக் கிடைக்கிறீர்கள்", availOff: "நீங்கள் பணியில் இல்லை", availOnHint: "உங்களுக்குப் புதிய பயணங்கள் ஒதுக்கப்படலாம்.", availOffHint: "உங்களுக்குப் புதிய பயணங்கள் ஒதுக்கப்படாது.",
  goOff: "பணியை நிறுத்து", goAvail: "கிடைக்கிறேன்",
  boardCode: "ஏற இந்தக் குறியீட்டை ஓட்டுநரிடம் காட்டுங்கள்.", boardCodeSafe: "உங்கள் வண்டியின் ஓட்டுநரிடம் மட்டும், நேரில் பகிருங்கள்.", boarded: "நீங்கள் ஏறியதாகக் குறிக்கப்பட்டுள்ளது.",
  installTitle: "RoutePilot-ஐ உங்கள் தொலைபேசியில் நிறுவுங்கள்", installBody: "ஒரே தொடுதலில் செயலியாகத் திறக்கலாம்.", install: "நிறுவு", notNow: "பிறகு",
  iosInstall: "நிறுவ: Share ஐத் தொட்டு, “Add to Home Screen” ஐத் தேர்ந்தெடுக்கவும்.",
  paused: "உங்கள் நிறுவனத்திற்கான அணுகல் இடைநிறுத்தப்பட்டுள்ளது. உங்கள் நிறுவன நிர்வாகியைத் தொடர்புகொள்ளுங்கள்.",
  shareWhatsapp: "WhatsApp-இல் பகிர்", copyLink: "இணைப்பை நகலெடு", copied: "இணைப்பு நகலெடுக்கப்பட்டது",
};
const dict: Record<Lang, Record<Key, string>> = { en, ta };

interface LangState { lang: Lang; setLang: (l: Lang) => void; adopt: (l: Lang | null | undefined) => void }
export const useLang = create<LangState>((set) => ({
  lang: read() ?? guess(),
  setLang(l) { try { localStorage.setItem(KEY, l); } catch { /* storage unavailable */ } document.documentElement.lang = l; set({ lang: l }); },
  /** Use the language saved on the server profile unless this device already has an explicit choice. */
  adopt(l) { if (l && read() === null) { document.documentElement.lang = l; set({ lang: l }); } },
}));

export function translate(lang: Lang, key: Key, vars?: Record<string, string>): string {
  let s = dict[lang][key] ?? en[key];
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}
export function useT() {
  const lang = useLang((s) => s.lang);
  return (key: Key, vars?: Record<string, string>) => translate(lang, key, vars);
}
