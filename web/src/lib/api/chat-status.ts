/**
 * GET /api/chat/status: whether the chat would take a question from this visitor now (plan
 * 13.3). /chat reads it once as it opens, so a visitor who is rate-limited, or a chat that is
 * capped, paused or off, sees saved answers before typing anything. It never spends a question.
 *
 * The body is checked field by field. Only `available` is required; anything else that doesn't
 * fit gets a neutral value (no reason, no wait, no quota), so a small server change can't hide the
 * whole answer. A reason this site doesn't know reads as "unavailable" while the chat is closed,
 * and an available chat has no reason.
 */

import { ApiError, getJSON, type RequestOptions } from "./client.ts";
import type { ChatStatus } from "./types.ts";

export const CHAT_STATUS_PATH = "/api/chat/status";

const REASONS: readonly NonNullable<ChatStatus["reason"]>[] = ["off", "paused", "daily_cap", "rate_limited", "unavailable"];
const MODES: readonly ChatStatus["mode"][] = ["anthropic", "fake", "off"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function quotaOf(value: unknown): ChatStatus["quota"] {
  if (!isRecord(value)) return null;
  const hour_left = count(value.hour_left);
  const day_left = count(value.day_left);
  const per_hour = count(value.per_hour);
  const per_day = count(value.per_day);
  if (hour_left === null || day_left === null || per_hour === null || per_day === null) return null;
  return { hour_left, day_left, per_hour, per_day };
}

function savedOf(value: unknown): ChatStatus["saved"] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) =>
    isRecord(item) && typeof item.id === "string" && typeof item.recorded_at === "string"
      ? [{ id: item.id, recorded_at: item.recorded_at }]
      : [],
  );
}

/** The status in a body, or null when it isn't one (no boolean `available`). */
export function parseChatStatus(value: unknown): ChatStatus | null {
  if (!isRecord(value) || typeof value.available !== "boolean") return null;
  const available = value.available;
  const known = REASONS.find((reason) => reason === value.reason) ?? null;
  const reason = available ? null : (known ?? "unavailable");
  const mode = MODES.find((m) => m === value.mode) ?? (reason === "off" ? "off" : "anthropic");
  return {
    available,
    mode,
    reason,
    retry_after_s: count(value.retry_after_s),
    quota: quotaOf(value.quota),
    visitor: typeof value.visitor === "string" ? value.visitor : null,
    saved: savedOf(value.saved),
  };
}

/** GET /api/chat/status. Throws an ApiError when the server can't be reached or answers with
 *  something else (an older API without the route answers 404). */
export async function loadChatStatus(init: RequestOptions = {}): Promise<ChatStatus> {
  const body = await getJSON<unknown>(CHAT_STATUS_PATH, {}, init);
  const status = parseChatStatus(body);
  if (status === null) throw new ApiError(200, "bad_payload", "The chat status couldn't be read.");
  return status;
}
