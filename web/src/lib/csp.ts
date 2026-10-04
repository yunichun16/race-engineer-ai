/**
 * The Content Security Policy and the other security headers (plan 7.3, decision 9), which
 * next.config.ts sends on every route of a production build. `next dev` gets none: it needs
 * `eval` and a websocket for hot reload.
 *
 * `script-src` allows `'unsafe-inline'` because a static page carries Next's inline RSC payload
 * scripts (13 to 40 a page, different in every build) as well as the theme script, so a list of
 * hashes would block hydration, and nonces would make every page dynamic (M6 decision 2). What
 * keeps that safe is the site's own rule: API and chat text reach the DOM only as React text, the
 * one `dangerouslySetInnerHTML` is the constant theme script, and report Markdown is parsed into
 * tokens. `style-src` needs it too, for inline `style` attributes.
 *
 * The browser fetches only from the site itself and from the API (`connect-src`). Everything
 * else (chunks, snapshots, saved answers, report figures, icons, the manifest) is same-origin;
 * `data:` images are Tailwind's. `upgrade-insecure-requests` is added only when the API is https,
 * so a local production build against `http://127.0.0.1:8000` still works. No
 * Strict-Transport-Security here: Vercel sends its own on its domains, and `includeSubDomains`
 * from the app would bind every subdomain of a custom domain.
 *
 * Pure: next.config.ts passes in the API URL and whether this is `next dev`.
 */

export interface Header {
  key: string;
  value: string;
}

/** The API's origin (scheme, host and port, no path), or null when it isn't an http(s) URL. */
export function apiOrigin(apiUrl: string): string | null {
  try {
    const url = new URL(apiUrl);
    return url.protocol === "https:" || url.protocol === "http:" ? url.origin : null;
  } catch {
    return null;
  }
}

/** The policy for a production build whose browser code calls `apiUrl`. */
export function contentSecurityPolicy(apiUrl: string): string {
  const api = apiOrigin(apiUrl);
  const directives: [string, ...string[]][] = [
    ["default-src", "'self'"],
    ["script-src", "'self'", "'unsafe-inline'"],
    ["style-src", "'self'", "'unsafe-inline'"],
    ["img-src", "'self'", "data:"],
    ["font-src", "'self'"],
    ["connect-src", "'self'", ...(api ? [api] : [])],
    ["object-src", "'none'"],
    ["base-uri", "'self'"],
    ["form-action", "'self'"],
    ["frame-ancestors", "'none'"],
    ["manifest-src", "'self'"],
  ];
  if (api?.startsWith("https:")) directives.push(["upgrade-insecure-requests"]);
  return directives.map((d) => d.join(" ")).join("; ");
}

/** The headers for every route: none under `next dev`, the policy and its companions otherwise. */
export function securityHeaders({ dev, apiUrl }: { dev: boolean; apiUrl: string }): Header[] {
  if (dev) return [];
  return [
    { key: "Content-Security-Policy", value: contentSecurityPolicy(apiUrl) },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), browsing-topics=()" },
    { key: "X-Frame-Options", value: "DENY" },
  ];
}
