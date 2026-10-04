/**
 * The gallery lists every saved tool result the site has a chart for (src/mcp-app/sample-*.json
 * except telemetry), each under its own chart and with its own lazy import, and gives every
 * error result the REST error it stands for. Read as text: fixtures.ts uses the `@/` alias and
 * JSON imports, which Node doesn't resolve.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("./fixtures.ts", import.meta.url), "utf8");
const dir = new URL("../../../../mcp-app/", import.meta.url);
const samples = readdirSync(dir)
  .filter((f) => /^sample-.*\.json$/.test(f) && !f.startsWith("sample-telemetry"))
  .map((f) => f.replace(/\.json$/, ""));

test("every site fixture is in the gallery, once, with its own import", () => {
  assert.ok(samples.length >= 16, `only ${samples.length} fixtures`);
  for (const name of samples) {
    assert.equal(source.split(`name: "${name}"`).length - 1, 1, `${name} listed once`);
    assert.ok(source.includes(`import("@/mcp-app/${name}.json")`), `${name} imported`);
    const chart = name.replace(/^sample-/, "").replace(/-(driver|empty|error|traffic|noreplay|rivals|sparse|small)$/, "");
    assert.match(source, new RegExp(`name: "${name}",\\s*chart: "${chart}"`), `${name} under ${chart}`);
  }
  const listed = [...source.matchAll(/name: "(sample-[a-z-]+)"/g)].map((m) => m[1]);
  assert.deepEqual(listed.sort(), [...samples].sort());
});

test("every error result names the REST error it stands for", () => {
  for (const name of samples) {
    const data = JSON.parse(readFileSync(new URL(`${name}.json`, dir), "utf8")) as { isError?: boolean };
    const entry = source.slice(source.indexOf(`name: "${name}"`)).split(/\n  \{|\n\];/)[0];
    assert.equal(/error: /.test(entry), data.isError === true, `${name}: error entry matches isError`);
  }
});
