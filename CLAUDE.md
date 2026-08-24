# CLAUDE.md

Context for Claude Code sessions working on this repository.

## What this is

A large e-ink desk/wall dashboard. A Raspberry Pi periodically pulls data from
various APIs, composes a single 1360×480 black-and-white image with Pillow,
and pushes it to a Waveshare 10.85" e-paper display over SPI. Forked from
[czuryk/Waveshare-ePaper-10.85-dashboard](https://github.com/czuryk/Waveshare-ePaper-10.85-dashboard).

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
the preview pipeline needs to special-case. Weather and the Gmail unread
count are not behind `ENABLE_*` flags: weather is a keyless public API so it
renders for real whenever there's network access; Gmail unread quietly stays
at `0` when no `token.json` is present.

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
headless Chromium/Playwright font rendering. To add e.g. "嗨" (U+55E8):

```shell
pip install fonttools
python -m fontTools.subset \
  /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
  --font-number=0 --unicodes=U+55E8 --glyph-names \
  --output-file=fnt/CJK-Greeting.ttf --no-hinting --desubroutinize
```

This produces a ~1.5KB font with just that glyph. `fnt/CJK-Greeting.ttf`
already exists for the greeting widget's "嗨" — add more `--unicodes` (comma
separated, or a new subset file) if a future widget needs different
characters. When mixing a CJK glyph into a line of Latin text, don't align
by baseline or top-left origin — the two fonts have very different vertical
metrics and it looks visibly off. `draw_mixed_text()` in `main.py` instead
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
   (`LASTFM_CONF`), documented here for context — no work needed.

## Conventions

- Australian/British English in prose, comments, and user-facing copy
  (colour, organise, centre, licence as a noun).
- Fallback/placeholder everywhere: a dead API or missing token should
  degrade to last-known-value or a placeholder, never crash the frame.
- Stagger initial network requests (see `update_data_thread`) — don't
  parallelise widget fetches without a reason.
