"use client";

import { memo, useId, type ReactNode } from "react";
import type { Mistake } from "@/charts/find-mistakes";
import { Mark } from "@/components/brand/Mark";
import { ApiDown } from "@/components/status/ApiDown";
import { Button } from "@/components/ui/Button";
import { Disclosure } from "@/components/ui/Disclosure";
import { Notice } from "@/components/ui/Notice";
import { devMeta } from "@/lib/chat/format";
import { COPY, type Part, type ToolPart, type Turn } from "@/lib/chat/reducer";
import { formatDate } from "@/lib/format";
import { IS_DEV } from "@/lib/env";
import { AnswerText } from "./AnswerText";
import { ExplorerLinks } from "./ChatBanner";
import { ChartCards } from "./ChartCards";
import { StatusLine } from "./StatusLine";
import { ToolStep } from "./ToolStep";

export interface TurnViewProps {
  turn: Turn;
  /** The newest turn: only it can be asked again. */
  last: boolean;
  /** The conversation's block, shown once at the end of the transcript, not under its turn. */
  blockedCode: string | null;
  onRetry: (turnId: string) => void;
  onEdit: (question: string) => void;
  onExplain: (turnId: string, toolId: string, list: unknown, mistake: Mistake) => void;
  onPrefill: (question: string) => void;
  openedId: string | null;
  /** A saved turn's line: how it was recorded and why it shows (reducer.ts savedNote). */
  savedNote?: string;
}

// Codes whose fix is a different question, not the same one again (plan 8.8: "edit"). M7's
// too_costly is the per-question cost ceiling: a narrower question can still work.
const EDIT_CODES = new Set(["invalid_request", "truncated", "step_limit", "too_costly"]);

type Piece = { kind: "text"; key: string; text: string } | { kind: "tools"; key: string; tools: ToolPart[] };

/** The streamed parts as pieces to draw: each text part, and each run of tool calls as one row. */
function pieces(parts: Part[]): Piece[] {
  const out: Piece[] = [];
  parts.forEach((part, i) => {
    if (part.kind === "text") {
      if (part.text.trim() !== "") out.push({ kind: "text", key: `text-${i}`, text: part.text });
    } else if (part.kind === "tool") {
      const previous = out[out.length - 1];
      if (previous?.kind === "tools") previous.tools.push(part);
      else out.push({ kind: "tools", key: `tools-${i}`, tools: [part] });
    }
    // Attachments are drawn under their chart card instead.
  });
  return out;
}

/** The answer's streamed parts in order: text as paragraphs, consecutive tool calls as one row of
 *  chips where they happened. */
function Streamed({ parts, answering }: { parts: Part[]; answering: boolean }) {
  return pieces(parts).map((piece) =>
    piece.kind === "text" ? (
      <AnswerText key={piece.key} text={piece.text} />
    ) : (
      <ul key={piece.key} aria-label="Tool steps" className="flex flex-wrap gap-2">
        {piece.tools.map((tool) => (
          <ToolStep key={tool.id} part={tool} answering={answering} />
        ))}
      </ul>
    ),
  );
}

/** What went wrong with this turn, in the words of plan 8.8, with the action that can help. */
function Problem({ turn, last, blockedCode, onRetry, onEdit }: Omit<TurnViewProps, "onExplain" | "onPrefill" | "openedId" | "savedNote">) {
  const problem = turn.problem;
  if (turn.outcome === "stopped" && problem === undefined) {
    return <p className="text-sm text-muted">{COPY.stopped}</p>;
  }
  if (problem === undefined || (blockedCode !== null && problem.code === blockedCode)) return null;

  const retry = last && problem.retryable;
  if (problem.code === "unreachable") {
    return last ? (
      <ApiDown onRetry={() => onRetry(turn.id)} />
    ) : (
      <Notice tone="error" title={problem.message} />
    );
  }
  if (problem.code === "refusal") return <Notice tone="info" title={problem.message} />;
  // The server's Anthropic credentials were refused: no question will work, so point at what
  // does (plan 8.8: links).
  if (problem.code === "chat_not_configured") {
    return (
      <Notice tone="warn" title={problem.message}>
        <ExplorerLinks />
      </Notice>
    );
  }

  let action: ReactNode = undefined;
  if (retry) {
    action = (
      <Button variant="secondary" size="sm" onClick={() => onRetry(turn.id)}>
        Try again
      </Button>
    );
  } else if (last && EDIT_CODES.has(problem.code)) {
    action = (
      <Button variant="secondary" size="sm" onClick={() => onEdit(turn.question)}>
        Edit the question
      </Button>
    );
  }
  return <Notice tone={problem.retryable ? "error" : "warn"} title={problem.message} action={action} />;
}

/**
 * One question and its answer. The question is the turn's heading (so a screen reader can jump
 * from question to question); the answer is the text and tool steps as they streamed, then the
 * chart cards, then what went wrong if anything did, and the progress notes folded into "Steps".
 * A saved example answer (M7) says so in its label, "Saved answer · 4 Oct 2026", with a line on
 * how it was made; its charts, "What Claude saw" and "Show telemetry" work as in a live one.
 */
function TurnViewInner({ turn, last, blockedCode, onRetry, onEdit, onExplain, onPrefill, openedId, savedNote }: TurnViewProps) {
  const headingId = useId();
  const answering = turn.phase !== "done";
  const saved = turn.saved;
  return (
    <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-4">
      <h2
        id={headingId}
        className="glass-inset ml-auto max-w-[85%] rounded-[20px_20px_6px_20px] px-4 py-3 text-base/[1.55] font-normal whitespace-pre-wrap wrap-anywhere text-fg"
      >
        <span className="sr-only">{saved ? "Saved answer to: " : "You asked: "}</span>
        {turn.question}
      </h2>
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-col gap-1">
          <p className="micro flex items-center gap-2">
            {/* The site's logo is the glowing apex dot alone (spec k), here as in the header. */}
            <Mark variant="dot" className="size-[18px]" />
            {saved ? `Saved answer · ${formatDate(saved.recordedAt, { year: true })}` : "Race engineer"}
          </p>
          {saved && savedNote ? <p className="text-sm text-muted">{savedNote}</p> : null}
        </div>
        <Streamed parts={turn.parts} answering={answering} />
        <ChartCards turn={turn} onExplain={onExplain} onPrefill={onPrefill} openedId={openedId} />
        {answering ? <StatusLine note={turn.notes[turn.notes.length - 1]} /> : null}
        <Problem turn={turn} last={last} blockedCode={blockedCode} onRetry={onRetry} onEdit={onEdit} />
        {!answering && turn.notes.length > 0 ? (
          <Disclosure summary={`Steps (${turn.notes.length})`} className="self-start">
            <ol className="flex list-decimal flex-col gap-1 pl-4 text-sm text-muted marker:text-muted">
              {turn.notes.map((note, i) => (
                <li key={i} className="wrap-anywhere whitespace-pre-line">
                  {note}
                </li>
              ))}
            </ol>
          </Disclosure>
        ) : null}
        {IS_DEV && turn.meta ? <p className="font-mono text-xs text-muted">{devMeta(turn.meta)}</p> : null}
      </div>
    </section>
  );
}

/** Re-renders only when its own turn changes: earlier turns keep their identity while a new one
 *  streams (lib/chat/reducer.ts), so a long conversation doesn't redraw on every text delta. */
export const TurnView = memo(TurnViewInner);
