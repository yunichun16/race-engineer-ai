import type { Metadata } from "next";
import { Suspense } from "react";
import { ChatApp } from "@/components/chat/ChatApp";
import { ChatSkeleton } from "@/components/chat/ChatSkeleton";

export const metadata: Metadata = {
  title: "Chat",
  description:
    "Ask about any F1 qualifying, sprint or race since 2022 in plain words. Answers come from the project's analysis tools, with charts. Unofficial fan project.",
};

// A static shell: the chat is a client component (it reads ?q= with useSearchParams, so it sits
// in a Suspense boundary) and talks to the API from the browser.
export default function ChatPage() {
  return (
    <Suspense fallback={<ChatSkeleton />}>
      <ChatApp />
    </Suspense>
  );
}
