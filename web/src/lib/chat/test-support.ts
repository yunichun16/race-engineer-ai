// Helpers shared by the chat tests (Node only; nothing in the app imports this file).

import { readFileSync } from "node:fs";

/** A fixture from ./fixtures as bytes (synthetic, hand-written streams; see each file). */
export function fixtureBytes(name: string): Uint8Array {
  return new Uint8Array(readFileSync(new URL(`./fixtures/${name}.sse`, import.meta.url)));
}

export function fixtureText(name: string): string {
  return new TextDecoder().decode(fixtureBytes(name));
}

export function bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

/** `data` cut at the given byte offsets. */
export function splitAt(data: Uint8Array, offsets: number[]): Uint8Array[] {
  const chunks: Uint8Array[] = [];
  let start = 0;
  for (const offset of offsets) {
    chunks.push(data.subarray(start, offset));
    start = offset;
  }
  chunks.push(data.subarray(start));
  return chunks;
}

export function everyByte(data: Uint8Array): Uint8Array[] {
  return Array.from(data, (_, i) => data.subarray(i, i + 1));
}

export interface StreamProbe {
  stream: ReadableStream<Uint8Array>;
  cancelled: () => boolean;
}

/** A body that sends `chunks`, then ends (or, with `hold`, stays open until cancelled). */
export function streamOf(chunks: Uint8Array[], options: { hold?: boolean } = {}): StreamProbe {
  let cancelled = false;
  let index = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(chunks[index++]);
        return;
      }
      if (!options.hold) {
        controller.close();
        return;
      }
      return new Promise<void>(() => {}); // never resolves: an open connection with nothing to say
    },
    cancel() {
      cancelled = true;
    },
  });
  return { stream, cancelled: () => cancelled };
}

export async function collect<T>(items: AsyncIterable<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of items) out.push(item);
  return out;
}

/** A sessionStorage stand-in. `failOver` makes setItem throw a quota error for longer values. */
export function memoryStorage(failOver = Infinity) {
  const map = new Map<string, string>();
  return {
    map,
    getItem(key: string): string | null {
      return map.has(key) ? (map.get(key) as string) : null;
    },
    setItem(key: string, value: string): void {
      if (value.length > failOver) throw new DOMException("The quota has been exceeded.", "QuotaExceededError");
      map.set(key, value);
    },
    removeItem(key: string): void {
      map.delete(key);
    },
  };
}

/** A saved-answer fixture from ./fixtures, parsed (saved-*.json: hand-written and synthetic, the
 *  Sample Grand Prix of basic.sse in the recording format of plan 4.1). */
export function savedFixture(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), "utf8")) as Record<string, unknown>;
}
