"use client";

import { useEffect, useId, useRef, useState } from "react";
import { QUESTIONS } from "@/content/questions";
import { loadSaved, loadSavedIndex, savedIdFor, type SavedIndexEntry } from "@/lib/chat/saved";
import { chatStore } from "./chat-store";
import { CHIP_CLASS } from "./ChatEmpty";

export interface SavedAnswersProps {
  /** The recordings already in the transcript: their chips go. */
  shown: readonly string[];
  /** A question to answer from its recording at once, if it has one (the landing page's, or one
   *  a limit refused). Each request is answered once, by identity. */
  open: { question?: string } | null;
  /** Whether there are saved answers at all, once the index has loaded. */
  onIndex: (hasSaved: boolean) => void;
  /** A saved answer was added to the transcript. */
  onShown?: () => void;
}

/** Which recordings a phone shows: the example questions marked `phone` (as ChatEmpty does),
 *  and any recording that isn't one of the example questions. */
function onPhone(id: string): boolean {
  const example = QUESTIONS.find((q) => (q.saved ?? q.id) === id);
  return example === undefined || example.phone === true;
}

/**
 * "See a saved answer": a chip for each recording the site has (plan 4.4), shown while the chat
 * is rate-limited, capped, paused or off. A chip adds that recording to the transcript as a saved
 * turn; nothing is sent to the chat. The page loads this lazily, with lib/chat/saved.ts, so
 * /chat's first load doesn't carry either. With no recordings it shows nothing (the notice above
 * then points at the explorers).
 */
export function SavedAnswers({ shown, open, onIndex, onShown }: SavedAnswersProps) {
  const labelId = useId();
  const [rows, setRows] = useState<SavedIndexEntry[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const handled = useRef<object | null>(null);

  useEffect(() => {
    let live = true;
    void loadSavedIndex().then((found) => {
      if (!live) return;
      setRows(found);
      onIndex(found.length > 0);
    });
    return () => {
      live = false;
    };
  }, [onIndex]);

  // A refused or handed-over question that is one of the chips: its recording, at once.
  useEffect(() => {
    if (open?.question === undefined || rows === null || handled.current === open) return;
    handled.current = open;
    const id = savedIdFor(open.question, rows);
    if (id === null || shown.includes(id)) return;
    void loadSaved(id).then((answer) => {
      if (answer !== null && chatStore.showSaved(answer)) onShown?.();
    });
  }, [open, rows, shown, onShown]);

  if (rows === null) return null;
  const visible = rows.filter((row) => !shown.includes(row.id));
  if (visible.length === 0) return null;

  const show = (id: string) => {
    setBusy(id);
    setFailed(null);
    void loadSaved(id).then((answer) => {
      setBusy(null);
      if (answer === null) setFailed(id);
      else if (chatStore.showSaved(answer)) onShown?.();
    });
  };

  return (
    <div role="group" aria-labelledby={labelId} className="flex flex-col gap-2">
      <p id={labelId} className="micro">
        See a saved answer
      </p>
      <ul className="flex flex-wrap items-center gap-2">
        {visible.map((row) => (
          <li key={row.id} className={`${onPhone(row.id) ? "flex" : "hidden sm:flex"} max-w-full`}>
            <button
              type="button"
              disabled={busy !== null}
              aria-busy={busy === row.id || undefined}
              onClick={() => show(row.id)}
              className={`${CHIP_CLASS} gap-2`}
            >
              {busy === row.id ? <span className="spinner size-3.5 shrink-0 border-[1.5px]" aria-hidden="true" /> : null}
              {row.question}
            </button>
          </li>
        ))}
      </ul>
      {failed !== null ? (
        <p role="status" className="text-sm text-muted">
          That saved answer couldn&apos;t load. Try again in a moment.
        </p>
      ) : null}
    </div>
  );
}
