# The demo video

A silent video of about 75 seconds, with captions, filmed from the running site by a script: the
landing page, a corner explained, a chat question with its chart, the mistake explorer, the
driving styles and the report, then an end card. It writes `data/demo/race-engineer-demo.mp4`
(1280 × 720, H.264, 30 frames a second, no audio).

The video shows real circuits and replays of the cars' positions drawn from F1 data, so it never
goes in the repository: `data/` is gitignored, and the video can be attached to the README through
GitHub's web editor instead (drag the file into the editor; GitHub hosts it).

## Rendering it

It needs a Mac with Google Chrome, Swift (Xcode, or the Command Line Tools:
`xcode-select --install`) and Node.js 22.18 or later. No packages and no ffmpeg: Node drives
Chrome over the DevTools protocol, and AVFoundation writes the video.

```bash
make demo-video SITE=http://localhost:3000 API=http://127.0.0.1:8000
# the same, from web/:
node scripts/demo/record.ts --site http://localhost:3000 --api http://127.0.0.1:8000
```

`--site` is the site to film and `--api` the API that site calls (its `NEXT_PUBLIC_API_URL`),
checked before anything is filmed. Filming takes about 80 seconds and encoding about 10.

| Option | What it does |
|---|---|
| `--out <dir>` | where everything goes (default: the repository's `data/demo/`) |
| `--label <text>` | the address the end card shows (default: the site's host) |
| `--skip <scenes>` | leaves scenes out, by id, comma-separated: `hero`, `story`, `chat`, `mistakes`, `styles`, `report`, `end` |
| `--bitrate <bits/s>` | the video's average bit rate (default 2,500,000) |
| `--keep-frames` | keeps the captured frames and their timing file |
| `--no-encode` | films only: the frames, `frames/timing.json` and `captions.json` |
| `--chrome <path>` | another Chrome binary (or `CHROME_PATH`) |

Which site to film:

| Render | Site and API | The chat scene |
|---|---|---|
| A local draft | `make web` and `make api-fake` | the scripted chat; its caption says "the chat", not Claude |
| The production-like draft | `make web-prodlike` and `make api-prodlike` (ports 3001 and 8001) | the scripted chat |
| The final video | the live site and API: `make demo-video SITE=$SITE API=$API` | one real question, about $0.02, and one of this connection's 10 questions that hour |

When the hour's questions are used up, `--skip chat` films the rest (55 seconds).

**What it writes,** all under `--out`: `race-engineer-demo.mp4`; `captions.json`; `stills/`, a
PNG from the finished file in the middle of each caption and in the last second, for a look
without a player; and, while it runs, the frames and a throwaway Chrome profile (the profile is
deleted at the end of every run, the frames after a good one). The last line it prints is what
AVFoundation reads back from the file: length, size, codec and the number of audio tracks (0).
To check a video yourself, open it in QuickTime Player, or
`mdls -name kMDItemDurationSeconds data/demo/race-engineer-demo.mp4`.

**When a step fails,** it exits with 1, names the scene and the step, and saves the page as it
was to `failed.jpg`. Every step waits for what the page shows (a chart drawn, a button enabled),
up to 90 seconds for data, 2 minutes for a chat answer and 2 minutes for a page's first compile
under `next dev`, so the usual causes are an API that isn't running, a chat that refuses the
question (rate-limited or paused), or a page whose data has changed. Before filming it checks
the API's health: an API that isn't answering, or a chat that is off, paused or at today's cap,
stops it at once with nothing written (`--skip chat` films the rest).

## The storyboard

The same storyboard drives the script (`web/scripts/demo/storyboard.ts`) and the recording by
hand below.

| Time | Scene | On screen | Caption |
|---|---|---|---|
| 0:00-0:07 | `hero` | The landing page. The hero's real flagged corners turn (each stays 4.5 s). | Deep learning on F1 telemetry finds the corners where drivers lost time. |
| 0:07-0:19 | `story` | Down through the story's three steps to the real `explain_corner` chart; its replay of the cars around plays. | Each flagged corner is explained against the driver's usual lap, with a replay of the cars around. |
| 0:19-0:39 | `chat` | "Who made the biggest mistakes in the last race?" typed and sent; the answer and its chart; Show telemetry on the first row; the corner's chart; Play on its replay. | Ask in plain words: Claude picks the analysis tools and answers with charts. |
| 0:39-0:50 | `mistakes` | The explorer on the 2026 Spanish Grand Prix race: the ranked list and the map, then ALB's lap 55, turn 3 opened (the first row if that one has gone), down to its replay. | Explore any session since 2022, corner by corner. |
| 0:50-1:01 | `styles` | Hamilton and Leclerc in 2025: the differences by corner type, the Qualifying filter, the style map. | Compare how teammates drive the same car. |
| 1:01-1:10 | `report` | The report's detector comparison, switched to the 2026 test events. | Checked against simple baselines, with intervals and limits stated. |
| 1:10-1:15 | `end` | The end card. | none |

**The end card,** faded in over the last page: the site's logo (the glowing lime dot), "Race
Engineer AI", the site's address, "Also inside Claude as a connector", "Unofficial fan project.
Findings can be wrong.", and "Built by Yuchun Wu · github.com/yunichun16 ·
linkedin.com/in/yuchun-wu". The name, the fan-project line and the credit come from
`web/src/content/site.ts`, so the card follows the site.

**How the script films it.** `record.ts` starts Chrome headless at 1280 × 720 in the Dark theme
(the site's default), with a throwaway profile, and plays the storyboard step by step over the
DevTools protocol (`cdp.ts`, on Node's built-in WebSocket). Frames come from Chrome's screencast,
up to 30 a second and only when the page repaints, each placed at the moment it was drawn. Page
loads and the rest of a long wait happen off camera: the video's clock stops, so a slow compile
or a long answer becomes a cut. A real answer streams on camera for up to 4 seconds before the
cut. Headless Chrome draws no pointer, so the script draws a soft ring that glides to each thing
it clicks. `encode.swift` then writes the video at 30 frames a second, holding each frame until
the next, with a 0.4-second cross-fade between scenes and each caption drawn with Core Text on a
bar of the site's dark glass.

**Changing it.** Edit `storyboard.ts`. Each scene has a length, and its steps must fit in it
even when every on-camera wait runs to the end, so the video keeps its length whichever chat
answers; the tests check that, the captions and the end card:

```bash
cd web && node --test scripts/demo/captions.test.ts
```

## Recording it by hand (QuickTime)

For a narrated version, for the Claude connector scene, or when the script can't run.

**Before recording**
1. Open the site in Chrome in a new window. Hide the bookmarks bar (Cmd-Shift-B), set the zoom to
   100% (Cmd-0) and pick the Dark theme (the moon in the header).
2. Open the five pages in tabs, in this order, and let each load once (the first visit is the
   slow one):
   1. `/`
   2. `/chat`
   3. `/mistakes?year=2026&event=Spanish+Grand+Prix&session=R`
   4. `/styles?a=HAM&b=LEC&year=2025`
   5. `/report`

   Switching tabs (Cmd-Option-Right) then shows on camera as a cut, with no address typed. Go
   back to the first tab and reload it.
3. Make the window a little larger than 1280 × 720, turn on Do Not Disturb, and move the Dock out
   of the way.
4. Press Cmd-Shift-5 (or QuickTime Player's File → New Screen Recording), choose Record Selected
   Portion, and drag a 1280 × 720 frame over the page below the address bar (the size shows
   while dragging; on a Retina screen the file comes out at 2560 × 1440, which is fine). Under
   Options, choose a microphone only for narration, and turn on Show Mouse Clicks.

**Recording** (the times are the storyboard's; being close is enough)
1. **0:00, the landing page.** Press Record and wait about 6 seconds, until the hero turns to its
   next corner. Keep the pointer off the hero's card: hovering pauses it.
2. **0:07, the story.** Scroll slowly down through the three steps to the chart, then a little
   further, until the replay below the chart plays.
3. **0:19, the chat** (next tab). Click the question box, type "Who made the biggest mistakes in
   the last race?" and press Send. When the chart has come in, scroll to it, click Show
   telemetry on the first row, scroll down to the corner's replay and press Play.
4. **0:39, the explorer** (next tab). Scroll to "Flagged mistakes", click Show telemetry on ALB's
   lap 55, turn 3 (or the first row), and scroll down to its replay.
5. **0:50, the styles** (next tab). Scroll to "HAM and LEC, 2025", click Qualifying, then scroll
   to the Style map.
6. **1:01, the report** (next tab). Scroll to "The baseline ranks better overall; the Transformer
   is sharper at the top" and click "2026 test events".
7. **1:10, stop** with the stop button in the menu bar (or Cmd-Ctrl-Esc).

**The end card.** The script films the end card on its own as a 5-second clip:

```bash
cd web && node scripts/demo/record.ts --site $SITE --api $API --skip hero,story,chat,mistakes,styles,report --out ../data/demo/end-card
```

**Trimming and joining** in QuickTime Player: open the recording, Edit → Trim (Cmd-T) to cut the
start and the end; Edit → Add Clip to End to add `data/demo/end-card/race-engineer-demo.mp4`;
then File → Export As → 1080p. QuickTime adds no captions: narrate instead, or put the
storyboard's captions on as titles in iMovie.

**The Claude connector scene** (by hand only: the Claude app can't be scripted). With the
connector added in Claude (Settings → Connectors → Add custom connector, the API's `/mcp`
address), start a new chat with it enabled and ask "Who made the biggest mistakes in the last
race?". Record the answer and its chart with the same Cmd-Shift-5 frame, and add the clip before
the end card with Add Clip to End.
