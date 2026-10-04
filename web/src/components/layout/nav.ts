// The site's four sections, in header order. The phone menu shows the one-line descriptions.

export interface NavItem {
  href: string;
  label: string;
  description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/chat", label: "Chat", description: "Ask the race engineer" },
  { href: "/mistakes", label: "Mistakes", description: "Flagged corners, race by race" },
  { href: "/styles", label: "Styles", description: "How teammates drive differently" },
  { href: "/report", label: "Report", description: "Method, results and limits" },
];

/** Whether `href` is the page being shown, or a page inside it (/report/dataset-card is under Report). */
export function isCurrent(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  return pathname === href || pathname.startsWith(`${href}/`);
}
