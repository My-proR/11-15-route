import { useEffect, useState } from "react";
import { Smartphone, X } from "lucide-react";
import { Button } from "./ui";
import { useT } from "../lib/i18n";
import { isIosSafari, isStandalone } from "../lib/pwa";

interface BeforeInstall extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> }
const KEY = "rp_install_dismissed";
const DAYS = 14;
const dismissedRecently = () => { try { const v = Number(localStorage.getItem(KEY)); return v > 0 && Date.now() - v < DAYS * 864e5; } catch { return false; } };

/** One-tap "Install" banner (Android/Chrome) or the Add-to-Home-Screen hint (iPhone). Hidden once installed or dismissed. */
export default function InstallPrompt() {
  const t = useT();
  const [evt, setEvt] = useState<BeforeInstall | null>(null);
  const [hidden, setHidden] = useState(() => isStandalone() || dismissedRecently());
  useEffect(() => {
    const on = (e: Event) => { e.preventDefault(); setEvt(e as BeforeInstall); };
    const done = () => setHidden(true);
    window.addEventListener("beforeinstallprompt", on);
    window.addEventListener("appinstalled", done);
    return () => { window.removeEventListener("beforeinstallprompt", on); window.removeEventListener("appinstalled", done); };
  }, []);
  const ios = isIosSafari();
  if (hidden || (!evt && !ios)) return null;
  const dismiss = () => { try { localStorage.setItem(KEY, String(Date.now())); } catch { /* storage unavailable */ } setHidden(true); };
  return (
    <div role="dialog" aria-label={t("installTitle")} className="fixed inset-x-3 bottom-3 z-50 mx-auto flex max-w-md items-center gap-3 rounded-2xl border border-line bg-surface p-3 shadow-lg">
      <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent text-accentfg"><Smartphone className="size-5" /></span>
      <div className="min-w-0 flex-1"><p className="text-sm font-semibold">{t("installTitle")}</p><p className="text-xs text-muted">{evt ? t("installBody") : t("iosInstall")}</p></div>
      {evt && <Button onClick={async () => { await evt.prompt(); const c = await evt.userChoice; if (c.outcome === "accepted") setHidden(true); else dismiss(); setEvt(null); }}>{t("install")}</Button>}
      <button aria-label={t("notNow")} className="grid size-9 place-items-center rounded-lg hover:bg-surface2" onClick={dismiss}><X className="size-4" /></button>
    </div>
  );
}
