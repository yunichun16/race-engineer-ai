import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";
import { describe, test } from "node:test";
import { EXTERNAL_REL, REPO_TITLE, resolveImage, resolveLink } from "./links.ts";
import { FIGURES } from "./figures.ts";
import { REPORTS, reportBySlug } from "./manifest.ts";
import { collectLinks, parseReport, type Block } from "./markdown.ts";
import { applyOmission, type PageDoc } from "./omit.ts";
import { FIGURES_DIR, figureFiles, readReport, repoFileExists } from "./testing.ts";

describe("resolveLink", () => {
  test("a published report becomes its page, keeping an anchor", () => {
    assert.deepEqual(resolveLink("m3_results.md"), { kind: "report", href: "/report/m3-results", slug: "m3-results", hash: "" });
    assert.deepEqual(resolveLink("./m3_summary.md#findings"), {
      kind: "report",
      href: "/report/m3-summary#findings",
      slug: "m3-summary",
      hash: "#findings",
    });
    assert.deepEqual(resolveLink("/report/m3_style_within_season.md"), {
      kind: "report",
      href: "/report/m3-style-within-season",
      slug: "m3-style-within-season",
      hash: "",
    });
  });

  test("a figure becomes the figure route", () => {
    assert.deepEqual(resolveLink("figures/style_map.png"), { kind: "figure", href: "/report/figures/style_map.png", file: "style_map.png" });
  });

  test("an anchor stays on the page", () => {
    assert.deepEqual(resolveLink("#limits"), { kind: "anchor", href: "#limits", id: "limits" });
  });

  test("http and https go out", () => {
    assert.deepEqual(resolveLink("https://github.com/theOehrly/Fast-F1"), { kind: "external", href: "https://github.com/theOehrly/Fast-F1" });
    assert.deepEqual(resolveLink("https://en.wikipedia.org/wiki/Monza_(circuit)"), {
      kind: "external",
      href: "https://en.wikipedia.org/wiki/Monza_(circuit)",
    });
    assert.equal(resolveLink("http://example.com").kind, "external");
    assert.equal(EXTERNAL_REL, "noopener noreferrer");
  });

  test("other files are repository references, linked on GitHub once a repository URL is given", () => {
    assert.deepEqual(resolveLink("m4_review_queue.csv"), { kind: "repo", path: "report/m4_review_queue.csv", href: null });
    assert.deepEqual(resolveLink("../engine/scripts/review_queue.py"), { kind: "repo", path: "engine/scripts/review_queue.py", href: null });
    assert.deepEqual(resolveLink("m3_ablations_smoke.md"), { kind: "repo", path: "report/m3_ablations_smoke.md", href: null });
    // The human review isn't published (plan 13), so a link to it is a file in the repository.
    assert.deepEqual(resolveLink("m4_review.md"), { kind: "repo", path: "report/m4_review.md", href: null });
    assert.deepEqual(resolveLink("../engine/x.py#L3", "https://github.com/o/race-engineer/"), {
      kind: "repo",
      path: "engine/x.py",
      href: "https://github.com/o/race-engineer/blob/main/engine/x.py#L3",
    });
    assert.equal(REPO_TITLE, "In the repository");
  });

  test("addresses that must never become links", () => {
    for (const href of ["", "#", "javascript:alert(1)", "mailto:a@b.c", "data:text/html,x", "//evil.example/x", "../../etc/passwd", "a.md?x=1"]) {
      assert.equal(resolveLink(href).kind, "invalid", href);
    }
  });

  test("images must be report figures", () => {
    assert.equal(resolveImage("figures/shift_finetune.png").kind, "figure");
    assert.equal(resolveImage("https://example.com/x.png").kind, "invalid");
    assert.equal(resolveImage("m3_results.md").kind, "invalid");
    assert.equal(resolveImage("figures/x.svg").kind, "invalid");
  });
});

describe("every link in the published reports resolves", () => {
  // The pages as the site renders them, omitted parts taken out: their links and their anchors.
  const docs = new Map<string, PageDoc>(REPORTS.map((r) => [r.slug, applyOmission(parseReport(readReport(r.file)), r.omit)]));
  const ids = (slug: string) => new Set(docs.get(slug)?.headings.map((h) => h.id));

  for (const { file, slug } of REPORTS) {
    test(file, () => {
      const problems: string[] = [];
      const doc = docs.get(slug)!;
      // The title and the generated line are taken out of `blocks`, so check their links too.
      const lead: Block = { type: "paragraph", children: [...doc.titleInlines, ...(doc.meta ?? [])] };
      const blocks = doc.blocks.filter((b): b is Block => b.type !== "omitted");
      for (const { href, image } of collectLinks([lead, ...blocks])) {
        const target = image ? resolveImage(href) : resolveLink(href);
        const bad = (why: string) => problems.push(`${href}: ${why}`);
        if (target.kind === "invalid") bad(target.reason);
        else if (target.kind === "report") {
          if (!reportBySlug(target.slug)) bad("not in the manifest");
          if (target.hash && !ids(target.slug).has(target.hash.slice(1))) bad(`no heading ${target.hash} in ${target.slug}`);
        } else if (target.kind === "figure") {
          if (!existsSync(path.join(FIGURES_DIR, target.file))) bad("no such figure in report/figures");
        } else if (target.kind === "anchor") {
          if (!ids(slug).has(target.id)) bad("no heading with that id in this file");
        } else if (target.kind === "repo") {
          if (!repoFileExists(target.path)) bad(`no file ${target.path} in the repository`);
        }
      }
      assert.deepEqual(problems, []);
    });
  }

  test("the figures folder holds the 7 PNGs the figure route serves", () => {
    assert.deepEqual(figureFiles(), [
      "labelled_mistake.png",
      "mae_reconstruction.png",
      "shift_finetune.png",
      "style_map.png",
      "teammates_corner.png",
      "teammates_lap_delta.png",
      "track_map_speed.png",
    ]);
  });

  test("the figure list describes exactly those files, each linked to a published report or a script that exists", () => {
    assert.deepEqual(FIGURES.map((f) => f.file).sort(), figureFiles());
    for (const f of FIGURES) {
      assert.ok(f.title.length > 0 && f.alt.length > 40, `${f.file} has a title and real alt text`);
      if (f.report !== undefined) assert.ok(reportBySlug(f.report), `${f.file}: ${f.report} is published`);
      assert.ok(repoFileExists(f.madeBy), `${f.file}: ${f.madeBy} exists`);
    }
  });
});
