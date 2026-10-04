// Builds each MCP App chart page (src/mcp-app/<page>.html) into one self-contained HTML file
// that the Python MCP server serves as a ui:// resource. vite-plugin-singlefile inlines a
// single entry per build, so MCP_APP picks the page and `npm run build:mcp-app` builds every
// page in turn. `npm run dev:mcp-app` serves all pages plus the dev host (dev-host.html).
import { resolve } from "node:path";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

const here = import.meta.dirname;
const PAGES = [
  "telemetry",
  "compare-laps",
  "find-mistakes",
  "explain-corner",
  "compare-styles",
  "race-summary",
];

export default defineConfig(({ command }) => {
  const page = process.env.MCP_APP;
  if (command === "build" && !PAGES.includes(page ?? "")) {
    throw new Error(`Set MCP_APP to one of: ${PAGES.join(", ")} (npm run build:mcp-app builds all)`);
  }
  const input = resolve(here, `src/mcp-app/${page}.html`);
  return {
    root: resolve(here, "src/mcp-app"),
    plugins: [viteSingleFile()],
    build: {
      outDir: resolve(here, "../engine/src/race_engineer/mcp_server/ui"),
      emptyOutDir: false, // the pages build one after another into the same folder
      // Only when building: in dev, Vite scans every page in root for its dependencies.
      ...(command === "build" && { rollupOptions: { input } }),
    },
  };
});
