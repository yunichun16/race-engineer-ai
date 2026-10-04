/**
 * The site's public addresses (plan 7.1 and 7.4).
 *
 * Its own origin is the root layout's `metadataBase` (so the Open Graph and Twitter image tags
 * are absolute URLs), robots.txt's sitemap line and the sitemap's entries. In order:
 * - `NEXT_PUBLIC_SITE_URL`, set only for a custom domain;
 * - Vercel's `VERCEL_PROJECT_PRODUCTION_URL`, the project's production host name (no scheme),
 *   which Vercel gives every build, previews included, so a shared preview link still points its
 *   image at the live site;
 * - `http://localhost:3000` for a local build.
 *
 * `buildEnvProblems` is `scripts/check-env.ts`'s rule: a Vercel production build must be given
 * the API's https address, or it would ship calling `http://127.0.0.1:8000` (lib/env.ts's
 * default) with a policy that allows only that, and every page would look broken.
 *
 * Server only, and read at build time: `VERCEL_PROJECT_PRODUCTION_URL` isn't a `NEXT_PUBLIC_`
 * variable, so a browser bundle would never see it. Only server files (the layout's metadata,
 * robots.ts, sitemap.ts) and the build scripts import this module.
 */

import { apiOrigin } from "./csp.ts";

export const LOCAL_SITE_URL = "http://localhost:3000";

export interface SiteUrlEnv {
  NEXT_PUBLIC_SITE_URL?: string;
  VERCEL_PROJECT_PRODUCTION_URL?: string;
}

/** "https://example.com" from "example.com", "https://example.com/" or "https://example.com"; null when it isn't an http(s) address. */
export function originOf(value: string | undefined): string | null {
  const text = value?.trim();
  if (!text) return null;
  try {
    const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return url.origin;
  } catch {
    return null;
  }
}

/** The site's origin, with no trailing slash. A value that isn't an http(s) address is skipped. */
export function siteUrl(env: SiteUrlEnv): string {
  return originOf(env.NEXT_PUBLIC_SITE_URL) ?? originOf(env.VERCEL_PROJECT_PRODUCTION_URL) ?? LOCAL_SITE_URL;
}

/** The site's origin for this build. */
export const SITE_URL = siteUrl({
  NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL,
  VERCEL_PROJECT_PRODUCTION_URL: process.env.VERCEL_PROJECT_PRODUCTION_URL,
});

/** An absolute URL on the site: "/chat" becomes "https://example.com/chat", and "/" the origin with its slash. */
export function siteHref(pathname: string, base: string = SITE_URL): string {
  return new URL(pathname, `${base}/`).href;
}

/** The pages a search engine may list, before the reports: the dev gallery and the API are not among them. */
export const SITEMAP_PAGES: readonly string[] = ["/", "/chat", "/mistakes", "/styles", "/report"];

/** The sitemap's URLs: the pages, then every published report in the manifest's order. */
export function sitemapUrls(slugs: readonly string[], base: string = SITE_URL): string[] {
  return [...SITEMAP_PAGES, ...slugs.map((slug) => `/report/${slug}`)].map((page) => siteHref(page, base));
}

export interface BuildEnv {
  VERCEL_ENV?: string;
  NEXT_PUBLIC_API_URL?: string;
  NEXT_PUBLIC_SITE_URL?: string;
}

/**
 * What would make this build ship broken: on a Vercel production build (`VERCEL_ENV=production`),
 * an API address that is missing or not https. Anywhere else, nothing. `warnings` are worth a
 * look but never fail a build: a non-https API on a preview, a site address that isn't one.
 */
export function buildEnvProblems(env: BuildEnv): { problems: string[]; warnings: string[] } {
  const problems: string[] = [];
  const warnings: string[] = [];
  const given = env.NEXT_PUBLIC_API_URL?.trim() ?? "";
  const api = given ? apiOrigin(given) : null;
  const https = api?.startsWith("https:") ?? false;
  if (env.VERCEL_ENV === "production") {
    if (!given) problems.push("NEXT_PUBLIC_API_URL is not set, so the site would call http://127.0.0.1:8000.");
    else if (!https) problems.push(`NEXT_PUBLIC_API_URL must be the API's https:// address, not "${given}".`);
  } else if (env.VERCEL_ENV === "preview" && !https) {
    warnings.push("NEXT_PUBLIC_API_URL isn't an https:// address, so this preview's charts and chat won't load.");
  }
  if (api && new URL(given).pathname.replace(/\/+$/, "") !== "") {
    warnings.push(`NEXT_PUBLIC_API_URL should be the API's origin; the path in "${given}" is kept before /api/….`);
  }
  if (env.NEXT_PUBLIC_SITE_URL?.trim() && !originOf(env.NEXT_PUBLIC_SITE_URL)) {
    warnings.push(`NEXT_PUBLIC_SITE_URL "${env.NEXT_PUBLIC_SITE_URL}" isn't an http(s) address, so it is ignored.`);
  }
  return { problems, warnings };
}
