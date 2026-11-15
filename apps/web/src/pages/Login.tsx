import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Navigation } from "lucide-react";
import { Button, Field, Input, Notice, Textarea } from "../components/ui";
import { useSession } from "../lib/session";
import { supabaseConfigured } from "../lib/supabase";
import { errMsg } from "../lib/api";
import { useT } from "../lib/i18n";
import LanguageSwitch from "../components/LanguageSwitch";
import GoogleButton from "../components/GoogleButton";

const schema = z.object({ email: z.string().email("Enter a valid email"), password: z.string().min(1, "Enter your password") });
type Form = z.infer<typeof schema>;

export default function Login() {
  const signIn = useSession((s) => s.signIn);
  const devSign = useSession((s) => s.signInWithDevToken);
  const [error, setError] = useState<string | null>(null);
  const [devToken, setDevToken] = useState("");
  const t = useT();
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<Form>({ resolver: zodResolver(schema) });
  return (
    <main className="grid min-h-full place-items-center p-4">
      <div className="w-full max-w-sm space-y-5 rounded-2xl border border-line bg-surface p-6">
        <div className="flex items-center gap-2 font-semibold"><span className="grid size-8 place-items-center rounded-lg bg-accent text-accentfg"><Navigation className="size-4" /></span><span className="text-lg">RoutePilot</span></div>
        <div className="flex items-center justify-between gap-2"><p className="text-sm text-muted">{t("loginHelp")}</p></div>
        <GoogleButton />
        <p className="text-center text-xs text-muted">{t("stayIn")}</p>
        {!supabaseConfigured && <Notice tone="warn">Authentication is not configured for this build (missing Supabase URL / anon key).</Notice>}
        <details className="text-sm"><summary className="cursor-pointer text-muted">{t("orEmail")}</summary>
        <form className="mt-3 space-y-4" onSubmit={handleSubmit(async (v) => { setError(null); try { await signIn(v.email, v.password); } catch (e) { setError(errMsg(e)); } })} noValidate>
          <Field label={t("email")} error={errors.email?.message}>{(id) => <Input id={id} type="email" autoComplete="username" {...register("email")} />}</Field>
          <Field label={t("password")} error={errors.password?.message}>{(id) => <Input id={id} type="password" autoComplete="current-password" {...register("password")} />}</Field>
          {error && <p role="alert" className="text-sm text-bad">{error}</p>}
          <Button type="submit" className="w-full" busy={isSubmitting} disabled={!supabaseConfigured}>{t("signIn")}</Button>
        </form></details>
        <div className="flex justify-center"><LanguageSwitch /></div>
        {import.meta.env.DEV && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted">Developer sign-in (local builds only)</summary>
            <div className="mt-2 space-y-2">
              <Textarea rows={3} placeholder="Paste a token printed by scripts/seed_dev.py" value={devToken} onChange={(e) => setDevToken(e.target.value)} />
              <Button variant="secondary" onClick={() => devSign(devToken)} disabled={!devToken}>Use token</Button>
            </div>
          </details>
        )}
      </div>
    </main>
  );
}
