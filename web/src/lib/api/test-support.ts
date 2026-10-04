// Stand-ins for fetch in lib/api's tests (node --test: no DOM, no packages). Not imported by the
// site.

export interface FetchCall {
  url: string;
  init: RequestInit | undefined;
  self: unknown; // `this` inside the call: a browser's fetch needs it to be nothing or window
}

type Handler = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

/** A fetch that answers with `handler` and records each call. */
export function fakeFetch(handler: Handler): typeof fetch & { calls: FetchCall[] } {
  const calls: FetchCall[] = [];
  const fake = async function (this: unknown, input: RequestInfo | URL, init?: RequestInit) {
    calls.push({ url: String(input), init, self: this });
    return handler(String(input), init);
  };
  return Object.assign(fake as typeof fetch, { calls });
}

/** A JSON response, as the API sends them. */
export function json(body: unknown, status = 200, statusText = ""): Response {
  return new Response(JSON.stringify(body), { status, statusText, headers: { "content-type": "application/json" } });
}

/** The API's error body. */
export function errorBody(code: string, message: string, tool: string | null = null): { error: object } {
  return { error: { code, message, tool } };
}

/** A promise with its resolve and reject in hand. */
export function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (error: unknown) => void } {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** A fetch that never answers, and rejects with an AbortError when its signal fires, as browsers do. */
export function hangingFetch(): typeof fetch & { calls: FetchCall[] } {
  return fakeFetch(
    (_url, init) =>
      new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        const abort = () => reject(new DOMException("The operation was aborted.", "AbortError"));
        if (signal?.aborted) abort();
        else signal?.addEventListener("abort", abort, { once: true });
      }),
  );
}

/** Lets every pending promise callback run. */
export function settle(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}
