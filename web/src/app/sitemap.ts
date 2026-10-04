import type { MetadataRoute } from "next";
import { REPORTS } from "@/lib/report/manifest";
import { sitemapUrls } from "@/lib/site-url";

// /sitemap.xml, written at build time (plan 7.4): the five pages and every published report, in
// the manifest's order. No dates: the pages are rebuilt on every deploy, so a build time would say
// nothing about when the content changed.
export default function sitemap(): MetadataRoute.Sitemap {
  return sitemapUrls(REPORTS.map((report) => report.slug)).map((url) => ({ url }));
}
