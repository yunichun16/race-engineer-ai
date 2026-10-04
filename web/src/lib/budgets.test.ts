import { test } from "node:test";
import assert from "node:assert/strict";
import { BUDGETS, chunkFile, evaluate, formatTable, htmlFileFor, kib, limitFor, scriptChunks, SLUG_PAGE } from "./budgets.ts";

// A trimmed prerendered page as `next build` writes it: async chunks, the noModule polyfill, the
// inline theme script and an RSC payload, a preload link, and an entity in one src.
const PAGE = `<!DOCTYPE html><html lang="en"><head><meta charSet="utf-8"/>
<link rel="preload" as="script" fetchPriority="low" href="/_next/static/chunks/preload-only.js"/>
<script src="/_next/static/chunks/19mx3mg6lkumu.js" async=""></script>
<script src="/_next/static/chunks/turbopack-07omqpe_ncfpq.js" async=""></script>
<script src="/_next/static/chunks/0cz1d0mv5g_q7.js" noModule=""></script>
<script>(function(){var t;try{t=localStorage.getItem("re-theme")}catch(e){}})()</script>
</head><body><script src="/_next/static/chunks/a.js?dpl=dpl_1&amp;v=2" async=""></script>
<script src='/_next/static/chunks/3s6nzrbk-8mnv.js' id="_R_" async=""></script>
<script src="/_next/static/chunks/19mx3mg6lkumu.js" async=""></script>
<script>self.__next_f.push([1,"0:{}"])</script></body></html>`;

test("scriptChunks keeps the chunks a modern browser runs, in order and once each", () => {
  assert.deepEqual(scriptChunks(PAGE), [
    "/_next/static/chunks/19mx3mg6lkumu.js",
    "/_next/static/chunks/turbopack-07omqpe_ncfpq.js",
    "/_next/static/chunks/a.js?dpl=dpl_1&v=2",
    "/_next/static/chunks/3s6nzrbk-8mnv.js",
  ]);
});

test("scriptChunks skips noModule in any case and pages with no chunks", () => {
  assert.deepEqual(scriptChunks('<script nomodule src="/_next/x.js"></script><script NOMODULE="" src="/_next/y.js"></script>'), []);
  assert.deepEqual(scriptChunks("<html><body><script>1</script></body></html>"), []);
});

test("chunkFile maps a chunk to the build directory and refuses anything else", () => {
  assert.equal(chunkFile("/_next/static/chunks/a.js"), "static/chunks/a.js");
  assert.equal(chunkFile("/_next/static/chunks/a.js?dpl=dpl_1&v=2"), "static/chunks/a.js");
  assert.equal(chunkFile("/_next/static/chunks/a.js#x"), "static/chunks/a.js");
  assert.equal(chunkFile("https://cdn.example.com/a.js"), null);
  assert.equal(chunkFile("/static/a.js"), null);
  assert.equal(chunkFile("/_next/../server/app/index.html"), null);
  assert.equal(chunkFile("/_next/"), null);
});

test("htmlFileFor names the prerendered file of a page", () => {
  assert.equal(htmlFileFor("/"), "index.html");
  assert.equal(htmlFileFor("/chat"), "chat.html");
  assert.equal(htmlFileFor("/report/m4-summary"), "report/m4-summary.html");
  assert.equal(htmlFileFor("/report/"), "report.html");
});

test("the limits are the M6 baseline plus 5%, rounded up (decision 11 with Gate M's numbers)", () => {
  assert.deepEqual(
    BUDGETS.map((b) => [b.page, b.baseline, b.limit]),
    [
      ["/", 185.8, 196],
      ["/chat", 194.3, 205],
      ["/mistakes", 190.7, 201],
      ["/styles", 191.5, 202],
      ["/report", 151.1, 159],
      [SLUG_PAGE, 139.1, 147],
    ],
  );
  assert.equal(limitFor(200), 210); // a whole number stays whole: no float creeping over it
  assert.equal(limitFor(100.1), 106);
});

test("evaluate passes a page at its limit, fails one a byte over, and fails one not measured", () => {
  const budgets = [
    { page: "/", baseline: 10, limit: 11 },
    { page: "/chat", baseline: 10, limit: 11 },
    { page: "/styles", baseline: 10, limit: 11 },
  ];
  const rows = evaluate(
    [
      { page: "/", path: "/", bytes: 11 * 1024, chunks: 3 },
      { page: "/chat", path: "/chat", bytes: 11 * 1024 + 1, chunks: 4 },
    ],
    budgets,
  );
  assert.deepEqual(
    rows.map((r) => [r.page, r.ok]),
    [
      ["/", true],
      ["/chat", false],
      ["/styles", false],
    ],
  );
  assert.equal(rows[0].kib, 11);
  assert.ok(Number.isNaN(rows[2].kib));
});

test("kib rounds to one decimal of 1,024 bytes", () => {
  assert.equal(kib(190259), 185.8);
  assert.equal(kib(0), 0);
});

test("formatTable prints every page with its result", () => {
  const rows = evaluate([{ page: SLUG_PAGE, path: "/report/m4-summary", bytes: 142_438, chunks: 7 }]);
  const table = formatTable(rows).split("\n");
  assert.equal(table.length, 1 + BUDGETS.length);
  assert.match(table[0], /^page\s+KiB\s+M6\s+limit\s+chunks$/);
  assert.match(table.at(-1) ?? "", /^\/report\/m4-summary\s+139\.1\s+139\.1\s+147\s+7\s+ok$/);
  assert.match(table[1], /^\/\s+-\s+185\.8\s+196\s+0\s+NOT MEASURED$/);
});
