# CLAUDE.md

Context for Claude Code sessions working on this repository.

## What this is

A large e-ink desk/wall dashboard. A Raspberry Pi periodically pulls data from
various APIs, composes a single 1360×480 black-and-white image with Pillow,
and pushes it to a Waveshare 10.85" e-paper display over SPI. Forked from
[czuryk/Waveshare-ePaper-10.85-dashboard](https://github.com/czuryk/Waveshare-ePaper-10.85-dashboard).

**Purpose**: this fork customises the display as a gift for the maintainer's
wife, Charlotte — hence the personalised greeting widget (`GREETING_NAME` in
`main.py`). Keep that framing in mind for widget/copy decisions: this is a
personal, affectionate object, not a generic ops dashboard.

- `main.py` — the whole app: widget toggles, API config, background data
  fetch thread (`update_data_thread`), the frame composer (`render_screen`),
  and the main render/refresh loop (`main()`).
- `lib/waveshare_epd/` — the vendored Waveshare display driver
  (`epd10in85.py` drives the panel; `epdconfig.py` is the GPIO/SPI backend).
- `lib/bambulabs_api/` — vendored Bambu Lab printer client (optional widget).
- `claude.py`, `codex.py`, `antigravity.py` — usage-tracking widgets that
  shell out to read local CLI auth tokens.

## Rendering vs. driving the screen

The core architectural split: **composing the frame** (pure Python, Pillow)
runs anywhere, including Windows. **Driving the display** (pushing bytes over
SPI) only works on the Pi. Same codebase, no code changes between
environments — the entry point auto-detects which one it's running on.

### Local preview (no hardware)

```shell
python -m venv .venv
# Windows: .venv\Scripts\activate | macOS/Linux: source .venv/bin/activate
pip install -r requirements-preview.txt
python render_preview.py
```

Produces `output.png` (1360×480) that you can open and eyeball. `requirements-preview.txt`
is a deliberate subset of the Pi's dependencies — it skips every hardware-only
package (`spidev`, `gpiozero`, `lgpio`, `aiomqtt`, `roborock`, `paho-mqtt`);
see README.md for the full Raspberry Pi install.

### How the auto-detect / mock works (`render_preview.py`)

`lib/waveshare_epd/epdconfig.py` instantiates its GPIO/SPI backend
**unconditionally at module import time** (`implementation = RaspberryPi()`
at the bottom of the file), and that backend imports `spidev`/`gpiozero` in
its `__init__`. Neither installs on Windows. That means the crash happens
the moment `main.py` runs `from waveshare_epd import epd10in85` — long
before any drawing code, and too early for a subclass or a runtime patch to
help.

The fix has to happen at the **import** level, before that line executes:

1. `render_preview.py` first tries the real import
   (`import waveshare_epd.epd10in85`) inside a try/except. If it succeeds,
   real hardware is present (Pi with the driver deps installed) — nothing
   is mocked.
2. If it fails, `render_preview.py` injects fake modules straight into
   `sys.modules` for `RPi`, `RPi.GPIO`, `spidev`, and the whole
   `waveshare_epd` package (including `epd10in85`) *before* importing
   `main`. When `main.py` then runs its own `from waveshare_epd import
   epd10in85`, Python finds the fake module already in `sys.modules` and
   never touches the real driver files at all.
3. The fake `EPD` class mirrors the real one's surface (`init`,
   `init_Part`, `Clear`, `getbuffer`, `display`, `display_Partial`,
   `sleep`). `getbuffer()` packs the Pillow image into bytes exactly like
   the real driver; `display()`/`display_Partial()` unpack that buffer back
   into an image and save it as the output PNG instead of writing to SPI —
   so the preview exercises the same render→pack→display code path the
   real Pi loop does.
4. `render_preview.py` then imports `main`, calls `main.load_fonts()` and
   `main.render_screen(epd, fonts)` directly (skipping `main()`'s infinite
   `while True` refresh loop, which is Pi-only), and starts
   `update_data_thread` briefly first so keyless public APIs (weather,
   crypto, ping) get a chance to populate before the one frame is rendered.

`main.py` also has one Windows-incompatibility unrelated to the display: it
imported the POSIX-only `resource` module unconditionally at the top level.
That import is now guarded (`try/except ImportError`) so the file loads on
Windows; the rlimit tweak it does is a no-op there.

### Placeholders / fallbacks

Every `ENABLE_*` widget toggle in `main.py` defaults to `False`, so a fresh
checkout with no credentials configured already renders the fallback widgets
(System Load, Crypto, Internet Ping, Greeting) instead of the token-gated
ones (Strava, Bambu, Roborock, Claude/Codex/Antigravity usage, Spotify) —
this is the project's built-in graceful-degradation design, not something
the preview pipeline needs to special-case. Weather, the Gmail unread count,
and the greeting widget's affirmation line are not behind `ENABLE_*` flags:
weather and the affirmation (from the keyless `affirmations.dev` API) render
for real whenever there's network access, falling back to
`GREETING_FALLBACK_AFFIRMATIONS` if that request fails; Gmail unread quietly
stays at `0` when no `token.json` is present.

The affirmation line is API-sourced text of unpredictable length, unlike
every other string in this widget — `wrap_lines_limited()` in `main.py`
word-wraps it to at most 2 lines and ellipsis-truncates (hard-breaking by
character first, if even a single "word" is wider than the widget) whatever
doesn't fit, so a long or malformed response can never overflow into the
divider line or the Gmail widget below it.

### Middle column: Spotify now-playing, not Weather

`ENABLE_WEATHER = False` by default — the middle column shows the Spotify
now-playing widget instead (`render_screen()`'s `elif not ENABLE_WEATHER:`
branch), so Spotify and the greeting widget can be visible at the same time
rather than fighting over the same column-3 slot they originally shared.
Weather's drawing code is untouched, just gated behind the flag, so flipping
it back to `True` restores it on this slot. `SPOTIFY_ART_SIZE` (currently
190px) controls the album-art fetch/display size in one place — the fetch
code (`update_data_thread`) resizes to this before dithering, since
upscaling an already-dithered 1-bit image afterwards would smear it.

Artist/track names come from Last.fm, same "unpredictable external text"
category as the affirmation line — a plain `[:N]` character slice can still
overflow the column if the text happens to be wide characters, so the
widget reuses `wrap_lines_limited(..., max_lines=1)` to pixel-measure and
ellipsis-truncate them instead, the same helper the affirmation line uses.

### Adding a CJK (or other non-Latin) glyph to a widget

`fnt/Aldrich-Regular.ttc` and `fnt/advanced_led_board-7.ttc` (the only two
fonts the dashboard ships) are Latin-only display fonts — no CJK, no
accented Latin glyphs. Drawing an unsupported character with them doesn't
error, it silently draws nothing (verified: `draw.text(..., "你好")` renders
a blank string). Don't add a full CJK font (e.g. Noto Sans SC) to fix
this — those run 10-20MB, which is a lot to vendor into a Pi Zero project
for a handful of characters.

Instead, subset the specific glyphs you need out of a system CJK font with
`fonttools`. This container/most Linux dev boxes already have
`/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` (WenQuanYi Zen Hei — GPL-2
with a font-embedding exception, freely redistributable) installed for
headless Chromium/Playwright font rendering, and it covers Hiragana/Katakana
as well as Han, so it covers Chinese and Japanese greetings alike. Re-run
the subset with the full set of code points every time (it's cumulative,
not additive — a run with fewer `--unicodes` drops glyphs the file
previously had):

```shell
pip install fonttools
python -m fontTools.subset \
  /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
  --font-number=0 \
  --unicodes=U+55E8,U+3053,U+3093,U+306B,U+3061,U+306F \
  --glyph-names \
  --output-file=fnt/CJK-Greeting.ttf --no-hinting --desubroutinize
```

That's every glyph `fnt/CJK-Greeting.ttf` currently needs: "嗨" (U+55E8) and
"こんにちは" (U+3053, U+3093, U+306B, U+3061, U+306F) — 2.4KB total. Adding a
handful more non-Latin greeting words this way costs low-single-digit KB,
not a meaningful bump to repo size. When mixing a CJK/kana glyph into a line
of Latin text, don't align by baseline or top-left origin — the two fonts
have very different vertical metrics and it looks visibly off.
`draw_mixed_text()` in `main.py` instead
centres each (text, font) segment's own bounding box on a shared
`y_center`, which is what `render_screen()`'s greeting widget uses.

## Roadmap (not yet built — one session per item)

These are planned follow-ups; each is its own scoped session. Keep the
render/drive split in mind when touching related code, but don't build these
until asked:

1. **Todo widget** from a self-hosted Cloudflare Pages API
   (`GET /api/top-todos`), replacing an existing widget slot. Needs a shared
   secret header and the same fetch → parse → draw → fallback pattern as the
   other widgets.
2. **Outlook unread count** replacing the Gmail widget: swap the fetch layer
   for Microsoft Graph (delegated permission, own mailbox) — draw logic is
   unchanged. Confirm with IT before investing effort if targeting a
   corporate M365 tenant (Conditional Access / admin consent can block a
   personal Azure app registration entirely).
3. **iPhone steps (yesterday only)**: push, not pull — an iOS Shortcuts
   automation POSTs the daily total to a small Flask receiver running on the
   Pi's LAN; the dashboard reads the stored value. (Strava is the fallback
   if this route isn't worth building.)
4. **Spotify now-playing** is already implemented upstream via Last.fm
   (`LASTFM_CONF`) and now lives in the middle column (replacing Weather,
   see above) — no work needed on the widget itself.
5. **More middle-column widgets**: the Spotify widget doesn't fill the
   whole column, and weather's slot is otherwise idle now. Candidates for
   filling the remaining space, not yet scoped: a compact weather summary,
   a second usage/status widget, or something else entirely — ask before
   picking one.

## Conventions

- Australian/British English in prose, comments, and user-facing copy
  (colour, organise, centre, licence as a noun).
- Fallback/placeholder everywhere: a dead API or missing token should
  degrade to last-known-value or a placeholder, never crash the frame.
- Stagger initial network requests (see `update_data_thread`) — don't
  parallelise widget fetches without a reason.
