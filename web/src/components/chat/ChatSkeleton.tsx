import { ChatLead, ChatTitle } from "./ChatEmpty";

/**
 * The chat's shell before its script runs (the page's static HTML, and the Suspense fallback):
 * the title and lead where the empty chat puts them, and the composer's box, so the page doesn't
 * jump when the chat takes over.
 */
export function ChatSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-hidden">
        <div className="mx-auto w-full max-w-[760px] px-4 pt-3">
          <div className="pt-4 sm:pt-8">
            <ChatTitle />
            <ChatLead latest={null} />
          </div>
        </div>
      </div>
      <div className="mx-auto w-full max-w-[760px] shrink-0 px-4 pt-2 pb-[max(8px,env(safe-area-inset-bottom))]">
        <div aria-hidden="true" className="glass h-[110px] rounded-[22px] border-line-strong" />
        <p className="sr-only">Loading the chat…</p>
      </div>
    </div>
  );
}
