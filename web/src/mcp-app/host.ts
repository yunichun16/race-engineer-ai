/**
 * The MCP App side of the charts: routing the host's tool notifications to a page, following
 * the host's theme, and the connect sequence. The charts themselves (`src/charts/`) know
 * nothing of the host; each page in this folder wires one of them to Claude through here.
 */
import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";

/** How a page reacts to its tool call; see `wireApp`. */
export interface AppHandlers<T> {
  /** Checks the result's `structuredContent` before it reaches `onResult`. */
  isData: (value: unknown) => value is T;
  /** The call's arguments, while it runs: show a status built from them. */
  onInput?: (args: Record<string, unknown>) => void;
  /** The call was cancelled before a result arrived. */
  onCancel?: () => void;
  /** A result whose `structuredContent` passed `isData`. */
  onResult: (data: T) => void;
  /**
   * An error result, or one without usable data: `text` is the tool's own message
   * (`content[0].text`) when it sent one.
   */
  onError: (text: string | undefined) => void;
}

/**
 * Route the host's tool notifications to a page. Register before `connect`: the host may send
 * the result right after the handshake. Arguments and cancellations are ignored once a result
 * is on screen.
 */
export function wireApp<T>(app: App, handlers: AppHandlers<T>): void {
  let shown = false;
  app.addEventListener("toolinput", ({ arguments: args }) => {
    if (!shown && args) handlers.onInput?.(args);
  });
  app.addEventListener("toolcancelled", () => {
    if (!shown) handlers.onCancel?.();
  });
  app.addEventListener("toolresult", (result) => {
    if (!result.isError && handlers.isData(result.structuredContent)) {
      shown = true;
      handlers.onResult(result.structuredContent);
      return;
    }
    const first = result.content?.[0];
    shown = false;
    handlers.onError(first?.type === "text" ? first.text : undefined);
  });
}

export function applyHostContext(ctx: McpUiHostContext | undefined): void {
  if (!ctx) return;
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
}

/**
 * Connect to the host and follow its theme. Call it after registering `ontoolresult` (and any
 * other handlers, or `wireApp`): the host may send the tool result right after the handshake.
 */
export async function connect(app: App): Promise<void> {
  app.onhostcontextchanged = (ctx) => applyHostContext(ctx);
  await app.connect();
  applyHostContext(app.getHostContext());
}
