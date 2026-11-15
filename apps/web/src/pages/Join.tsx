import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigation } from "lucide-react";
import { api, ApiError, errMsg } from "../lib/api";
import { useSession } from "../lib/session";
import { useT } from "../lib/i18n";
import { Button, Notice, Spinner } from "../components/ui";
import GoogleButton from "../components/GoogleButton";
import LanguageSwitch from "../components/LanguageSwitch";

interface Preview { organization: string; role: "driver" | "employee" | "dispatcher"; first_name: string | null }

/** Landing page for an invite link sent on WhatsApp: sign in with Google, then the account is linked automatically. */
export default function Join() {
  const { token = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const t = useT();
  const session = useSession((s) => s.token);
  const ready = useSession((s) => s.ready);
  const signOut = useSession((s) => s.signOut);
  const preview = useQuery({ queryKey: ["invite-preview", token], queryFn: () => api<Preview>("/invites/preview", { query: { token } }), retry: false });
  const [state, setState] = useState<"idle" | "working" | "done" | "error">("idle");
  const [err, setErr] = useState<ApiError | Error | null>(null);
  const started = useRef(false);

  const accept = async () => {
    setState("working"); setErr(null);
    try {
      await api("/invites/accept", { method: "POST", body: { token } });
      setState("done"); qc.clear();
      setTimeout(() => nav("/", { replace: true }), 600);
    } catch (e) { setErr(e as Error); setState("error"); }
  };
  useEffect(() => {
    if (ready && session && preview.isSuccess && !started.current) { started.current = true; void accept(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, session, preview.isSuccess]);

  const invalid = preview.isError && !(session && state !== "idle");
  return (
    <main className="grid min-h-full place-items-center p-4">
      <div className="w-full max-w-sm space-y-5 rounded-2xl border border-line bg-surface p-6">
        <div className="flex items-center gap-2 font-semibold"><span className="grid size-8 place-items-center rounded-lg bg-accent text-accentfg"><Navigation className="size-4" /></span><span className="text-lg">RoutePilot</span></div>
        {!ready || preview.isPending ? <Spinner /> : invalid ? <Notice tone="bad">{t("joinInvalid")}</Notice> : (
          <>
            <div><h1 className="text-xl font-semibold">{t("joinTitle")}{preview.data?.first_name ? `, ${preview.data.first_name}` : ""}</h1>
              {preview.data && <p className="mt-1 text-sm text-muted">{t("joinBody", { org: preview.data.organization, role: t(`role_${preview.data.role}` as const) })}</p>}</div>
            {!session && <><p className="text-sm">{t("joinSignInFirst")}</p><GoogleButton returnPath={`/join/${token}`} /><p className="text-center text-xs text-muted">{t("stayIn")}</p></>}
            {session && state === "working" && <Spinner label={t("joinSetup")} />}
            {state === "done" && <Notice tone="ok">{t("joinDone")}</Notice>}
            {state === "error" && (
              <div className="space-y-3"><Notice tone="bad">{err instanceof ApiError && err.code === "invite_invalid" ? t("joinInvalid") : errMsg(err)}</Notice>
                <div className="flex gap-2"><Button variant="secondary" onClick={() => { started.current = false; void accept(); }}>{t("joinTryAgain")}</Button><Button variant="ghost" onClick={() => void signOut()}>{t("signOut")}</Button></div></div>)}
          </>)}
        <div className="flex justify-center"><LanguageSwitch /></div>
      </div>
    </main>
  );
}
