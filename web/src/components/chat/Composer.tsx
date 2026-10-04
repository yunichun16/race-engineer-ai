"use client";

import { useLayoutEffect, type FormEvent, type KeyboardEvent, type RefObject } from "react";
import { Button } from "@/components/ui/Button";
import type { QuotaState } from "@/lib/chat/reducer";
import { MAX_MESSAGE_CHARS } from "@/lib/chat/types";
import { draftStore, useDraft } from "./chat-store";
import { QuestionMeter } from "./QuestionMeter";
import { SmallPrint } from "./SmallPrint";

function PlusIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" aria-hidden="true" focusable="false" className={className}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

/** The counter shows from here: the server takes up to MAX_MESSAGE_CHARS (2,000). */
const COUNT_FROM = 1800;
const NUMBER = new Intl.NumberFormat("en");

export interface ComposerProps {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  /** A question is being answered: Send becomes Stop. */
  answering: boolean;
  /** A question can be sent now (the server answers and the conversation isn't blocked). */
  canSend: boolean;
  /** No more questions in this conversation: the box is read-only until a new one starts. */
  locked: boolean;
  questionsLeft: number | null;
  /** The visitor's quota under the limits, for "3 questions left today"; null without limits. */
  quota?: QuotaState | null;
  /** The bar's words before the first answer, or while the chat can't take a question: the
   *  server's state, if there is something to say. */
  status?: string;
  onSend: (question: string) => void;
  onStop: () => void;
  /** Starts a new conversation (after a confirmation); null while there is nothing to clear. */
  onNewConversation: (() => void) | null;
  /** The phone keeps one short line of small print once a conversation has started. */
  compactPrint: boolean;
}

/**
 * The question box pinned under the transcript. It grows to six lines; a counter appears near
 * the 2,000-character limit; a blank question can't be sent. With a mouse or trackpad Enter
 * sends and Shift+Enter adds a line; on a touch screen Enter is a new line. Enter never sends
 * while an input method is composing. Esc stops an answer (ChatApp listens for it).
 */
export function Composer({
  textareaRef,
  answering,
  canSend,
  locked,
  questionsLeft,
  quota = null,
  status,
  onSend,
  onStop,
  onNewConversation,
  compactPrint,
}: ComposerProps) {
  const draft = useDraft();
  const blank = draft.trim() === "";
  const counting = draft.length > COUNT_FROM;

  // Grow with the text, up to the max height in the class below (then it scrolls).
  useLayoutEffect(() => {
    const box = textareaRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${box.scrollHeight}px`;
  }, [draft, textareaRef]);

  const submit = () => {
    if (canSend && !answering && !blank) onSend(draft);
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
    // An input method (Japanese, Chinese, …) uses Enter to confirm its text, not to send.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (!window.matchMedia("(pointer: fine)").matches) return; // touch: Enter is a new line
    event.preventDefault();
    submit();
  };

  return (
    <div className="flex flex-col gap-2">
      <form
        onSubmit={onSubmit}
        className="glass grid gap-1.5 rounded-[22px] border-line-strong p-2 has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-focus"
      >
        <label htmlFor="chat-question" className="sr-only">
          Your question
        </label>
        <textarea
          ref={textareaRef}
          id="chat-question"
          name="question"
          rows={1}
          value={draft}
          readOnly={locked}
          aria-describedby={counting ? "chat-question-count" : undefined}
          maxLength={MAX_MESSAGE_CHARS}
          // No enterKeyHint="send": on a touch keyboard Enter adds a line here, so a key labelled
          // "send" would lie. Send is the button.
          autoComplete="off"
          placeholder={locked ? "Start a new conversation to ask more" : "Ask about any session since 2022"}
          onChange={(event) => draftStore.set(event.target.value)}
          onKeyDown={onKeyDown}
          className="max-h-[164px] min-h-12 w-full resize-none border-0 bg-transparent px-3 py-2.5 text-base/[1.5] text-fg outline-none placeholder:text-muted read-only:cursor-not-allowed"
        />
        <div className="flex min-h-11 items-center gap-2 pl-2">
          <QuestionMeter left={questionsLeft} fallback={status} quota={quota} />
          {counting ? (
            <span id="chat-question-count" className="font-mono text-xs text-muted tabular-nums">
              {NUMBER.format(draft.length)} / {NUMBER.format(MAX_MESSAGE_CHARS)}
            </span>
          ) : null}
          {onNewConversation ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onNewConversation}
              className="min-w-11 no-underline max-sm:px-0 sm:underline"
            >
              <PlusIcon className="size-4 shrink-0" />
              <span className="max-sm:sr-only">New conversation</span>
            </Button>
          ) : null}
          {answering ? (
            <Button variant="secondary" size="sm" onClick={onStop}>
              Stop
            </Button>
          ) : (
            <Button type="submit" variant="primary" size="sm" disabled={!canSend || blank}>
              Send
            </Button>
          )}
        </div>
      </form>
      <div className="px-1">
        {/* What the limits keep shows before the first question; after it, the M6 lines. */}
        <SmallPrint className="hidden sm:block" privacy={!compactPrint} />
        <SmallPrint short className={compactPrint ? "sm:hidden" : "hidden"} />
      </div>
    </div>
  );
}
