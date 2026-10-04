// Turns the demo recorder's frames into an H.264 MP4 with the captions drawn on (plan 12). Swift
// and AVFoundation only: no ffmpeg, no packages.
//
//   swift encode.swift <frames-dir> <captions.json> <out.mp4> [--bitrate 2500000] [--stills <dir>]
//
// <frames-dir>/timing.json (written by record.ts, shaped by captions.ts) lists every captured
// frame with its time in the video; a frame shows until the next one starts. The output runs at
// the timing file's frame rate, repeating frames as their times say, with a short cross-fade at
// each scene boundary. Each caption is drawn with Core Text on a translucent bar near the foot of
// the frame, fading in and out with its scene. No audio.
//
// Afterwards it opens the file again with AVFoundation and prints what it reads back (length,
// size, codec) as one JSON line, so the caller checks the video itself, not just the exit code.
// With --stills it also saves a PNG from the finished file at the middle of each caption and in
// the last second, for a look without a player.

import AppKit
import AVFoundation
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

struct Frame: Decodable { let t: Double; let file: String }
struct SceneTiming: Decodable { let id: String; let start: Double; let end: Double }
struct Timing: Decodable {
  let v: Int
  let width: Int
  let height: Int
  let fps: Int
  let duration: Double
  let frames: [Frame]
  let scenes: [SceneTiming]
  let cuts: [Double]
}
struct Caption: Decodable { let start: Double; let end: Double; let text: String }

func fail(_ message: String) -> Never {
  FileHandle.standardError.write(("encode.swift: " + message + "\n").data(using: .utf8)!)
  exit(1)
}

// ---- Arguments ----------------------------------------------------------------------------------

var positional: [String] = []
var bitrate = 2_500_000
var stillsDir: URL?
var args = CommandLine.arguments.dropFirst().makeIterator()
while let arg = args.next() {
  switch arg {
  case "--bitrate":
    guard let value = args.next().flatMap(Int.init), value > 0 else { fail("--bitrate takes bits per second") }
    bitrate = value
  case "--stills":
    guard let value = args.next() else { fail("--stills takes a folder") }
    stillsDir = URL(fileURLWithPath: value)
  default:
    positional.append(arg)
  }
}
guard positional.count == 3 else {
  fail("usage: swift encode.swift <frames-dir> <captions.json> <out.mp4> [--bitrate N] [--stills <dir>]")
}
let framesDir = URL(fileURLWithPath: positional[0])
let captionsURL = URL(fileURLWithPath: positional[1])
let outURL = URL(fileURLWithPath: positional[2])

let timing: Timing
let captions: [Caption]
do {
  timing = try JSONDecoder().decode(Timing.self, from: Data(contentsOf: framesDir.appendingPathComponent("timing.json")))
  captions = try JSONDecoder().decode([Caption].self, from: Data(contentsOf: captionsURL))
} catch {
  fail("couldn't read the timing or captions file: \(error)")
}
guard timing.v == 1, !timing.frames.isEmpty, timing.duration > 0 else { fail("timing.json has no frames or no length") }

let width = timing.width
let height = timing.height
let fps = Int32(timing.fps)
let total = Int((timing.duration * Double(fps)).rounded(.up))

// ---- Look ---------------------------------------------------------------------------------------

// The site's dark theme (web/src/styles/tokens.css): --color-text-primary on the header's glass
// (--glass-nav, a little more opaque so the words stay readable over a busy chart) with its faint
// --edge (a touch stronger, so the bar holds its shape over the page). A cross-fade of 0.4 s at
// scene boundaries; captions fade in over 0.35 s and out over 0.25 s.
let ink = CGColor(srgbRed: 0xee / 255, green: 0xf2 / 255, blue: 0xf8 / 255, alpha: 1)
let barFill = CGColor(srgbRed: 19 / 255, green: 26 / 255, blue: 44 / 255, alpha: 0.86)
let barEdge = CGColor(srgbRed: 215 / 255, green: 222 / 255, blue: 234 / 255, alpha: 0.14)
let barShadow = CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0.45)
let captionFont = NSFont.systemFont(ofSize: 25, weight: .medium)
let crossFade = 0.4
let fadeIn = 0.35
let fadeOut = 0.25
let maxTextWidth = 1000.0
let padX = 30.0
let padY = 16.0
let bottomMargin = 44.0

// ---- Drawing ------------------------------------------------------------------------------------

func loadImage(_ file: String) -> CGImage {
  let url = framesDir.appendingPathComponent(file)
  guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
        let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("couldn't read frame \(file)") }
  return image
}

func smooth(_ x: Double) -> Double {
  let c = min(max(x, 0), 1)
  return c * c * (3 - 2 * c)
}

/** The caption's text laid out once: its framesetter and the size it needs. */
struct Laid { let setter: CTFramesetter; let size: CGSize }
var laidOut: [String: Laid] = [:]
func layout(_ text: String) -> Laid {
  if let cached = laidOut[text] { return cached }
  let paragraph = NSMutableParagraphStyle()
  paragraph.alignment = .center
  paragraph.lineSpacing = 4
  let string = NSAttributedString(string: text, attributes: [
    .font: captionFont,
    .foregroundColor: NSColor(cgColor: ink)!,
    .paragraphStyle: paragraph,
  ])
  let setter = CTFramesetterCreateWithAttributedString(string)
  let size = CTFramesetterSuggestFrameSizeWithConstraints(
    setter, CFRange(location: 0, length: 0), nil, CGSize(width: maxTextWidth, height: .greatestFiniteMagnitude), nil)
  let laid = Laid(setter: setter, size: CGSize(width: ceil(size.width), height: ceil(size.height)))
  laidOut[text] = laid
  return laid
}

func drawCaption(_ text: String, alpha: Double, in ctx: CGContext) {
  let laid = layout(text)
  let bar = CGRect(
    x: (Double(width) - laid.size.width) / 2 - padX, y: bottomMargin,
    width: laid.size.width + 2 * padX, height: laid.size.height + 2 * padY)
  ctx.saveGState()
  ctx.setAlpha(alpha)
  ctx.beginTransparencyLayer(auxiliaryInfo: nil)
  let shape = CGPath(roundedRect: bar, cornerWidth: 18, cornerHeight: 18, transform: nil)
  ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 30, color: barShadow)
  ctx.addPath(shape)
  ctx.setFillColor(barFill)
  ctx.fillPath()
  ctx.setShadow(offset: .zero, blur: 0, color: nil)
  ctx.addPath(shape)
  ctx.setStrokeColor(barEdge)
  ctx.setLineWidth(1)
  ctx.strokePath()
  let textRect = CGRect(x: bar.minX + padX, y: bar.minY + padY, width: laid.size.width, height: laid.size.height)
  let frame = CTFramesetterCreateFrame(laid.setter, CFRange(location: 0, length: 0), CGPath(rect: textRect, transform: nil), nil)
  CTFrameDraw(frame, ctx)
  ctx.endTransparencyLayer()
  ctx.restoreGState()
}

// ---- Writing ------------------------------------------------------------------------------------

try? FileManager.default.removeItem(at: outURL)
let writer: AVAssetWriter
do { writer = try AVAssetWriter(outputURL: outURL, fileType: .mp4) } catch { fail("couldn't create \(outURL.path): \(error)") }
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
  AVVideoCodecKey: AVVideoCodecType.h264,
  AVVideoWidthKey: width,
  AVVideoHeightKey: height,
  AVVideoCompressionPropertiesKey: [
    AVVideoAverageBitRateKey: bitrate,
    AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
    AVVideoMaxKeyFrameIntervalKey: Int(fps) * 2,
  ],
])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
  kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
  kCVPixelBufferWidthKey as String: width,
  kCVPixelBufferHeightKey as String: height,
])
guard writer.canAdd(input) else { fail("the writer can't take an H.264 track") }
writer.add(input)
guard writer.startWriting() else { fail("couldn't start writing: \(String(describing: writer.error))") }
writer.startSession(atSourceTime: .zero)

let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
let full = CGRect(x: 0, y: 0, width: width, height: height)
var index = 0 // the source frame showing now
var current = loadImage(timing.frames[0].file)
var beforeCut: CGImage? // the last frame before the cut being faded across
var cut = 0 // the next cut to reach
let started = Date()

for i in 0..<total {
  let t = Double(i) / Double(fps)
  // The frame for time t: the last one that starts at or before it.
  var moved = false
  while index + 1 < timing.frames.count && timing.frames[index + 1].t <= t + 1e-6 {
    index += 1
    moved = true
  }
  // Passing a cut: keep what showed just before it, to fade from.
  while cut < timing.cuts.count && timing.cuts[cut] <= t + 1e-6 {
    beforeCut = current
    cut += 1
  }
  if moved { current = loadImage(timing.frames[index].file) }

  guard let pool = adaptor.pixelBufferPool else { fail("no pixel buffer pool: \(String(describing: writer.error))") }
  var buffer: CVPixelBuffer?
  CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer)
  guard let pixels = buffer else { fail("couldn't get a pixel buffer") }
  CVPixelBufferLockBaseAddress(pixels, [])
  guard let ctx = CGContext(
    data: CVPixelBufferGetBaseAddress(pixels), width: width, height: height, bitsPerComponent: 8,
    bytesPerRow: CVPixelBufferGetBytesPerRow(pixels), space: colorSpace,
    bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
  else { fail("couldn't draw into a pixel buffer") }
  ctx.interpolationQuality = .high
  ctx.draw(current, in: full)

  if cut > 0, let previous = beforeCut {
    let since = t - timing.cuts[cut - 1]
    if since < crossFade {
      ctx.setAlpha(1 - smooth(since / crossFade))
      ctx.draw(previous, in: full)
      ctx.setAlpha(1)
    }
  }
  if let caption = captions.first(where: { $0.start <= t && t < $0.end }) {
    let alpha = min(1, (t - caption.start) / fadeIn, (caption.end - t) / fadeOut)
    if alpha > 0 { drawCaption(caption.text, alpha: smooth(alpha), in: ctx) }
  }
  CVPixelBufferUnlockBaseAddress(pixels, [])

  while !input.isReadyForMoreMediaData { usleep(2000) }
  guard adaptor.append(pixels, withPresentationTime: CMTime(value: CMTimeValue(i), timescale: fps)) else {
    fail("couldn't append frame \(i): \(String(describing: writer.error))")
  }
}

input.markAsFinished()
await writer.finishWriting() // not a semaphore wait: Swift 6 refuses those in top-level async code
guard writer.status == .completed else { fail("writing failed: \(String(describing: writer.error))") }
let encodeSeconds = Date().timeIntervalSince(started)

// ---- Reading it back ----------------------------------------------------------------------------

let asset = AVURLAsset(url: outURL)
let duration = try await asset.load(.duration)
let tracks = try await asset.loadTracks(withMediaType: .video)
guard let track = tracks.first else { fail("the written file has no video track") }
let size = try await track.load(.naturalSize)
let formats = try await track.load(.formatDescriptions)
let codec = formats.first.map { description -> String in
  let code = CMFormatDescriptionGetMediaSubType(description)
  return String(bytes: [24, 16, 8, 0].map { UInt8((code >> $0) & 0xff) }, encoding: .ascii) ?? "?"
} ?? "?"
let audio = try await asset.loadTracks(withMediaType: .audio).count
let bytes = (try? FileManager.default.attributesOfItem(atPath: outURL.path)[.size] as? Int) ?? 0

var stills: [String] = []
if let dir = stillsDir {
  try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
  let generator = AVAssetImageGenerator(asset: asset)
  generator.requestedTimeToleranceBefore = .zero
  generator.requestedTimeToleranceAfter = .zero
  var times = captions.map { ($0.start + $0.end) / 2 }
  times.append(max(0, duration.seconds - 0.5))
  for (n, seconds) in times.enumerated() {
    let image = try await generator.image(at: CMTime(seconds: seconds, preferredTimescale: 600)).image
    let url = dir.appendingPathComponent(String(format: "still-%02d-%05.1fs.png", n + 1, seconds))
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else { continue }
    CGImageDestinationAddImage(dest, image, nil)
    if CGImageDestinationFinalize(dest) { stills.append(url.path) }
  }
}

let report: [String: Any] = [
  "file": outURL.path,
  "duration_s": (duration.seconds * 1000).rounded() / 1000,
  "width": Int(size.width),
  "height": Int(size.height),
  "codec": codec,
  "fps": Int(fps),
  "frames_written": total,
  "frames_captured": timing.frames.count,
  "audio_tracks": audio,
  "bytes": bytes,
  "encode_s": (encodeSeconds * 10).rounded() / 10,
  "stills": stills,
]
let line = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
print(String(data: line, encoding: .utf8)!)
