/**
 * Local stand-in for Claude: loads an MCP App page in an iframe and plays the host side of
 * the MCP Apps protocol with a saved tool result, so charts can be checked in a browser
 * without a Claude session. Start it with `npm run dev:mcp-app` and open /dev-host.html.
 *
 * Query string: ?app=<page> picks the page (telemetry by default; the links at the top list
 * them all), and &fixture=<name> picks another result file (<name>.json next to this one).
 * The committed fixtures are synthetic, written by engine/scripts/export_sample_result.py.
 *
 * Messages a page sends to the conversation (`app.sendMessage`, e.g. an "Explain" button) are
 * shown in the log under the page instead of being posted anywhere.
 */
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

// App page -> its default fixture.
const APPS: Record<string, string> = {
  telemetry: "sample-telemetry",
  "compare-laps": "sample-compare-laps",
  "find-mistakes": "sample-find-mistakes",
  "explain-corner": "sample-explain-corner",
  "compare-styles": "sample-compare-styles",
  "race-summary": "sample-race-summary",
};

interface Fixture {
  arguments?: Record<string, unknown>; // replayed as the tool input when present
  content: unknown[];
  structuredContent?: unknown;
}

// Vite resolves this at build time. (Next's global types, which this project checks against,
// declare import.meta.glob without generics, hence the cast where a fixture is loaded.)
const fixtures = import.meta.glob("./*.json");
const params = new URLSearchParams(location.search);
const app = params.get("app") ?? "telemetry";
const fixtureName = params.get("fixture") ?? APPS[app];
const iframe = document.getElementById("app") as HTMLIFrameElement;

const nav = document.getElementById("apps")!;
for (const name of Object.keys(APPS)) {
  const link = document.createElement("a");
  link.href = `?app=${name}`;
  link.textContent = name;
  if (name === app) link.setAttribute("aria-current", "page");
  nav.append(link, " ");
}

function fail(message: string): never {
  const error = document.getElementById("error")!;
  error.textContent = message;
  error.hidden = false;
  throw new Error(message);
}

if (!(app in APPS)) fail(`Unknown app "${app}". Try one of: ${Object.keys(APPS).join(", ")}.`);
const loadFixture = fixtures[`./${fixtureName}.json`];
if (!loadFixture) fail(`No fixture ${fixtureName}.json in src/mcp-app.`);
const fixture = ((await loadFixture()) as { default: Fixture }).default;
const { arguments: toolArguments, ...result } = fixture;

// Advertise text messages so pages show their "send to the conversation" buttons.
const bridge = new AppBridge(
  null,
  { name: "race-engineer-dev-host", version: "0.1.0" },
  { message: { text: {} } },
  { hostContext: { theme: "light", displayMode: "inline" } },
);

type ToolResult = Parameters<typeof bridge.sendToolResult>[0];
type Message = Parameters<NonNullable<typeof bridge.onmessage>>[0];

const messageLog = document.getElementById("log")!;

/** Show a message the page sent to the conversation (text blocks only; others are named). */
function log(message: Message): void {
  const text = message.content
    .map((block) => (block.type === "text" ? block.text : `[${block.type}]`))
    .join(" ");
  const entry = document.createElement("li");
  entry.textContent = `${message.role}: ${text}`;
  messageLog.append(entry);
  document.getElementById("messages")!.hidden = false;
  console.info("sendMessage", message);
}

bridge.onmessage = async (message) => {
  log(message);
  return {};
};

bridge.oninitialized = () => {
  if (toolArguments) void bridge.sendToolInput({ arguments: toolArguments });
  // JSON imports widen literal types ("text" -> string), so restate the result's type.
  void bridge.sendToolResult(result as ToolResult);
};
bridge.onsizechange = ({ height }) => {
  if (height) iframe.height = String(Math.ceil(height));
};

function setTheme(theme: "light" | "dark") {
  document.body.classList.toggle("dark", theme === "dark");
  void bridge.sendHostContextChange({ theme });
}
document.getElementById("light")!.onclick = () => setTheme("light");
document.getElementById("dark")!.onclick = () => setTheme("dark");
document.getElementById("wide")!.onclick = () => iframe.classList.remove("narrow");
document.getElementById("narrow")!.onclick = () => iframe.classList.add("narrow");

// Listen before loading the app so its initialize request isn't missed.
await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
iframe.src = `./${app}.html`;
