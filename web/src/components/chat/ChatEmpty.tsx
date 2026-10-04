import { QUESTION_GROUPS, QUESTIONS } from "@/content/questions";

export const CHAT_TITLE = "Ask the race engineer";

/** A question chip on the bare page (spec g: `glass glass-sm`), here and for saved answers. */
export const CHIP_CLASS =
  "glass glass-sm inline-flex min-h-11 max-w-full cursor-pointer items-center rounded-[18px] px-4 py-2.5 text-left text-[15px]/[1.4] text-fg transition-colors duration-(--dur-hover) hover:bg-glass-strong disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-(--glass)";

/** The page's h1, as the inner pages set it (spec g). */
export function ChatTitle() {
  return <h1 className="text-[clamp(36px,5vw,56px)] font-semibold tracking-tight text-balance text-fg">{CHAT_TITLE}</h1>;
}

/** "…from 2022 to the 2026 Azerbaijan Grand Prix Race.", or "since 2022" until the server says. */
export function ChatLead({ latest }: { latest: string | null }) {
  return (
    <p className="mt-3 max-w-[60ch] text-lead text-muted">
      Questions about any qualifying, sprint or race {latest ? `from 2022 to the ${latest}` : "since 2022"}. Answers
      come from the analysis tools, with charts.
    </p>
  );
}

export interface ChatEmptyProps {
  latest: string | null;
  /** Sends a chip's question. */
  onAsk: (question: string) => void;
  /** No question can be sent yet (the server is being checked, or can't be reached). */
  disabled: boolean;
}

/**
 * The chat before the first question: the title, what it can answer, and the example questions
 * in their groups. A chip sends its full question. A phone shows the four marked `phone`, without
 * the group labels (each group would hold one chip); from 640 px every chip shows, by group.
 */
export function ChatEmpty({ latest, onAsk, disabled }: ChatEmptyProps) {
  return (
    <div className="pt-4 sm:pt-8">
      <ChatTitle />
      <ChatLead latest={latest} />
      <div className="mt-6 flex flex-col gap-2 sm:mt-8 sm:gap-3">
        {QUESTION_GROUPS.map((group) => {
          const questions = QUESTIONS.filter((q) => q.group === group.id);
          if (questions.length === 0) return null;
          const onPhone = questions.some((q) => q.phone);
          const labelId = `chat-group-${group.id}`;
          return (
            <div
              key={group.id}
              role="group"
              aria-labelledby={labelId}
              className={`${onPhone ? "grid" : "hidden sm:grid"} gap-2 sm:grid-cols-[124px_minmax(0,1fr)] sm:items-start`}
            >
              <p id={labelId} className="micro max-sm:sr-only sm:pt-3">
                {group.label}
              </p>
              <ul className="flex flex-wrap items-center gap-2">
                {questions.map((q) => (
                  <li key={q.id} className={`${q.phone ? "flex" : "hidden sm:flex"} max-w-full flex-wrap items-center gap-x-3 gap-y-1`}>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onAsk(q.text)}
                      className={CHIP_CLASS}
                    >
                      {q.text}
                    </button>
                    {q.hint ? <span className="text-sm text-muted">{q.hint}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}
