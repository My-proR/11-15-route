/** Pure helpers for deep links, kept free of native imports so they are unit-testable. */

const TOKEN_RE = /^[A-Za-z0-9_-]{20,200}$/;

/** Pulls an invite token out of a pasted WhatsApp link (https://site/join/<token>), a routepilot://join/<token> link, or a bare token. */
export function parseInviteToken(input: string): string | null {
  const s = input.trim();
  if (!s) return null;
  const m = s.match(/\/join\/([^/?#\s]+)/);
  const cand = m ? decodeURIComponent(m[1]) : s;
  return TOKEN_RE.test(cand) ? cand : null;
}

/** Reads the PKCE `code` (or an error) from the OAuth redirect URL. Tokens in the URL fragment are deliberately not accepted. */
export function parseAuthCallback(url: string): { code: string } | { error: string } | null {
  const q = url.split("#")[0].split("?")[1];
  if (!q) return null;
  const p = new URLSearchParams(q);
  const err = p.get("error_description") ?? p.get("error");
  if (err) return { error: err };
  const code = p.get("code");
  return code ? { code } : null;
}
