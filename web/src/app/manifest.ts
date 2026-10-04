import type { MetadataRoute } from "next";
import { BRAND, PWA_ICONS } from "@/components/brand/brand";
import { site } from "@/content/site";

// The web app manifest (/manifest.webmanifest), so the site can be added to a phone's home
// screen and open full screen. There is no service worker: installing doesn't need one, and the
// site makes no promise to work offline. The colours are the dark page, the first-visit theme.
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: site.name,
    short_name: "Race Engineer",
    description: "Find the corner where the lap went wrong: deep learning on F1 telemetry. An unofficial fan project.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: BRAND.navy,
    theme_color: BRAND.navy,
    icons: PWA_ICONS.map(({ file, size, purpose }) => ({
      src: `/pwa-icon/${file}`,
      sizes: `${size}x${size}`,
      type: "image/png",
      purpose,
    })),
  };
}
