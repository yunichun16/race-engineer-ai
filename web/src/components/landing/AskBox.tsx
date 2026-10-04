"use client";

import { useRouter } from "next/navigation";
import { useRef, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { chipLabel, LANDING_QUESTIONS } from "@/content/questions";
import { handOff, MAX_QUESTION_CHARS, sessionStore } from "./ask";

/**
 * The hero's question box and its three suggested questions. Asking (the button, Enter, or a
 * chip) leaves the question for the chat and goes to /chat, which sends it once (plan decision 8).
 * The landing itself never calls the chat or checks the server: if the chat is off, /chat says so.
 */
export function AskBox() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);

  const ask = (question: string) => {
    const href = handOff(sessionStore(), question);
    if (href) router.push(href);
    else input.current?.focus(); // a blank question goes nowhere
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    ask(input.current?.value ?? "");
  };

  return (
    <>
      <form onSubmit={onSubmit}>
        <label htmlFor="ask" className="micro mb-2 block">
          Ask about any session since 2022
        </label>
        <div className="glass flex max-w-[560px] gap-1.5 rounded-[18px] border-line-strong p-1.5 has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-focus">
          <input
            ref={input}
            id="ask"
            name="q"
            type="text"
            autoComplete="off"
            enterKeyHint="send"
            maxLength={MAX_QUESTION_CHARS}
            placeholder="Where did Norris lose time?"
            className="min-w-0 flex-1 border-0 bg-transparent px-3 py-2.5 text-base text-fg outline-none placeholder:text-muted"
          />
          <Button type="submit" variant="primary">
            Ask
          </Button>
        </div>
      </form>
      <ul aria-label="Suggested questions" className="mt-3.5 mb-4.5 flex max-w-[560px] flex-wrap gap-2">
        {LANDING_QUESTIONS.map((q) => (
          <li key={q.id}>
            <button
              type="button"
              title={q.text}
              onClick={() => ask(q.text)}
              className="glass glass-sm inline-flex min-h-11 cursor-pointer items-center rounded-full px-4 text-left text-sm text-muted transition-colors duration-(--dur-hover) hover:bg-glass-strong hover:text-fg"
            >
              {chipLabel(q)}
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
