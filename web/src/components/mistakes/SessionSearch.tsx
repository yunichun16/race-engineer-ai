"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ErrorNotice } from "@/components/status/ErrorNotice";
import { Button } from "@/components/ui/Button";
import { CONTROL_CLASS, Field, hintId } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { callTool, toApiError, type ApiError } from "@/lib/api/client";
import { recheckOnUnreachable } from "@/lib/api/health";
import type { SessionRef } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { MAX_SEARCH_LENGTH, parseSessionSearch } from "./search";

type SearchState =
  | { status: "idle" }
  | { status: "empty" }
  | { status: "searching" }
  | { status: "found"; match: SessionRef }
  | { status: "candidates"; candidates: SessionRef[] }
  | { status: "none"; summary: string }
  | { status: "error"; error: ApiError };

export interface SessionSearchProps {
  /** A session found: the explorer shows it (a new history entry). */
  onPick(session: SessionRef): void;
  className?: string;
}

/** "2026 Italian Grand Prix · Qualifying · 5 Sep". */
function sessionLabel(s: SessionRef): string {
  return `${s.year} ${s.event} · ${s.session_name} · ${formatDate(s.date)}${s.scored ? "" : " (not scored)"}`;
}

function statusText(state: SearchState): string {
  switch (state.status) {
    case "empty":
      return "Type a Grand Prix, a place, a season or “last race”.";
    case "searching":
      return "Looking for that session…";
    case "found":
      return `Showing the ${state.match.year} ${state.match.event}, ${state.match.session_name}.`;
    case "candidates":
      return "No single session matches. Did you mean one of these?";
    default:
      return "";
  }
}

/**
 * "Find a session" (plan 7.3): the box is read into find_session's fields in the browser
 * (`search.ts`), then the tool finds the session. A match opens it; near matches show as buttons;
 * no match shows the tool's own answer, unchanged. A new search cancels the one before.
 */
export function SessionSearch({ onPick, className }: SessionSearchProps) {
  const id = useId();
  const [state, setState] = useState<SearchState>({ status: "idle" });
  const pending = useRef<AbortController | null>(null);

  useEffect(() => () => pending.current?.abort(), []);

  const pick = (session: SessionRef) => {
    setState({ status: "found", match: session });
    onPick(session);
  };

  const search = async (text: string) => {
    const fields = parseSessionSearch(text);
    pending.current?.abort();
    if (!fields) {
      setState({ status: "empty" });
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    setState({ status: "searching" });
    try {
      const { summary, data } = await recheckOnUnreachable(callTool("find_session", fields, { signal: controller.signal }));
      if (controller.signal.aborted) return;
      if (data.match) pick(data.match);
      else if (data.candidates.length > 0) setState({ status: "candidates", candidates: data.candidates });
      else setState({ status: "none", summary });
    } catch (error) {
      const e = toApiError(error);
      if (e.code !== "aborted" && !controller.signal.aborted) setState({ status: "error", error: e });
    }
  };

  const text = statusText(state);
  return (
    <div className={["grid min-w-0 gap-3", className].filter(Boolean).join(" ")}>
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          void search(String(new FormData(event.currentTarget).get("q") ?? ""));
        }}
      >
        <Field label="Find a session" hint="Try “monza quali 2025” or “last race”." htmlFor={id}>
          <div className="flex gap-2">
            <input
              id={id}
              name="q"
              type="search"
              enterKeyHint="search"
              maxLength={MAX_SEARCH_LENGTH}
              autoComplete="off"
              spellCheck={false}
              aria-describedby={hintId(id)}
              className={CONTROL_CLASS}
            />
            <Button type="submit" variant="secondary" disabled={state.status === "searching"}>
              Find
            </Button>
          </div>
        </Field>
      </form>

      {/* One polite line for what the search did; the choices and notices follow it. */}
      <p role="status" className={text ? "text-sm text-muted" : "sr-only"}>
        {text}
      </p>

      {state.status === "candidates" ? (
        <ul className="flex flex-wrap gap-2">
          {state.candidates.map((c) => (
            <li key={c.key}>
              <Button variant="secondary" size="sm" className="whitespace-normal text-left" onClick={() => pick(c)}>
                {sessionLabel(c)}
              </Button>
            </li>
          ))}
        </ul>
      ) : null}

      {state.status === "none" ? (
        <Notice tone="info" title="No session found">
          <p className="whitespace-pre-line wrap-anywhere">{state.summary}</p>
        </Notice>
      ) : null}

      {state.status === "error" ? <ErrorNotice error={state.error} /> : null}
    </div>
  );
}
