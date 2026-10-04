import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { pngSize } from "./png.ts";
import { FIGURES_DIR, figureFiles } from "./testing.ts";

/** The first 24 bytes of a PNG of the given size. */
function header(width: number, height: number, chunk = "IHDR"): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13);
  bytes.set([...chunk].map((c) => c.charCodeAt(0)), 12);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return bytes;
}

test("reads width and height from the header", () => {
  assert.deepEqual(pngSize(header(1200, 675)), { width: 1200, height: 675 });
  assert.deepEqual(pngSize(header(70000, 1)), { width: 70000, height: 1 });
});

test("works on a view into a larger buffer (as fs gives pooled Buffers)", () => {
  const big = new Uint8Array(100);
  big.set(header(640, 480), 30);
  assert.deepEqual(pngSize(big.subarray(30)), { width: 640, height: 480 });
});

test("refuses anything that isn't a PNG", () => {
  assert.throws(() => pngSize(new Uint8Array(10)), /shorter than its header/);
  const jpeg = header(1, 1);
  jpeg.set([0xff, 0xd8, 0xff, 0xe0]);
  assert.throws(() => pngSize(jpeg), /wrong signature/);
  assert.throws(() => pngSize(header(1, 1, "IDAT")), /IHDR chunk is not first/);
  assert.throws(() => pngSize(header(0, 10)), /zero width or height/);
});

test("every report figure has a size", () => {
  for (const file of figureFiles()) {
    const { width, height } = pngSize(readFileSync(path.join(FIGURES_DIR, file)));
    assert.ok(Number.isInteger(width) && width > 0 && Number.isInteger(height) && height > 0, file);
  }
});
