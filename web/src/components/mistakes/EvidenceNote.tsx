import type { CornerChart } from "@/charts/explain-corner";
import type { FeaturedCorner } from "@/content/featured";
import { evidenceText } from "./selection";

export interface EvidenceNoteProps {
  /** The featured entry, when the open corner is one: its own blurb is the note. */
  featured?: FeaturedCorner;
  /** The corner's answer, once it is here. */
  corner: CornerChart | null;
  /** What to say until the answer arrives (for a corner that isn't featured); empty for nothing. */
  pending: string;
}

/**
 * One plain line under the open corner's heading saying where the finding comes from: race
 * control, or the telemetry alone, or that the corner wasn't flagged (or wasn't scored) and is
 * shown for comparison.
 * No accuracy figure and no review wording (plan 13).
 */
export function EvidenceNote({ featured, corner, pending }: EvidenceNoteProps) {
  const text = evidenceText(featured, corner) ?? pending;
  return text ? <p className="text-sm text-muted">{text}</p> : null;
}
