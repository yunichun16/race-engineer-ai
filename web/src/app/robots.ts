import type { MetadataRoute } from "next";
import { siteHref } from "@/lib/site-url";

// /robots.txt, written at build time (plan 7.4): everything may be crawled except the dev chart
// gallery (a 404 in production anyway), and the sitemap is on the site's own origin.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: "/dev/" },
    sitemap: siteHref("/sitemap.xml"),
  };
}
