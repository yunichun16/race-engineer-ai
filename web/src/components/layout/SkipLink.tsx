/**
 * The first focusable thing on every page: hidden until a keyboard reaches it, then shown as an
 * opaque pill over the header. It moves focus to <main id="main" tabindex="-1">, past the
 * navigation.
 */
export function SkipLink() {
  return (
    <a
      href="#main"
      className={
        "sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-4 focus:z-[60] focus:inline-flex focus:min-h-11 " +
        "focus:items-center focus:rounded-full focus:bg-(--glass-solid) focus:px-4 focus:font-semibold focus:text-fg " +
        "focus:shadow-[var(--shadow-glass-sm)]"
      }
    >
      Skip to content
    </a>
  );
}
