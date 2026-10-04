"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Mistake } from "@/charts/find-mistakes";
import { ApiDown } from "@/components/status/ApiDown";
import { ApiNotice } from "@/components/status/ApiNotice";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { apiStatus, useHealth } from "@/lib/api/health";
import { toolStepLabel } from "@/lib/chat/format";
import { explainCornerArgs } from "@/lib/chat/links";
import { activeTurn, canAsk, savedNote, unavailableCopy, type ChatState, type ToolPart } from "@/lib/chat/reducer";
import { MAX_MESSAGE_CHARS } from "@/lib/chat/types";
import { readQuery, setQuery, withQuery } from "@/lib/url";
import { CHAT_OFF_TITLE, ChatBanner, ChatOff } from "./ChatBanner";
import { CHAT_TITLE, ChatEmpty, ChatLead, ChatTitle } from "./ChatEmpty";
import { ChatUnavailable } from "./ChatUnavailable";
import { chatStore, draftStore, useChat } from "./chat-store";
import { Composer } from "./Composer";
import { NewConversation } from "./NewConversation";
import { SmallPrint } from "./SmallPrint";
import { Transcript } from "./Transcript";
import { TurnView } from "./TurnView";
import { useNow } from "./useNow";

// The saved-answer chips and lib/chat/saved.ts load only when the chat can't answer (M7), so
// /chat's first load stays inside its size budget.
const SavedAnswers = dynamic(() => import("./SavedAnswers").then((m) => m.SavedAnswers), { ssr: false });

/**
 * What the status region says about the newest turn: "Answering…", each tool step as it starts
 * (and again if it didn't work), then "Answer ready" or what went wrong, or "Saved answer shown";
 * and, once the chat's limits close it, what the notice says. The key changes with every new
 * thing to say, including the same words for a new turn (but not with the notice's countdown).
 */
function announcement(state: ChatState, now: number): { key: string; text: string } | null {
  const turn = state.turns[state.turns.length - 1];
  const limited = state.unavailable;
  const shown = turn?.saved ? `${turn.id}\n` : "";
  if (limited !== null && limited.code !== "chat_disabled" && (turn === undefined || turn.phase === "done")) {
    const words = unavailableCopy(limited, now).spoken;
    return { key: `${shown}limits\n${limited.code}\n${limited.retryAt}`, text: shown ? `Saved answer shown. ${words}` : words };
  }
  if (turn === undefined) return null;
  if (turn.saved) return { key: `${turn.id}\nsaved`, text: "Saved answer shown." };
  let text: string;
  if (turn.phase !== "done") {
    const tool = turn.parts.findLast((part): part is ToolPart => part.kind === "tool");
    if (tool === undefined) text = "Answering…";
    else {
      const label = toolStepLabel(tool.name, tool.input);
      text = tool.result?.is_error ? `${label}: didn't work` : label;
    }
  } else if (state.blocked?.code === "chat_disabled") {
    text = CHAT_OFF_TITLE;
  } else if (turn.problem) {
    text = turn.problem.message;
  } else if (turn.outcome === "stopped") {
    text = "Stopped.";
  } else {
    text = state.blocked ? `Answer ready. ${state.blocked.message}` : "Answer ready";
  }
  return { key: `${turn.id}\n${text}`, text };
}

/**
 * The visually hidden status region (plan 6.5). The streamed text itself isn't live: this says
 * what is happening in a few words. It is written only after the page has opened, so a restored
 * conversation isn't read out as news.
 */
function Announcer({ message }: { message: { key: string; text: string } | null }) {
  const region = useRef<HTMLParagraphElement>(null);
  const said = useRef<string | null | undefined>(undefined);
  const key = message?.key ?? null;
  const text = message?.text ?? "";
  useEffect(() => {
    if (said.current === undefined) {
      // What the page opened with, which isn't news: read from the store itself, since the first
      // render after a reload may still show the server's empty state.
      said.current = announcement(chatStore.getState(), 0)?.key ?? null;
      return;
    }
    // An empty conversation has nothing to say (and StrictMode's second run may still see the
    // server's empty state). After New conversation the region empties, so the last answer's
    // words aren't left there to be found.
    if (key === null) {
      if (region.current) region.current.textContent = "";
      return;
    }
    if (key === said.current) return;
    said.current = key;
    if (region.current) region.current.textContent = text;
  }, [key, text]);
  return <p ref={region} role="status" className="sr-only" />;
}

/** Whether the device has a mouse or trackpad (moving focus there doesn't open a keyboard). */
function finePointer(): boolean {
  return window.matchMedia("(pointer: fine)").matches;
}

/**
 * The chat page: the conversation (kept in this tab), the server's state, and the composer.
 * Nothing is sent without a click: `?q=` only fills the composer, and the landing page's question
 * (sessionStorage "re.chat.pending") is sent once on arrival because the reader pressed Ask there.
 *
 * M7: while the chat's limits close it to this visitor (rate-limited, at today's budget, paused,
 * unable to check its limits) or it is switched off, a notice says why and until when, the
 * composer can't send, and "See a saved answer" chips offer recorded answers instead. A question
 * that can't be sent waits in the composer, and if it is one of the chips, its saved answer is
 * shown at once.
 */
export function ChatApp() {
  const state = useChat();
  const health = useHealth();
  const server = apiStatus(health);
  const params = useSearchParams();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [openedId, setOpenedId] = useState<string | null>(null);
  // Whether there are saved answers to offer (null until the chips have loaded the index), and
  // the landing page's question when the chat couldn't take it.
  const [hasSaved, setHasSaved] = useState<boolean | null>(null);
  const [handedOver, setHandedOver] = useState<{ question: string } | null>(null);
  const unavailable = state.unavailable;
  const now = useNow(unavailable !== null && unavailable.retryAt !== null);

  // ?q= fills the composer and leaves the address bar, so a shared link never sends anything.
  const prefill = params.get("q");
  useEffect(() => {
    if (prefill === null) return;
    if (prefill.trim() !== "") draftStore.set(prefill.slice(0, MAX_MESSAGE_CHARS));
    const rest: Record<string, string | undefined> = readQuery(window.location.search);
    delete rest.q;
    if (!setQuery(rest, { push: false })) {
      // An empty "?q=" is no change to setQuery, which drops empty values, so it would stay in
      // the address bar; replace the entry with the same query without it.
      const { pathname, hash } = window.location;
      window.history.replaceState(null, "", `${withQuery(pathname, rest)}${hash}`);
    }
  }, [prefill]);

  // The landing page's question: read and deleted once, then sent once the chat's status is in
  // (briefly awaited), so a closed chat isn't asked at all. If the chat or this conversation
  // can't take it (limits, blocked, another answer in flight), it waits in the composer instead,
  // and its saved answer shows if it is one of the chips.
  useEffect(() => {
    const pending = chatStore.consumePending();
    if (pending === null) return;
    void chatStore.checked().then(() => {
      if (chatStore.ask(pending)) return;
      draftStore.set(pending);
      setHandedOver({ question: pending });
    });
  }, []);

  // A question a limit refused comes back here (the reducer took it out of the transcript): into
  // the composer, unless something else is being typed.
  useEffect(() => {
    const question = unavailable?.question;
    if (question !== undefined && draftStore.get().trim() === "") draftStore.set(question);
  }, [unavailable]);

  const active = activeTurn(state) !== null;

  // Esc stops an answer, wherever focus is, unless a dialog (the menu, the confirmation) is open
  // or an input method is composing (there Esc cancels the composition, not the answer).
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing) return;
      if (document.querySelector("dialog[open]")) return;
      chatStore.stop();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active]);

  const sendDraft = useCallback((question: string) => {
    if (!chatStore.ask(question)) return;
    draftStore.set("");
    textarea.current?.focus(); // Send turns into Stop; keep focus in the box
  }, []);

  const askChip = useCallback((question: string) => {
    if (chatStore.ask(question) && finePointer()) textarea.current?.focus(); // the chip goes away
  }, []);

  const stop = useCallback(() => {
    chatStore.stop();
    textarea.current?.focus(); // Stop turns back into Send
  }, []);

  const retry = useCallback((turnId: string) => {
    chatStore.retry(turnId);
  }, []);

  const fill = useCallback((question: string) => {
    draftStore.set(question.slice(0, MAX_MESSAGE_CHARS));
    textarea.current?.focus();
  }, []);

  const explain = useCallback((turnId: string, toolId: string, list: unknown, mistake: Mistake) => {
    const args = explainCornerArgs(list, mistake);
    if (args === null) return;
    const id = chatStore.attach(turnId, toolId, args);
    if (id !== null) setOpenedId(id);
  }, []);

  const savedShown = useCallback(() => {
    if (finePointer()) textarea.current?.focus(); // the chip goes away
  }, []);

  const confirmNew = useCallback(() => dialog.current?.showModal(), []);

  const startNew = useCallback(() => {
    chatStore.reset();
    textarea.current?.focus();
  }, []);

  const turns = state.turns;
  const last = turns[turns.length - 1];
  const mode = health.status === "ok" ? health.data.chat.mode : null;
  const latest = health.status === "ok" ? health.data.data.latest : null;
  const off = mode === "off" || state.blocked?.code === "chat_disabled" || unavailable?.code === "chat_disabled";
  const scripted = mode === "fake" || turns.some((turn) => turn.meta?.model === "scripted");
  const checking = server === "checking";
  const down = server === "down";
  const blocked = state.blocked !== null && state.blocked.code !== "chat_disabled" ? state.blocked : null;
  // The limits' notice; the chat switched off keeps M6's ChatOff, and "down" says more.
  const limited = !off && !down && unavailable !== null ? unavailable : null;
  const limitedCopy = limited ? unavailableCopy(limited, now) : null;
  const offerSaved = (off || limited !== null) && !down;
  const canSend = !checking && !down && !off && canAsk(state);
  const blockedCode = state.blocked?.code ?? null;
  // A failed question already shows "Can't reach the analysis server" with its own Try again.
  const downShown = last?.problem?.code === "unreachable";
  const shownSaved = turns.flatMap((turn) => (turn.saved ? [turn.saved.id] : []));
  const note = savedNote(off ? (unavailable ?? { code: "chat_disabled", message: "", retryAt: null }) : limited);
  // A question a limit refused, or the landing page's one the chat couldn't take: its recording.
  const openSaved = unavailable?.question !== undefined ? unavailable : handedOver;

  const status = checking
    ? "Connecting to the analysis server…"
    : down
      ? "Can't reach the analysis server"
      : limitedCopy
        ? limitedCopy.meter
        : blocked
          ? "Start a new conversation"
          : undefined;

  const saved = offerSaved ? (
    <SavedAnswers shown={shownSaved} open={openSaved} onIndex={setHasSaved} onShown={savedShown} />
  ) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Announcer message={announcement(state, now)} />
      <Transcript followKey={last?.id ?? null} holdKey={openedId}>
        {turns.length > 0 ? <h1 className="sr-only">{CHAT_TITLE}</h1> : null}
        {scripted && !off ? <ChatBanner /> : null}
        {server === "degraded" ? <ApiNotice /> : null}
        {turns.length === 0 ? (
          <>
            {off || down || limited ? (
              <div className="pt-4 sm:pt-8">
                <ChatTitle />
                <ChatLead latest={latest} />
              </div>
            ) : (
              <ChatEmpty latest={latest} onAsk={askChip} disabled={!canSend} />
            )}
            {off ? (
              <ChatOff />
            ) : down ? (
              <ApiDown />
            ) : limited ? (
              <ChatUnavailable unavailable={limited} now={now} hasSaved={hasSaved} />
            ) : null}
            {saved}
          </>
        ) : (
          <>
            {turns.map((turn) => (
              <TurnView
                key={turn.id}
                turn={turn}
                last={turn === last}
                blockedCode={blockedCode}
                onRetry={retry}
                onEdit={fill}
                onExplain={explain}
                onPrefill={fill}
                openedId={openedId}
                savedNote={turn.saved ? note : undefined}
              />
            ))}
            {off ? <ChatOff /> : null}
            {limited ? <ChatUnavailable unavailable={limited} now={now} hasSaved={hasSaved} /> : null}
            {saved}
            {blocked ? (
              <Notice
                tone="warn"
                title={blocked.message}
                action={
                  <Button variant="secondary" size="sm" onClick={confirmNew}>
                    New conversation
                  </Button>
                }
              />
            ) : null}
            {down && !downShown && !off ? <ApiDown /> : null}
          </>
        )}
        {/* A phone keeps only one short line under the composer once a conversation has started
            (Composer), so the whole small print sits here before that, and whenever there is no
            composer. From 640 px it is under the composer. */}
        {turns.length === 0 || off ? <SmallPrint className="sm:hidden" /> : null}
      </Transcript>
      <div className="mx-auto w-full max-w-[760px] shrink-0 px-4 pt-2 pb-[max(8px,env(safe-area-inset-bottom))]">
        {off ? (
          <SmallPrint className="hidden sm:block" />
        ) : (
          <Composer
            textareaRef={textarea}
            answering={active}
            canSend={canSend}
            locked={blocked !== null}
            questionsLeft={checking || down || blocked || limited ? null : state.questionsLeft}
            quota={checking || down || limited ? null : state.quota}
            status={status}
            onSend={sendDraft}
            onStop={stop}
            onNewConversation={turns.length > 0 ? confirmNew : null}
            compactPrint={turns.length > 0}
          />
        )}
      </div>
      <NewConversation dialogRef={dialog} onConfirm={startNew} />
    </div>
  );
}
