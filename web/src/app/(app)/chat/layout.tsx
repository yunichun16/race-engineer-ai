// The chat fills the window under the header, with no footer: the transcript scrolls inside it
// and the composer stays at the bottom. 100dvh follows the phone's address bar as it moves.
export default function ChatLayout({ children }: LayoutProps<"/chat">) {
  return (
    <main id="main" tabIndex={-1} className="flex h-[calc(100dvh-var(--header-h))] flex-col">
      {children}
    </main>
  );
}
