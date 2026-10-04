import { ImageResponse } from "next/og";
import { BRAND, MARK, PWA_ICONS } from "@/components/brand/brand";

// The installed app's icons (app/manifest.ts lists them), drawn from the brand mark at build time:
// static files in the build output, no image package. "any" is the full-bleed navy square of
// apple-icon.png at size; "maskable" is the same square with the mark scaled to 80% about the
// centre, so its farthest point (the line's end cap) stays inside the 80% safe circle a launcher
// may crop to.

export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  return PWA_ICONS.map(({ file }) => ({ file }));
}

export async function GET(_request: Request, { params }: RouteContext<"/pwa-icon/[file]">) {
  const { file } = await params;
  const icon = PWA_ICONS.find((candidate) => candidate.file === file);
  if (!icon) return new Response("Not found", { status: 404 });

  // Scaling the drawing to 80% about the centre is the same as widening the view box by 1/0.8:
  // 32 units become 40, starting 4 units out on each side, with the navy square filling it all.
  const [origin, span] = icon.purpose === "maskable" ? [-4, 40] : [0, 32];
  return new ImageResponse(
    (
      <div style={{ display: "flex", width: "100%", height: "100%", background: BRAND.tile }}>
        <svg width={icon.size} height={icon.size} viewBox={`${origin} ${origin} ${span} ${span}`}>
          <rect x={origin} y={origin} width={span} height={span} fill={BRAND.tile} />
          <path d={MARK.line} fill="none" stroke={BRAND.line} strokeWidth={MARK.lineWidth} strokeLinecap="round" />
          <circle cx={MARK.apex.cx} cy={MARK.apex.cy} r={MARK.halo.r} fill={BRAND.apex} opacity={MARK.halo.opacity} />
          <circle cx={MARK.apex.cx} cy={MARK.apex.cy} r={MARK.apex.r} fill={BRAND.apex} />
        </svg>
      </div>
    ),
    {
      width: icon.size,
      height: icon.size,
      headers: { "Cache-Control": "public, max-age=3600" },
    },
  );
}
