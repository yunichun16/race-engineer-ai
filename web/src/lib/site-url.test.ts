import { test } from "node:test";
import assert from "node:assert/strict";
import { buildEnvProblems, LOCAL_SITE_URL, originOf, SITEMAP_PAGES, sitemapUrls, siteHref, siteUrl } from "./site-url.ts";

test("a custom domain wins over Vercel's production host", () => {
  assert.equal(
    siteUrl({ NEXT_PUBLIC_SITE_URL: "https://race.example.com/", VERCEL_PROJECT_PRODUCTION_URL: "race-engineer.vercel.app" }),
    "https://race.example.com",
  );
});

test("Vercel's production host, which has no scheme, becomes an https origin", () => {
  assert.equal(siteUrl({ VERCEL_PROJECT_PRODUCTION_URL: "race-engineer.vercel.app" }), "https://race-engineer.vercel.app");
  assert.equal(siteUrl({ NEXT_PUBLIC_SITE_URL: "", VERCEL_PROJECT_PRODUCTION_URL: "race-engineer.vercel.app" }), "https://race-engineer.vercel.app");
});

test("a local build falls back to localhost:3000", () => {
  assert.equal(siteUrl({}), LOCAL_SITE_URL);
  assert.equal(siteUrl({ NEXT_PUBLIC_SITE_URL: "  ", VERCEL_PROJECT_PRODUCTION_URL: undefined }), "http://localhost:3000");
});

test("a value that isn't an http(s) address is skipped", () => {
  assert.equal(originOf("ftp://example.com"), null);
  assert.equal(originOf("javascript:alert(1)"), null);
  assert.equal(originOf("https://"), null);
  assert.equal(siteUrl({ NEXT_PUBLIC_SITE_URL: "ftp://example.com", VERCEL_PROJECT_PRODUCTION_URL: "x.vercel.app" }), "https://x.vercel.app");
});

test("the origin keeps a port and drops a path", () => {
  assert.equal(originOf("http://localhost:3010/"), "http://localhost:3010");
  assert.equal(originOf("https://race.example.com/some/path?q=1"), "https://race.example.com");
  assert.equal(originOf("RACE.example.com"), "https://race.example.com");
});

test("siteHref makes absolute URLs on the site", () => {
  assert.equal(siteHref("/", "https://race.example.com"), "https://race.example.com/");
  assert.equal(siteHref("/report/m4-summary", "https://race.example.com"), "https://race.example.com/report/m4-summary");
  assert.equal(siteHref("/sitemap.xml", "http://localhost:3010"), "http://localhost:3010/sitemap.xml");
});

test("the sitemap lists the pages, then each report, on the site's origin", () => {
  assert.deepEqual(sitemapUrls(["m4-summary", "model-card"], "https://race.example.com"), [
    "https://race.example.com/",
    "https://race.example.com/chat",
    "https://race.example.com/mistakes",
    "https://race.example.com/styles",
    "https://race.example.com/report",
    "https://race.example.com/report/m4-summary",
    "https://race.example.com/report/model-card",
  ]);
  assert.ok(!SITEMAP_PAGES.some((page) => page.startsWith("/dev")));
  assert.equal(sitemapUrls([], "http://localhost:3000").length, SITEMAP_PAGES.length);
});

test("a Vercel production build needs the API's https address", () => {
  assert.deepEqual(buildEnvProblems({ VERCEL_ENV: "production" }).problems, [
    "NEXT_PUBLIC_API_URL is not set, so the site would call http://127.0.0.1:8000.",
  ]);
  assert.equal(buildEnvProblems({ VERCEL_ENV: "production", NEXT_PUBLIC_API_URL: "  " }).problems.length, 1);
  for (const url of ["http://127.0.0.1:8000", "http://someone--race-engineer-api.modal.run", "someone.modal.run", "not a url"]) {
    assert.equal(buildEnvProblems({ VERCEL_ENV: "production", NEXT_PUBLIC_API_URL: url }).problems.length, 1, url);
  }
  assert.deepEqual(buildEnvProblems({ VERCEL_ENV: "production", NEXT_PUBLIC_API_URL: "https://someone--race-engineer-api.modal.run" }), {
    problems: [],
    warnings: [],
  });
});

test("previews and local builds always pass, with a warning where it helps", () => {
  assert.deepEqual(buildEnvProblems({}), { problems: [], warnings: [] });
  assert.deepEqual(buildEnvProblems({ NEXT_PUBLIC_API_URL: "http://127.0.0.1:8001" }), { problems: [], warnings: [] });
  const preview = buildEnvProblems({ VERCEL_ENV: "preview" });
  assert.deepEqual(preview.problems, []);
  assert.equal(preview.warnings.length, 1);
  assert.deepEqual(buildEnvProblems({ VERCEL_ENV: "development", NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000" }).problems, []);
});

test("an API path or a bad site address is a warning, never a failure", () => {
  const withPath = buildEnvProblems({ VERCEL_ENV: "production", NEXT_PUBLIC_API_URL: "https://api.example.com/v1" });
  assert.deepEqual(withPath.problems, []);
  assert.equal(withPath.warnings.length, 1);
  assert.deepEqual(buildEnvProblems({ VERCEL_ENV: "production", NEXT_PUBLIC_API_URL: "https://api.example.com/" }).warnings, []);
  const badSite = buildEnvProblems({ NEXT_PUBLIC_SITE_URL: "ftp://example.com" });
  assert.deepEqual(badSite.problems, []);
  assert.equal(badSite.warnings.length, 1);
});
