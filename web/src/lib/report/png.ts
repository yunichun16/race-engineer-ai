/**
 * The width and height of a PNG, from its header, so a report figure's `<img>` carries its real
 * size and the page doesn't shift when it loads. A PNG starts with an 8-byte signature and then
 * the IHDR chunk: 4 bytes of length (13), "IHDR", then width and height as big-endian 32-bit ints.
 */

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

export interface PngSize {
  width: number;
  height: number;
}

/** Throws if the bytes don't start like a PNG. Only the first 24 bytes are read. */
export function pngSize(bytes: Uint8Array): PngSize {
  if (bytes.length < 24) throw new Error("not a PNG: shorter than its header");
  if (SIGNATURE.some((b, k) => bytes[k] !== b)) throw new Error("not a PNG: wrong signature");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const chunk = String.fromCharCode(bytes[12], bytes[13], bytes[14], bytes[15]);
  if (view.getUint32(8) !== 13 || chunk !== "IHDR") throw new Error("not a PNG: the IHDR chunk is not first");
  const width = view.getUint32(16);
  const height = view.getUint32(20);
  if (width === 0 || height === 0) throw new Error("not a PNG: zero width or height");
  return { width, height };
}
