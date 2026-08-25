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
the preview pipeline needs to special-case. Weather, the email-unread count,
and the greeting widget's affirmation line are not behind `ENABLE_*` flags:
weather and the affirmation (from the keyless `affirmations.dev` API) render
for real whenever there's network access, falling back to
`GREETING_FALLBACK_AFFIRMATIONS` if that request fails; email unread quietly
stays at `0` when `EMAIL_PROVIDER` is `None` or its token file is missing.

The affirmation line is API-sourced text of unpredictable length, unlike
every other string in this widget — `wrap_lines_limited()` in `main.py`
word-wraps it to at most 2 lines and ellipsis-truncates (hard-breaking by
character first, if even a single "word" is wider than the widget) whatever
doesn't fit, so a long or malformed response can never overflow into the
divider line or the email widget below it.

### Email widget: Gmail or Outlook, via EMAIL_PROVIDER

`EMAIL_PROVIDER` (`"gmail"` | `"outlook"` | `None`, defaults `None`) is a
mode selector, not a pair of `ENABLE_*` flags — exactly one provider (or
none) can be active, and two independent booleans would allow an
unrepresentable "both on" state. It gates `auth_gmail()`/`auth_outlook()`'s
interactive one-time OAuth prompts (so a fresh checkout never blocks on
`input()`) and which branch of `update_data_thread` fetches into the same
`data_store.email_unread_today` field; the fetch itself still self-gates on
its provider's token file, same as before this flag existed. Draw code
(`render_screen()`'s "3. Email" section) doesn't care which provider is
active — same field, same icon, same "Unread Today: N" label.

Both providers use the same one-time-authorize-then-refresh-forever pattern
as `auth_strava()`/`auth_spotify()`, counting `len(messages)` rather than a
returned/estimated count (same reasoning as Spotify's HTTP 204 handling:
what's actually returned, not an estimate), scoped to "received since local
midnight" rather than lifetime unread. Gmail's `after:` filter takes a Unix
timestamp (timezone-safe regardless of the Pi's locale); Microsoft Graph's
`receivedDateTime ge <ISO8601 with offset>` is offset-aware natively, so
`datetime.now().astimezone()` (not the naive `datetime.now()` Gmail's path
uses) is what makes that comparison correct.

Outlook authenticates as an Azure **public client** (Mobile and desktop
applications platform type) rather than a confidential client like
Gmail/Spotify — no client secret exists to protect, so it authenticates via
PKCE instead (`_pkce_pair()`): a random `code_verifier` and its
SHA-256-derived `code_challenge` (RFC 7636), generated fresh per
`auth_outlook()` call and never persisted, since the verifier is only
needed within that one authorization-code exchange. `OUTLOOK_REDIRECT_URI`
is `http://localhost` — one of Azure's own suggested values for this client
type, chosen specifically to avoid a repeat of Spotify's redirect-URI
back-and-forth.

**Work/school (M365) accounts are not guaranteed to work** — this is a
policy question on Microsoft's/the tenant's side, not something fixable in
this code: Conditional Access and tenant-wide app consent restrictions can
block a personal Azure app registration outright. `auth_outlook()` prints a
warning about this before the browser step; a personal Microsoft account
(`@outlook.com`/`@hotmail.com`) has none of these restrictions.

### Left column: Tasks, hiding System Load/Crypto/Ping (and their alternates)

`ENABLE_TODO = False` by default — when `True`, `render_screen()`'s Column 1
takes the `if ENABLE_TODO: ... else: <all three existing widget slots>`
branch, so the Tasks widget replaces System Load/Strava, Crypto/Bambu, *and*
Ping/Roborock/Antigravity/Codex all at once rather than swapping just one of
the three stacked slots — it's a whole-column takeover, same shape as
`ENABLE_WEATHER` but for three slots instead of one.

`TODO_MAX_TASKS` (currently 5) always reserves that many row slots — fewer
real tasks just leave empty checkbox rows rather than resizing the rows, so
the layout doesn't jump around as the count changes day to day. Row height
is computed from `TODO_MAX_TASKS` rather than hardcoded, so changing the
constant needs no other edits. `data_store.todos` is a list of
`{"title": str, "due": str | None, "completed": bool}` dicts —
`TODO_PLACEHOLDER_TASKS` seeds it until the roadmap's `GET /api/top-todos`
fetch is wired in (see above); a completed task can still legitimately be
among today's top N (e.g. shown done for the rest of the day), so the
placeholder data deliberately mixes completed and not.

A completed task draws a filled checkbox (rather than the default outline)
with a light checkmark cut into it via two white lines, plus a strikethrough
through the title — both cues together rather than either alone, since a
filled checkbox alone doesn't read as "done" at e-ink's low contrast and a
strikethrough alone is easy to miss at a glance. The strikethrough's y
position comes from the title's own `draw.textbbox()` vertical centre
rather than a fixed offset, so it lands correctly regardless of which font
metrics the title text ends up using.

Task titles and due-time strings are external text like the affirmation
line and Spotify's artist/track fields — `wrap_lines_limited(..., max_lines=1)`
truncates each independently in pixels rather than assuming they fit,
same helper, same reasoning. The due-time column budget (100px) is sized
off measuring a real "HH:MM AM/PM" string (~94px at `fonts['20']`), not a
round number — an untested guess here undercounted it once already.

### Middle column: Spotify now-playing, not Weather

`ENABLE_WEATHER = False` by default — the middle column shows the Spotify
now-playing widget instead (`render_screen()`'s `elif not ENABLE_WEATHER:`
branch), so Spotify and the greeting widget can be visible at the same time
rather than fighting over the same column-3 slot they originally shared.
Weather's drawing code is untouched, just gated behind the flag, so flipping
it back to `True` restores it on this slot. `SPOTIFY_ART_SIZE` (currently
190px) controls the album-art fetch/display size in one place — the fetch
code (`fetch_spotify_data()`) resizes to this before dithering, since
upscaling an already-dithered 1-bit image afterwards would smear it.

Uses Spotify's own Web API (`/v1/me/player/currently-playing`), not
Last.fm — see `auth_spotify()`/`fetch_spotify_data()`. One-time OAuth via
the same copy-the-code-from-the-dead-redirect-page trick `auth_strava()`
already used, then a `refresh_token` renews access tokens indefinitely with
no further browser interaction. The redirect URI is `SPOTIFY_REDIRECT_URI`
(`https://127.0.0.1`) — Spotify's dashboard rejects `http://localhost`
(insecure hostname) and even plain `http://127.0.0.1` (insecure scheme),
currently accepting only the `https://` IP literal; Strava's equivalent flow
has neither restriction, hence the difference from `auth_strava()`'s
`http://localhost`. No real TLS listener is needed: nothing answers on
`127.0.0.1` at all, so the browser's connection fails before any handshake,
and the address bar still shows the attempted URL with `?code=...` intact.
`fetch_spotify_data()`
makes its own request rather than using `net.get_json()`, because Spotify
returns HTTP 204 (empty body, not an error) when nothing is playing —
`get_json()`'s `resp.json()` call would turn that into a swallowed exception
indistinguishable from a real failure, and unlike a real failure "nothing
playing" must still update the widget (otherwise pausing would leave stale
"still playing" text on screen indefinitely).

**Refresh token lifetime**: Spotify's dashboard shows "Refresh Token
Lifetime: 180 days" for the app. This is an inactivity timer, not a hard
expiry — using the refresh token resets it, and `fetch_spotify_data()` does
that roughly hourly (access tokens last ~1hr) whenever `ENABLE_SPOTIFY` is on
and the Pi is running, so it should never actually lapse in normal
operation. It only bites if the dashboard sits off/disabled for 180+
consecutive days, in which case Spotify starts rejecting the token; the
existing 401 handling in `fetch_spotify_data()` logs
`"Spotify token rejected (401) - re-run auth_spotify() to re-authorize"`
rather than failing silently, at which point deleting `spotify_token.json`
and re-running the one-time browser auth fixes it.

Artist/track names are external (Spotify) text, same "unpredictable width"
category as the affirmation line — a plain `[:N]` character slice can still
overflow the column if the text happens to be wide characters, so the
widget reuses `wrap_lines_limited(..., max_lines=1)` to pixel-measure and
ellipsis-truncate them instead, the same helper the affirmation line uses.

Artist/track names can also be full Chinese/Japanese/Russian, which Aldrich
can't render at all — `needs_intl_font()` checks each field (CJK Unified
Ideographs / Hiragana+Katakana / Cyrillic ranges) and switches that whole
field to `fnt/CJK-Cyrillic-Spotify.ttf` (`'intl_24'`/`'intl_28'`) when it
does, rather than mixing fonts within one field like the greeting widget's
`draw_mixed_text()` does — simpler, and correct here since a field is never
part-Latin-part-CJK in practice. This font is a ~5.8MB subset (GB2312 +
Big5 + Hiragana/Katakana + Cyrillic + ASCII), not the tiny curated one the
greeting widget uses — the two are unrelated problems: the greeting widget
only ever needs a handful of words *we* chose, so a handful of exact glyphs
is enough, but artist/track names are unpredictable live data, so this one
needs broad script coverage instead. GB2312 alone (Simplified Chinese,
~7445 chars, 2.4MB) is NOT enough on its own — verified against a real
J-pop artist name (米津玄師, Kenshi Yonezu) where 師 uses a traditional
character form GB2312 doesn't have but Big5 does; Japanese Kanji lean
closer to Traditional than Simplified. Regenerate with `fontTools.subset`
the same way as `fnt/CJK-Greeting.ttf` (see below) if broader coverage is
ever needed — derive the GB2312/Big5 code point lists via Python's built-in
codecs (`bytes([hi, lo]).decode('gb2312')` swept over the valid byte range)
rather than guessing a Unicode range, since neither is contiguous.

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

## Companion web app (Cloudflare Pages + D1)

A separate project (its own repo, not this one) is being built to feed this
dashboard custom data it has no public API for: a Cloudflare Pages app
backed by a D1 database, reachable at a `*.pages.dev` dev subdomain (no
custom domain). First use case is a personal todo list
(`GET /api/top-todos` → the N highest-priority items) and remote widget
configuration (which widgets/features should be visible, without editing
`main.py` and redeploying to the Pi by hand). More endpoints will show up
there as new widgets need custom data — this section describes the shape to
follow, not an exhaustive endpoint list.

**One endpoint per resource, not one combined blob.** Each logical piece of
data (todos, widget config, whatever's next) gets its own URL
(`/api/top-todos`, `/api/widget-config`, ...), fetched independently by
`update_data_thread` on its own timer — the same pattern every other widget
in this file already uses (weather, crypto, Spotify, ...): a fetch that can
fail without affecting anything else, degrading to last-known-value or a
placeholder. A single combined endpoint breaks that isolation — a bug in
the todo feature's D1 query would 500 the whole response and blank out
unrelated data (like widget config) riding along in the same payload, and
it forces every consumer onto one refresh cadence even though a todo list
wants to feel reasonably fresh while widget config is more "check every
so often." Cloudflare's free tier (100k Workers requests/day, 5M D1 rows
read/day) isn't a reason to combine them either — a personal dashboard
polling a handful of endpoints every few minutes is nowhere near either
limit regardless of how many separate endpoints it hits.

Auth: a shared secret sent as a header (e.g. `X-Api-Key`) on every request,
checked once in the Cloudflare Pages Function — the endpoint is otherwise
public since there's no real domain/WAF in front of it.

## Roadmap (not yet built — one session per item)

These are planned follow-ups; each is its own scoped session. Keep the
render/drive split in mind when touching related code, but don't build these
until asked:

1. **Todo widget fetch** — the draw side is already built (`ENABLE_TODO`,
   see below); still needs `update_data_thread` to actually call
   `GET /api/top-todos` from the companion app above and populate
   `data_store.todos`, replacing `TODO_PLACEHOLDER_TASKS`. Same shared-secret
   header and fetch → parse → fallback pattern as every other widget — each
   task dict should keep the `{"title": ..., "due": ..., "completed": ...}`
   shape (`due` nullable) the draw code already expects.
2. **Remote widget config via `/api/widget-config`** — right now
   `EMAIL_PROVIDER` (and every `ENABLE_*` flag) is a local edit-and-redeploy
   constant. Once the companion app exists, have `update_data_thread` poll
   `/api/widget-config` on its own timer (independent of `/api/top-todos`,
   per the one-endpoint-per-resource rule above) and let its response
   override these at runtime. Proposed shape, extensible as more remote
   toggles are needed — add fields, don't restructure what's already there:
   ```json
   { "email_provider": "gmail" }
   ```
   `email_provider` mirrors `EMAIL_PROVIDER`'s exact values (`"gmail"` /
   `"outlook"` / `null`). Missing/unrecognised values should fall back to
   the local constant, not disable the widget — a remote-config outage must
   degrade like every other fetch in this file, not change behaviour.
3. **iPhone steps (yesterday only)**: push, not pull — an iOS Shortcuts
   automation POSTs the daily total to a small Flask receiver running on the
   Pi's LAN; the dashboard reads the stored value. (Strava is the fallback
   if this route isn't worth building.)
4. **Spotify now-playing** is already implemented via Spotify's own Web API
   (`SPOTIFY_CONF`, see above) and lives in the middle column (replacing
   Weather) — no work needed on the widget itself.
5. **Gmail vs Outlook** is already implemented via `EMAIL_PROVIDER` (see
   above) — no work needed on the widget itself, only on wiring item 2 in
   once the companion app exists.
6. **More middle-column widgets**: the Spotify widget doesn't fill the
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
