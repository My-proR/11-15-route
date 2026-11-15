import { useLang, useT, type Lang } from "../lib/i18n";
import { api } from "../lib/api";
import { useSession } from "../lib/session";

/** English / தமிழ் toggle. When signed in the choice is also saved on the profile (best effort) so other devices follow. */
export default function LanguageSwitch() {
  const lang = useLang((s) => s.lang);
  const setLang = useLang((s) => s.setLang);
  const token = useSession((s) => s.token);
  const t = useT();
  const pick = (l: Lang) => { setLang(l); if (token) void api("/me/language", { method: "PUT", body: { language: l } }).catch(() => undefined); };
  return (
    <div role="group" aria-label={t("language")} className="inline-flex overflow-hidden rounded-lg border border-line text-sm">
      {(["en", "ta"] as const).map((l) => (
        <button key={l} type="button" aria-pressed={lang === l} onClick={() => pick(l)} className={`min-h-9 px-3 ${lang === l ? "bg-accent text-accentfg" : "bg-surface hover:bg-surface2"}`}>{l === "en" ? "English" : "தமிழ்"}</button>
      ))}
    </div>
  );
}
