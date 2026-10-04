/**
 * Where a link in a report file goes on the site. Addresses in a report are relative to `report/`:
 * another report (`m3_results.md`) becomes `/report/<slug>`, a figure (`figures/x.png`) becomes
 * `/report/figures/x.png`, `#anchor` stays, and `http(s)` links go out with
 * `rel="noopener noreferrer"`. Anything else (`m4_review_queue.csv`, `../engine/…`, an excluded
 * report) is a file in the repository: the page shows it as inline code titled "In the
 * repository", or links it on GitHub once `NEXT_PUBLIC_REPO_URL` is set (M7).
 */
import { reportByFile } from "./manifest.ts";

export const REPO_TITLE = "In the repository";
export const EXTERNAL_REL = "noopener noreferrer";

export type LinkTarget =
  | { kind: "report"; href: string; slug: string; hash: string } // hash is "" or "#id"
  | { kind: "figure"; href: string; file: string }
  | { kind: "anchor"; href: string; id: string }
  | { kind: "external"; href: string }
  | { kind: "repo"; path: string; href: string | null } // path from the repository root
  | { kind: "invalid"; reason: string };

/**
 * Resolve an address written in a report. `repoUrl` (for example
 * "https://github.com/<owner>/race-engineer") turns repository files into links on its main branch.
 */
export function resolveLink(raw: string, repoUrl?: string): LinkTarget {
  const href = raw.trim();
  if (href === "" || href === "#") return { kind: "invalid", reason: "empty address" };
  if (href.startsWith("#")) return { kind: "anchor", href, id: href.slice(1) };
  if (/^https?:\/\/[^/\s]+/i.test(href)) return { kind: "external", href };
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//")) {
    return { kind: "invalid", reason: "only http(s) addresses may leave the site" };
  }

  const cut = href.search(/[?#]/);
  const hash = cut >= 0 && href[cut] === "#" ? href.slice(cut) : "";
  if (cut >= 0 && href[cut] === "?") return { kind: "invalid", reason: "query strings are not supported" };
  const written = cut >= 0 ? href.slice(0, cut) : href;
  const path = normalise(written.startsWith("/") ? written.slice(1) : `report/${written}`);
  if (path === null) return { kind: "invalid", reason: "the path leaves the repository" };

  const report = /^report\/([^/]+\.md)$/.exec(path);
  const entry = report ? reportByFile(report[1]) : undefined;
  if (entry) return { kind: "report", href: `/report/${entry.slug}${hash}`, slug: entry.slug, hash };

  const figure = /^report\/figures\/([^/]+\.png)$/.exec(path);
  if (figure && !hash) return { kind: "figure", href: `/report/figures/${figure[1]}`, file: figure[1] };

  const base = repoUrl?.replace(/\/+$/, "");
  return { kind: "repo", path, href: base ? `${base}/blob/main/${path}${hash}` : null };
}

/** An image in a report must be one of its figures: the page sizes it from the PNG header. */
export function resolveImage(src: string): Extract<LinkTarget, { kind: "figure" } | { kind: "invalid" }> {
  const target = resolveLink(src);
  return target.kind === "figure" ? target : { kind: "invalid", reason: "an image must be a PNG in report/figures" };
}

/** Resolve `.` and `..` in a slash-separated path; null if it climbs above the repository root. */
function normalise(path: string): string | null {
  const out: string[] = [];
  for (const part of path.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (out.length === 0) return null;
      out.pop();
    } else out.push(part);
  }
  return out.join("/");
}
