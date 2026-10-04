/**
 * Build-time settings. Next.js inlines `process.env.NEXT_PUBLIC_*` and `NODE_ENV` into the
 * browser bundle when the code reads them by their full name, as below, so these are constants in
 * every page. Set them in `web/.env.local` or the shell (see `.env.example`).
 *
 * The site's own address (`NEXT_PUBLIC_SITE_URL`, else Vercel's production domain) is in
 * `site-url.ts` instead: only server files need it, and the Vercel fallback isn't a browser
 * variable. `API_URL` also sets the production Content Security Policy's `connect-src`
 * (`csp.ts`, from next.config.ts), so the policy always names the API the pages call.
 */

/** The FastAPI server the browser calls directly (over CORS), without a trailing slash. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

/** The public repository, once there is one (M7): turns report references into links. */
export const REPO_URL: string | null = process.env.NEXT_PUBLIC_REPO_URL || null;

/** `next dev`. Development-only help (how to start the API, server problems) shows only here. */
export const IS_DEV = process.env.NODE_ENV === "development";
