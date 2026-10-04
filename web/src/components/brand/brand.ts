/**
 * The brand mark's fixed colours and geometry, in one place: the header and footer lockups
 * (Mark.tsx), the installable app's icons (app/pwa-icon) and manifest, and the browser bar
 * colour. They are the same in both themes on purpose: the tile stays navy everywhere (spec k).
 * app/icon.svg and app/apple-icon.png are the same drawing as files.
 */

export const BRAND = {
  /** The page in the dark theme (--page), the installed app's background and the browser bar. */
  navy: "#070a12",
  /** The tile behind the mark. */
  tile: "#0e1424",
  /** The tile's hairline edge (the favicon and the lockups only). */
  edge: "#2a3552",
  /** The racing line. */
  line: "#eef2f8",
  /** The lime apex dot and its halo. */
  apex: "#c4f56a",
} as const;

/** The mark on its 32-unit grid: the racing line through the corner, and the apex. */
export const MARK = {
  line: "M9 25V17.5a8.5 8.5 0 0 1 8.5-8.5H25",
  lineWidth: 2.6,
  apex: { cx: 11.5, cy: 11.5, r: 2.4 },
  halo: { r: 4.6, opacity: 0.22 },
} as const;

export interface PwaIcon {
  /** The file name under /pwa-icon/. */
  file: string;
  size: number;
  purpose: "any" | "maskable";
}

/**
 * The installed app's icons: the full-bleed navy square at 192 and 512, and a maskable 512 with
 * the mark scaled to 80% so it stays inside the safe circle when a launcher crops it.
 */
export const PWA_ICONS: readonly PwaIcon[] = [
  { file: "192.png", size: 192, purpose: "any" },
  { file: "512.png", size: 512, purpose: "any" },
  { file: "maskable-512.png", size: 512, purpose: "maskable" },
];
