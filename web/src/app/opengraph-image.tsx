import { ImageResponse } from "next/og";
import { BRAND } from "@/components/brand/brand";
import { site } from "@/content/site";

// The picture a shared link shows (plan 7.4), drawn once at build time into a static PNG: the
// display lockup (brand sheet "w2": the site's glowing lime dot, then the name in sans 600 at
// -0.02em), the landing's headline, and what the site is, on the dark page with its faint grid.
// No data and no other lime: the dot is the only colour (spec e and k). twitter-image.tsx
// re-exports this file, so both tags point at the same drawing.
//
// next/og draws with the one font it bundles (Geist, regular weight), so the build downloads no
// font and the image is the same on every machine; a stroke in the text's own colour gives the
// semibold weight the site's system sans has. It also places each word where its measured width
// ends, which runs a little wider than the word draws, so a long word left a gap after it; each
// line is therefore one run of no-break spaces (`oneRun`), with the headline broken by hand.

export const alt = `${site.name}: find the corner where the lap went wrong. Deep learning on F1 telemetry, an unofficial fan project.`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// The dark theme's values from tokens.css, which an image can't read: --color-text-secondary and
// --bg-grid (the grid is a touch stronger than the page's, so it survives a scaled-down preview).
const MUTED = "rgb(151, 163, 184)";
const GRID = "rgba(215, 222, 234, 0.07)";
const SEMIBOLD = (px: number) => `${px}px ${BRAND.line}`;

/** The line as one word, so next/og draws it in one piece. */
function oneRun(text: string): string {
  return text.replaceAll(" ", "\u00a0");
}

/** The site's logo (Mark variant "dot"): the apex and its .22 halo at the tile's ratio, with a soft glow. */
function Dot({ px }: { px: number }) {
  const halo = Math.round((px * 7.6 * 2) / 24);
  const core = Math.round((px * 4 * 2) / 24);
  return (
    <div style={{ display: "flex", width: px, height: px, alignItems: "center", justifyContent: "center", position: "relative" }}>
      <div style={{ position: "absolute", width: halo, height: halo, borderRadius: 9999, background: BRAND.apex, opacity: 0.22 }} />
      <div
        style={{
          width: core,
          height: core,
          borderRadius: 9999,
          background: BRAND.apex,
          boxShadow: `0 0 ${Math.round(px / 6)}px ${BRAND.apex}`,
        }}
      />
    </div>
  );
}

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          width: "100%",
          height: "100%",
          position: "relative",
          background: BRAND.navy,
          color: BRAND.line,
        }}
      >
        {/* The page's grid, 64 px, fading out away from the upper left as the site's does. */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: size.width,
            height: size.height,
            backgroundImage: `linear-gradient(${GRID} 1px, transparent 1px), linear-gradient(90deg, ${GRID} 1px, transparent 1px)`,
            backgroundSize: "64px 64px",
            maskImage: "radial-gradient(circle at 360px 80px, black 240px, transparent 900px)",
          }}
        />
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            width: "100%",
            height: "100%",
            padding: "72px 84px 68px",
            position: "relative",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <Dot px={64} />
            <div style={{ fontSize: 46, letterSpacing: "-0.02em", WebkitTextStroke: SEMIBOLD(1.1) }}>{oneRun(site.name)}</div>
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              fontSize: 92,
              lineHeight: 1.04,
              letterSpacing: "-0.03em",
              WebkitTextStroke: SEMIBOLD(2),
            }}
          >
            <div>{oneRun("Find the corner where")}</div>
            <div>{oneRun("the lap went wrong.")}</div>
          </div>
          <div style={{ display: "flex", fontSize: 30, color: MUTED }}>
            {oneRun("Deep learning on F1 telemetry · an unofficial fan project")}
          </div>
        </div>
      </div>
    ),
    { ...size },
  );
}
