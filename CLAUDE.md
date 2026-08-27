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
   `update_data_thread` briefly first so every widget gets a chance to
   populate before the one frame is rendered — waiting on
   `main.first_fetch_pass_done` (a `threading.Event()` set once
   `update_data_thread()`'s first loop iteration finishes every gated
   fetch, since every `last_update` timer starts at 0) rather than a
   fixed `time.sleep()`. That first pass runs *every* fetch sequentially
   — weather, crypto, ping, todos, water, away-message, system-status,
   whichever `TODO_FILLER_WIDGET` source, etc. — so a flat sleep long
   enough to reliably cover all of them on a slow/jittery connection
   would waste time on a fast one, and one too short (the original
   default was 6s) intermittently snapshots the frame mid-fetch: no
   error, just `data_store.todos` (or anything else) still at its
   pre-fetch default, which looks identical to a real failure. `--settle`
   is now a timeout on that wait (default 30s) rather than an
   unconditional sleep — it still renders once that timeout elapses, just
   with whatever happened to populate by then, so the tool never hangs
   forever on one stuck fetch. `main()`'s own Pi loop never touches this
   event; it doesn't need to, since it redraws continuously regardless of
   whether the first pass has finished yet.

`main.py` also has one Windows-incompatibility unrelated to the display: it
imported the POSIX-only `resource` module unconditionally at the top level.
That import is now guarded (`try/except ImportError`) so the file loads on
Windows; the rlimit tweak it does is a no-op there.

### Placeholders / fallbacks

Every `ENABLE_*` widget toggle in `main.py` defaults to `False`, so a fresh
checkout with no credentials configured already renders the fallback widgets
(System Load, Crypto, Internet Ping, Greeting) instead of the token-gated
ones (Strava, Bambu, Roborock, Claude/Codex/Antigravity usage) — this is the
project's built-in graceful-degradation design, not something the preview
pipeline needs to special-case. `ENABLE_TODO`, `ENABLE_SPOTIFY`, and
`ENABLE_WATER` are the exceptions, defaulting to `True` now that all three
are fully set up and confirmed working for this deployment — a fresh
checkout with no `waveshare_api_config.json`/`spotify_token.json` still
degrades safely (`fetch_todos_data()`/`fetch_water_status()` both no-op
without their config file; `auth_spotify()` only prompts once, and
pressing Enter through it falls back to "Nothing Playing"), it just isn't
the *quiet* off-by-default most other toggles are. Weather, the
email-unread count, and the greeting widget's affirmation line are not
behind `ENABLE_*` flags at all: weather and the affirmation (from the
keyless `affirmations.dev` API) render for real whenever there's network
access, falling back to `GREETING_FALLBACK_AFFIRMATIONS` if that request
fails; email unread quietly stays at `0` when `EMAIL_PROVIDER` is `None`
or its token file is missing. `EMAIL_PROVIDER` itself defaults to
`"gmail"` rather than `None`, same "already confirmed working" reasoning
as `ENABLE_TODO`/`ENABLE_SPOTIFY`/`ENABLE_WATER` — see "Email widget"
below.

The affirmation line is API-sourced text of unpredictable length, unlike
every other string in this widget — `wrap_lines_limited()` in `main.py`
word-wraps it to at most 2 lines and ellipsis-truncates (hard-breaking by
character first, if even a single "word" is wider than the widget) whatever
doesn't fit, so a long or malformed response can never overflow into the
divider line or the email widget below it.

### Email widget: Gmail or Outlook, via EMAIL_PROVIDER

`EMAIL_PROVIDER` (`"gmail"` | `"outlook"` | `None`, defaults `"gmail"`) is a
mode selector, not a pair of `ENABLE_*` flags — exactly one provider (or
none) can be active, and two independent booleans would allow an
unrepresentable "both on" state. It gates `auth_gmail()`/`auth_outlook()`'s
interactive one-time OAuth prompts and which branch of `update_data_thread`
fetches into the same `data_store.email_unread_today` field; the fetch
itself still self-gates on its provider's token file, same as before this
flag existed. Joins `ENABLE_TODO`/`ENABLE_SPOTIFY`/`ENABLE_WATER` as an exception to the
usual "off by default" rule now that Gmail OAuth is set up and confirmed
working for this deployment — still safe on a fresh checkout without
`token.json`, since `auth_gmail()` only prompts once and pressing Enter
through it leaves the widget at 0 rather than crashing. Draw code
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
`MIDDLE_COLUMN_WIDGET` but for three slots instead of one.

`TODO_TASKS_PER_PAGE` (currently 3) is a fixed page size, not a "reserve at
least this many rows" minimum — the widget always shows exactly this many
rows, regardless of how many real tasks come back. Fewer real tasks than
that just leave the remaining rows blank (no checkbox, no title) rather
than resizing the grid around them; more real tasks page through
`TODO_TASKS_PER_PAGE` at a time (see "Task pagination" below) rather than
growing the grid to fit them all. This is what makes both this widget's
height *and* the filler carousel's height below it fixed, predictable
blocks instead of the two trading space back and forth as the task count
changes day to day — "controllable space" for each, not one growing at the
other's expense. Row height itself is still computed off a fixed 5-row
reference (`row_h = (row_bottom - row_top) / 5`) independent of
`TODO_TASKS_PER_PAGE` — that reference exists purely so the
checkbox/font/spacing sizing already tuned for that exact row height
doesn't need retuning if the page size is ever changed; it has no other
meaning any more (there's no longer a "5th row" for anything to occupy —
whatever height that used to leave over now belongs to the filler carousel
as a fixed block instead of a variable-height remainder). `data_store.todos` is a list of
`{"title": str, "due": str | None, "completed": bool}` dicts, or `None` —
`None` specifically means "no successful fetch yet" (never tried, or the
last attempt failed), distinct from `[]` (fetch succeeded, genuinely no
tasks today); see "Placeholders" below for how the draw code tells them
apart. `TODO_PLACEHOLDER_TASKS` is a manual-testing seed only (not a live
default any more — see `DataStore.__init__`) for exercising the row
layout locally; `fetch_todos_data()` populates the real value at runtime.
A completed task can still legitimately be among today's top N (e.g.
shown done for the rest of the day), so the placeholder data deliberately
mixes completed and not.

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

**Two distinct placeholders, not one.** `render_screen()` checks
`todos is None` before `not todos`: a centred `icon_disconnected` plus
"Connection Failed" for `None` (no successful fetch yet), or a centred
`icon_task` plus "No Tasks Today" for `[]` (fetch succeeded, list is
genuinely empty) — same "icon plus centred message" shape as Spotify's
"Nothing Playing" and the weather placeholder, just two variants instead
of one. `icon_disconnected.bmp` is a custom glyph (concentric white
signal arcs cut into a black circle, struck through with a diagonal
slash) rather than reusing `icon_wifi.bmp` — that one's a vendored icon
used elsewhere for "internet quality" in a *connected* context, so
reusing it here would read as "still connected" rather than "failed".
This is a
deliberate exception to this file's usual "keep the last known value on a
failed fetch" convention (see Conventions below): `update_data_thread`'s
todos fetch overwrites `data_store.todos` with `fetch_todos_data()`'s
result on *every* attempt, success or failure, rather than only on
success. A stale task list — wrong due times, wrong completed
checkboxes — is actively misleading in a way a stale weather reading or
crypto price isn't, so for this widget an honest "can't connect" beats
silently showing yesterday's data.

**Task pagination.** `page_count = math.ceil(len(todos) / TODO_TASKS_PER_PAGE)`
and `page_idx = int(time.time() // TODO_TASK_ROTATE_SECONDS) % page_count`
pick which `TODO_TASKS_PER_PAGE`-sized slice of `todos` is on screen right
now — the same wall-clock-derived, no-state-to-persist mechanism the
filler carousel below uses for its own rotation, so it stays correct
across a script restart with no counter to save/restore. A day with
`TODO_TASKS_PER_PAGE` or fewer tasks always has exactly one page
(`page_count == 1`), so nothing ever visibly rotates; more than that pages
through in fixed-size groups every `TODO_TASK_ROTATE_SECONDS`. The last
page of an uneven count (e.g. 5 tasks → pages of 3 then 2) is simply
shorter — its remaining row(s) draw blank, same as an under-`TODO_TASKS_PER_PAGE`
day, rather than pulling tasks forward from the next page to keep every
page full.

**The filler slot is a combined carousel, not a single leftover-space
widget, and is now a fixed-height block independent of the task grid
above it.** Previously the filler always got at least one row by capping
real task display one slot short of the total row count — a coupling
where a busy day (more tasks) meant a cramped filler and a quiet day
(fewer tasks) meant an oversized one. Fixing task display at
`TODO_TASKS_PER_PAGE` rows (see above) decouples the two entirely: the
task grid is always exactly `TODO_TASKS_PER_PAGE` rows regardless of task
count, so the filler carousel below it is always exactly
`row_bottom - TODO_TASKS_PER_PAGE * row_h` tall too — both are fixed,
predictable blocks now, neither one's size depends on the other's content.
`TODO_FILLER_WIDGET` (`"news"` |
`"nasa"`, default `"news"`) is a mode selector, same reasoning as
`EMAIL_PROVIDER`/`MIDDLE_COLUMN_WIDGET`: exactly one of the two occupies
that half of the rotation at a time. Local default for now, intended to
eventually be set remotely via `/api/widget-config` the same way those
two are — see roadmap. Calendar events are deliberately *not* part of
this selector — they always join the rotation whenever there are any
upcoming, regardless of which of news/NASA is chosen (see "Combined
carousel" below). `update_data_thread` only polls whichever of news/NASA
is currently selected, but always polls calendar events unconditionally.

`render_screen()` builds one ordered `filler_slides` list —
`[('news', headline), ...]` or `[('nasa', None)]` (whichever
`TODO_FILLER_WIDGET` selects) followed by `[('calendar', event), ...]`
for every upcoming event — as two contiguous blocks in rotation order,
not interleaved. A single wall-clock-derived index
(`int(time.time() // TODO_FILLER_ROTATE_SECONDS) % len(filler_slides)`)
picks the current slide, the same "no state to persist, stays correct
across a script restart" mechanism the old calendar-only rotation used.
`TODO_FILLER_ROTATE_SECONDS` (60s, matched to `main()`'s own render
cadence — a shorter value would just mean some redraws land in the same
rotation bucket as the last, since the display can only change on an
actual redraw) replaced the old separate `NEWS_ROTATE_SECONDS`/
`CALENDAR_ROTATE_SECONDS` constants now that one rotation clock drives
every slide type. `draw_rotation_dots()` draws the position-indicator dot
row (filled = current position) across all three slide types, shown only
when `len(filler_slides) > 1` — one slide (e.g. NASA with no calendar
events upcoming) needs no "cycling through several things" cue and gets
the block's full height instead.

When `filler_slides` is empty — fresh boot, missing config file, a fetch
failure on whichever of news/NASA is selected, (NASA only) a day it
publishes a video instead of a photo, *and* no calendar events upcoming —
it falls back to `TODO_EMPTY_ROW_ICONS`: a row of smile/happy/laugh faces
(one picked at random per remaining slot via `random.choice()`), laid out
side by side and centred in the filler block rather than stacked one per
row, sized to use the filler block's full height (up to 64px) rather than
pinned to a single row's ~77px band. Because calendar events always join
the rotation, this fallback is rarer than it was when calendar was just a
third pickable mode — it now only fires when *both* halves of the
carousel are empty at once. `n_faces` is a fixed count (3) rather than
derived from how many task rows were blank — the two blocks have no
size relationship to each other any more (see above), so there's nothing
meaningful left to derive it from.

**`"news"`** contributes her next `NEWS_FETCH_SIZE` (5) headlines from
newsdata.io's `/api/1/latest` (`fetch_news_headlines()`) to the carousel,
one slide per headline. Replaced NewsAPI.org (used earlier in this
project) after it stopped returning usable results in testing.
`NEWS_COUNTRY` (`"au"`) is the only query filter — newsdata.io has no
city-level targeting (nothing closer to "Canberra" than the country
code), and the confirmed-working test query didn't filter by category
either, so this keeps it simple rather than guessing at a category value
that might zero out results on a quiet day. `NEWS_PREFERRED_KEYWORDS`
sorts the fetched batch so any headline containing one of those words
rotates in first, rather than narrowing the query itself and risking an
empty result on quiet news days — everything else keeps newsdata.io's
own ordering. Polled every 4 hours (`ENABLE_TODO`-gated) — a handful of
headlines don't need to feel real-time, and this also bounds how often
the companion app gets a fresh batch (see below). `NEWS_API_CONF` holds
a gitignored `{"api_key": ...}` file, same never-commit-a-secret pattern
as every other credential here.

**The `content` field is never used, on screen or off.** newsdata.io's
free tier returns the literal string `"ONLY AVAILABLE IN PAID PLANS"`
for `content` rather than omitting the field — `fetch_news_headlines()`
doesn't even read it, using `description` (real free-tier text) instead
wherever more than a title is needed, so that placeholder string can
never end up displayed or stored anywhere by accident.

**Every fetched batch is also pushed to the companion app**, via
`post_news_articles()` (`POST /api/news-articles`, fire-and-forget, same
pattern as `post_system_status()`) — each article's `title`, `link`,
`description`, `source_name`, and `published_at` (newsdata.io's naive
UTC `pubDate` string, reformatted to real ISO 8601 by
`_format_newsdata_pub_date()`). The e-ink screen only ever shows a
title, rotating through the 5; this lets her read past a headline on the
companion app if she wants to, without needing the screen itself to
show more than one line of text.

**`"nasa"`** contributes NASA's Astronomy Picture of the Day
(`fetch_nasa_apod_image()`, `GET /planetary/apod`) as a single slide —
one photo, not a copy per row — since APOD is often a busy
starfield/nebula shot that needs real room to read as a picture once
dithered to 1-bit rather than noise. Cover-fit via `ImageOps.fit()`
(crop to fill, not letterbox) and dithered the same way as Spotify's
album art (`ImageEnhance.Contrast(...).enhance(3.0)` then
`.convert("1", dither=Image.NONE)` — a hard threshold, not
Floyd-Steinberg, for visual consistency with that widget). `NASA_APOD_CONF`
holds a gitignored `{"api_key": ...}` file, same pattern as `NEWS_API_CONF`.
Polled hourly — APOD changes at most once a day, so anything faster is
unnecessary load. Because it's a single slide sharing the carousel with
however many calendar events are upcoming, its photo only shrinks to
leave room for the dot row when `len(filler_slides) > 1` (i.e. at least
one calendar event is also in the mix) — with no calendar events at all
it's the sole slide and the photo still fills the block edge-to-edge,
exactly as before this redesign.

**Calendar events always join the carousel** — up to 3 upcoming iPhone
calendar events, contributed as one slide each regardless of whether
`TODO_FILLER_WIDGET` is `"news"` or `"nasa"`. Each event shows a small
"UPCOMING" label plus a formatted time (`_format_calendar_time()`:
`"Today 3:00 PM"` / `"Tomorrow 9:00 AM"` / `"Fri 10:00 AM"` for anything
further out — with at most 3 events shown, they're never more than about
a week away, so a weekday name is unambiguous without the full date) and
the event title, pixel-wrapped the same way a news headline is.

**Past events are filtered out at render time, not fetch time.**
`render_screen()` builds `upcoming_calendar_events` by dropping anything
in `calendar_events` whose parsed start time (`_parse_calendar_time()`,
shared with `_format_calendar_time()`) is already earlier than the Pi's
current local time, *before* deciding what to rotate through or whether
to fall back to the faces. This can't happen at fetch time instead —
`data_store.calendar_events` is only refreshed every 300s (see below),
so an event that was still upcoming at the last poll can lapse well
before the next one, and the display re-renders roughly every 60s
regardless of whether a fetch just happened. Unlike a stale headline or
a day-old space photo (which still read as valid content even when
old), a calendar event carries its own built-in "is this still true"
signal — its timestamp — so a stale snapshot (her Shortcut hasn't run in
a while) degrades to fewer events, or the fallback faces once all three
have passed, rather than rotating through events that have obviously
already happened.

There's no `GET /api/calendar-events` data source on the Pi's own
initiative — the *source* of truth is her iPhone's Calendar app, which
the companion app has no API access to. An iPhone Shortcuts automation
(running on a schedule, e.g. hourly) reads her upcoming events with the
"Find Calendar Events" action and `POST`s them to the companion app,
which stores just the latest snapshot (trimmed to the 3 soonest,
server-side, same "server does the slicing" precedent as
`/api/tasks/top`). The Pi only ever polls that stored snapshot
(`fetch_calendar_events()`) — same shared-secret auth
(`waveshare_api_config.json`) as every other companion-app call, no
separate credential needed. Polled every 300s — faster than news/NASA's
hourly cadence, since "is this event starting soon" is more time-
sensitive than a headline, even though the underlying data only actually
changes whenever her Shortcut last ran, and it's polled *unconditionally*
(not gated on `TODO_FILLER_WIDGET`, see above) since it always feeds the
carousel regardless of which of news/NASA is selected. `events[:3]` in
`fetch_calendar_events()` is a defensive cap in case the endpoint ever
sends more than 3, since the dot-row math assumes it hasn't.

Unlike the Tasks fetch itself, a failed fetch on any of the three
sources (news, NASA, or calendar) does *not* clear the last good result
(see `update_data_thread`) — a slightly stale headline, a day-old space
photo, or a slightly stale event list isn't misleading the way a stale
task list is, so all three follow the file's usual "keep the last known
value" convention instead of the Tasks-specific exception.

Distinct from the all-empty placeholder above, which replaces the whole
column with one centred icon+message — this is the "some tasks done,
rest of the day is clear" case instead of "nothing at all today".

### Middle column: Spotify or Weather, switchable via MIDDLE_COLUMN_WIDGET

`MIDDLE_COLUMN_WIDGET = "spotify"` by default (`"spotify"` | `"weather"`) —
a single mode selector rather than the old `ENABLE_WEATHER` boolean, same
reasoning as `EMAIL_PROVIDER`: exactly one of the two can occupy this slot,
so two independent flags would allow an ambiguous "both on" state. Mirrors
a Spotify/Weather switcher on the companion app's side — local
edit-and-redeploy constant for now, intended to eventually be set remotely
via `/api/widget-config` (see roadmap) the same way `EMAIL_PROVIDER` is.
`render_screen()`'s column-2 block picks between the two with
`elif MIDDLE_COLUMN_WIDGET == "weather": ... else: <Spotify>` — Weather's
drawing code is untouched, just gated behind the switch. `SPOTIFY_ART_SIZE`
(currently 190px) controls the album-art fetch/display size in one place —
the fetch code (`fetch_spotify_data()`) resizes to this before dithering, since
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

**Weather placeholder**: when `MIDDLE_COLUMN_WIDGET == "weather"` but the
fetch hasn't populated `data_store.weather['current']` yet (fresh boot, or
open-meteo unreachable), `render_screen()` falls through to a "Weather
Unavailable" placeholder — a header ("WEATHER" + `icon_clouds`) plus a big
centred `icon_clouds` and message, the same "icon plus centred message"
shape as Spotify's "Nothing Playing" fallback. Without this, a dead
weather fetch left the whole column blank, since the original code only
had a weather-vs-Spotify branch with no third "weather failed" state.

### Middle column: hydration/growth widget overlays Spotify or Weather

`ENABLE_WATER = False` by default. When on, `render_screen()`'s column-2
block checks a `show_water` flag *before* the `MIDDLE_COLUMN_WIDGET`
switch, so the hydration widget takes priority over whichever of
Spotify/Weather is currently active — not just Spotify. `show_water` is
`ENABLE_WATER` and a water log within the last `WATER_SHOW_SECONDS` (60)
of now — regardless of `MIDDLE_COLUMN_WIDGET`.

**Timing piggybacks on the existing render loop instead of a dedicated
timer.** `main()`'s loop already redraws the whole screen roughly once a
minute (`sleep_time = max(5, 60 - elapsed)`). `show_water` just re-checks
"was the last log within the last 60s?" on every one of those redraws, so
the widget naturally appears on the first redraw after a log and disappears
on the redraw after that — no extra thread, timer, or scheduling needed.
The trade-off: because it's tied to that ~60s cadence rather than reacting
instantly, the widget can take up to ~60s after the real-world log to first
appear (whichever redraw happens to catch it), and it stays up for roughly
one redraw cycle rather than exactly 60.00s. Acceptable for a low-stakes
encouragement widget; not worth extra redraws (and extra e-ink wear) to
tighten.

**Growth resets on completion, not on a calendar timer.** `data_store.water`
carries `progress_litres` — a running total since the last time a plant
was completed — that the companion app computes and resets server-side
(not something the Pi derives itself): once it reaches
`WATER_GROWTH_TARGET_LITRES` (40L), the app zeroes it and bumps
`plants_grown_lifetime`, however long that took. No calendar binding at
all — a slow week doesn't lose progress the way a monthly reset would,
and a fast one doesn't have to wait for the calendar to catch up. The Pi
only ever divides `progress_litres` by `WATER_GROWTH_TARGET_LITRES` to get
a 0.0-1.0 growth fraction — no date math on this side, same as before.
At 0.5L per logged step, 40L is 80 distinct steps from seed to full growth.

**The plant is drawn procedurally (`draw_growth_plant()`), not as a set of
stage bitmaps.** A single growth fraction continuously scales the pot's
stem height/width and the canopy's radius/leaf size/leaf count, the same
"draw it with Pillow primitives" approach the sparklines and checkboxes
already use, rather than mapping ranges of litres to a handful of discrete
images — no art assets to commission, and no "which bitmap for 23.7L"
boundary logic to get wrong. Canopy leaves are placed with a golden-angle
(sunflower-seed) spiral (`GOLDEN_ANGLE = π(3-√5)`) rather than a fixed ring
of blobs — that packing stays gap-free and non-repeating at any leaf count,
so it looks equally natural whether there are 2 leaves (the sprout at
t≈0) or 46 (the lush canopy at t=1), and every added leaf slots in without
visibly rearranging the ones already there.

It's a potted plant, not a tree straight out of the ground — a fixed pot
(solid trapezoid body, a white rim ring cut into the top edge, a black soil
disc inside it) sits under the stem regardless of growth. The stem and
canopy never fully disappear even at t=0 — a very short stem with just 2
tiny leaves reads as a sprout rather than an empty pot, per the "starts
with a sprout, not nothing" requirement. Both canopy radius and leaf count
scale with `t` (leaf count 2→46 by default), so each 0.5L logged step
still nudges the canopy's spacing or leaf count by a visible amount across
all 80 steps, rather than only a few discrete jumps being visible over the
whole growth cycle.

`data_store.water` defaults to `{'progress_litres': 0.0,
'last_logged_at': None, 'last_amount_litres': None,
'plants_grown_lifetime': 0}` — `last_logged_at: None` always keeps
`show_water` false, so a fresh checkout (or before the companion app's
water endpoint is wired in) never shows the widget, same
degrade-to-safe-default convention as every other credentialed/unwired
widget in this file. Since there's no calendar window to label the total
against any more, the widget shows progress against the fixed target
instead of a bare total (e.g. "21.0 / 40 L grown"). `icons/icon_droplet.bmp`
is a hand-drawn bold water drop (no existing icon fit "hydration"), made
the same way as `icons/icon_task.bmp` — a filled ellipse plus a triangular
tip, rendered at 10x scale and downsampled for anti-aliasing, matching the
bold/filled style of the rest of the icon set.

**Lifetime badge**: a small mini-plant icon (`icons/icon_plant_grown.bmp`
— a scaled-down one-shot render of the same pot/stem/canopy shapes
`draw_growth_plant()` draws, not a separate design) plus `"x N"` in the
content area's top-left corner, showing `plants_grown_lifetime` — how many
times she's grown a plant to full (`progress_litres` reaching
`WATER_GROWTH_TARGET_LITRES`) across all time, not just the current growth
cycle. A distinct icon from the header's droplet so it reads as its own
"trophy count" rather than a second copy of the header at a glance. The
companion app computes this count server-side, same as `progress_litres`
itself — the Pi only displays it.

**Hydration nag notice**: a one-line reminder ("Haven't had water in a
while!" plus a small `icon_droplet`) pinned to the very bottom of the
middle column whenever `hydration_alert` is true — on top of whichever of
Spotify/Weather is currently showing there, same overlay approach as
`show_water` above it. `_hydration_alert_active()` computes it entirely
Pi-side from the same `last_logged_at` timestamp `/api/water-status`
already returns (see "Hydration widget fetch" below) — no new API data
needed. It's scoped to a working-hours window
(`HYDRATION_WINDOW_START_HOUR`/`HYDRATION_WINDOW_END_HOUR`, 9am-6pm by
default) rather than a rolling "N hours since last log" check that runs
all day: outside those hours she's off work and home, where a screen nag
isn't useful, so the count is treated as reset to zero at
`HYDRATION_WINDOW_END_HOUR` and doesn't start ticking again until
`HYDRATION_WINDOW_START_HOUR` the next morning — a stale log from days
ago can never trigger it, since the baseline is always clamped to
*today's* window start (`max(window_start, last_logged_at)`), never left
at some ancient timestamp. It fires once at least `HYDRATION_ALERT_HOURS`
(4.0) have elapsed since that baseline. Uses the Pi's own local clock
(`datetime.now()`), same convention as the local-midnight calculation
elsewhere in this file — it trusts the Pi's system timezone to already be
set to Canberra time rather than hardcoding one. Mutually exclusive with
`show_water` in practice (that needs a log within the last minute; this
needs one missing for hours) but excluded explicitly anyway
(`hydration_alert = ENABLE_WATER and not show_water and ...`), so a fresh
log's pop-up always wins the redraw it first appears on.

Weather's hourly-forecast row normally runs edge-to-edge to the column's
bottom pixel (see the 4-hour forecast in the weather widget above) — no
room left for a bottom-pinned notice. Rather than permanently shrinking
that row (which would waste space on every ordinary day just for a rare
alert), it only shrinks — smaller icons/font, tighter spacing — on the
redraws where `hydration_alert` is actually true, freeing exactly enough
of a strip at the bottom for the notice; the everyday layout is
byte-for-byte unchanged. Spotify already has enough clearance below its
artist/track text without any such adjustment.

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
backed by a D1 database, reachable at `https://waveshare-apis.pages.dev`
(no custom domain). First use case is a personal todo list
(`GET /api/tasks/top` → the N highest-priority items) and remote widget
configuration (`GET /api/widget-config`, which widgets/features should be
visible, without editing `main.py` and redeploying to the Pi by hand). More
endpoints will show up there as new widgets need custom data — this section
describes the shape to follow, not an exhaustive endpoint list.

**One endpoint per resource, not one combined blob.** Each logical piece of
data (todos, widget config, whatever's next) gets its own URL
(`/api/tasks/top`, `/api/widget-config`, ...), fetched independently by
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
public since there's no real domain/WAF in front of it. On the Pi side, the
base URL and key live in `waveshare_api_config.json`
(`WAVESHARE_API_CONF['CONFIG_FILE']`) — a small local file, gitignored like
every other credential in this repo, holding
`{"base_url": ..., "api_key": ...}`. It's read fresh on every fetch rather
than cached at import (`_load_waveshare_api_config()`), so editing it takes
effect on the next poll with no restart; a missing file (fresh checkout)
makes both fetchers below no-ops, same degrade-to-safe-default convention
as every other unwired widget.

### Todo widget fetch (`fetch_todos_data()`)

Polls `GET /api/tasks/top` every 120s (todos want to feel reasonably fresh,
per the cadence reasoning above) and remaps its response onto
`data_store.todos`. The companion app's actual field names —
`{"text": ..., "dueTime": "HH:MM", "completed": ...}`, plus `id`/`position`/
`isTop` the widget doesn't need — differ from the `{"title": ..., "due": ...,
"completed": ...}` shape the draw code expects (see "Left column: Tasks"
above), so this function maps `text` → `title` and reformats `dueTime`
through `_format_due_time()`: the API sends 24h `"HH:MM"`, but the due-time
column was sized for `"H:MM AM/PM"` (see the `due_col_w` note above), so
`"18:08"` becomes `"6:08 PM"` rather than being passed through raw. A
non-list response, an unreachable endpoint, or a missing config file all
return `None`, which leaves `data_store.todos` at its last known value —
same "don't blank a working widget on one bad fetch" rule as every other
fetch in this file. `/api/widget-config`'s `top_n` isn't used to slice the
result further here: the companion app already trims `/api/tasks/top` to
that count server-side.

### Remote widget config (`fetch_widget_config_data()`)

Polls `GET /api/widget-config` every 600s (config changes rarely, "check
every so often" per the cadence reasoning above) and, when the response is
a dict, overrides the module-level `EMAIL_PROVIDER`/`MIDDLE_COLUMN_WIDGET`/
`TODO_FILLER_WIDGET` globals directly from inside `update_data_thread`
(declared `global` there alongside `global_printer`). Only recognised
values are applied — `email_provider` must be `"gmail"`/`"outlook"`/`null`,
`middle_widget` must be `"spotify"`/`"weather"`, `todo_filler` must be
`"news"`/`"nasa"` — anything else (including a field the endpoint omits,
like `top_n` today) leaves the current value alone rather than disabling
the widget, per the roadmap's original "degrade like every other fetch"
requirement. `ENABLE_TODO` and the other `ENABLE_*` toggles are untouched
by this endpoint; they remain local edit-and-redeploy constants.

### Hydration widget fetch (`fetch_water_status()`)

Polls `GET /api/water-status` every 20s — much more frequent than the
todos/config endpoints above, because `show_water`'s "was the last log
within the last 60s" check needs to reliably catch a fresh log within that
window; a slower poll risks missing the pop-up on a given render cycle
entirely. `progress_litres`/`last_amount_litres`/`plants_grown_lifetime`
pass straight through into `data_store.water` — the companion app already
computes `progress_litres` as a running total since the last completed
plant (see `WATER_GROWTH_TARGET_LITRES` above), not a calendar window, so
the Pi does no further math on it. `last_logged_at` comes back as an ISO
8601 UTC string and gets converted to a Unix timestamp (`datetime.
fromisoformat(...).timestamp()`, same `Z`-suffix handling `time_until()`
uses elsewhere in this file) since that's what `show_water`'s
`time.time() - ...` comparison expects. A non-dict response, an
unreachable endpoint, or a missing config file all return `None`, which
leaves `data_store.water` at its last known value — same "don't blank a
working widget on one bad fetch" rule as every other fetch in this file.

### Away message fetch (`fetch_away_message()`)

Polls `GET /api/away-message` every 15s — faster even than the hydration
widget's 20s, since flipping this on is meant to take effect right away
(a "back in 10" set on the way out the door shouldn't take minutes to
show up). Returns `{"enabled": bool, "message": str}`; `message` is
hard-truncated to `AWAY_MESSAGE_MAX_LEN` regardless of what the API
sends, so a misbehaving companion app (or a bug in its own length check)
can never hand `draw_away_message()` a string long enough to force the
auto-fit below `AWAY_MESSAGE_MIN_FONT_SIZE` (see "Away message" below). A
non-dict response, an unreachable endpoint, or a missing config file all
return `None`, which leaves `data_store.away_message` at its last known
value — same "don't blank a working widget on one bad fetch" rule as
every other fetch in this file. In particular, that means a network blip
*while she's away* keeps showing the away screen rather than silently
reverting to the normal dashboard — the safer failure direction, since a
missed "she's back" toggle just delays the dashboard reappearing by one
poll, while a missed "she's away" fetch would expose the real dashboard
(and whatever's on it) when the point was to hide it.

## Away message (full-screen takeover)

A remote switch, driven entirely by the companion app (see "Away message
fetch" above) — no local `ENABLE_*` toggle, since the whole point is that
she controls it from the app in the moment, not something set once at
deploy time. `render_screen()` checks `data_store.away_message` right
after its usual lock-and-copy block, before column 1 is drawn at all: if
`enabled` is true and `message` is non-empty, it calls
`draw_away_message()` and returns immediately, so every widget is
genuinely skipped that redraw, not just drawn-over. Defaults to
`{'enabled': False, 'message': ''}` in `DataStore.__init__` and on a
missing config file, same safe-default convention as every other unwired
widget — a fresh checkout never hides the dashboard.

**`AWAY_MESSAGE_MAX_LEN` (60 characters)** exists so the auto-fit below
never has to shrink a message down to an illegibly small size just to
make it fit. The bound was chosen by testing the worst realistic case —
one long unbreakable "word" with no spaces for the wrapper to break on
(spaces let the wrapper add lines instead of shrinking the font further,
so a sentence of the same length is never worse) — and confirming it
still lands at a large, clearly-legible font well above
`AWAY_MESSAGE_MIN_FONT_SIZE` (40px): 60 unbroken characters fits at 84px.
`fetch_away_message()` enforces this length server-response-side (see
above); the companion app's input field should also enforce it
client-side with a live character counter, so she sees the limit while
typing rather than discovering it's been silently cut off.

**`draw_away_message()`/`_fit_away_message_font()`** implement the
auto-sizing: starting from `AWAY_MESSAGE_FONT_SIZES` (160px down to
`AWAY_MESSAGE_MIN_FONT_SIZE` in 4px steps), each candidate size
word-wraps the message with the same `wrap_text()` helper the
affirmation/news/task-title text uses elsewhere in this file — which
already hard-breaks a single word wider than the safe width by character,
so even a pathological no-spaces message stays within the horizontal
margin at every candidate size. The first (largest) size whose wrapped
lines fit within the vertical safe area wins; if even the smallest
candidate doesn't fit vertically, it's used anyway rather than raising,
since the widget must always draw something. This is a "shrink to fit"
search, not a "minimise line count" one — a short phrase that doesn't
fit the safe width in one line at the largest sizes wraps to two lines
*at that same large size*, rather than dropping to a smaller size just to
stay on one line, since a bigger two-line message reads better on a
across-the-room e-ink display than a smaller single-line one.

**Vertical/horizontal centring uses real ink bounding boxes
(`draw.textbbox()`), not nominal font-size-derived line heights** —
Aldrich's ascent/descent padding means a size-based estimate draws with a
visible gap above the glyphs instead of true centring, the same "measure
the actual bbox, don't trust nominal offsets" approach the Tasks widget's
completed-task strikethrough already uses elsewhere in this file. Each
line is centred independently, and lines are stacked with a
size-proportional gap (`0.3 * font.size`) around their combined real
height, then that whole block is centred vertically on the screen.

## System status reporting (POST /api/system-status)

The upstream project displayed Pi health (CPU load, RAM free, internet
ping) as an on-screen fallback widget in Column 1, shown when
Strava/Roborock/Antigravity aren't configured (the `SysLoad`/`Ping`
branches in `update_data_thread`). In this fork, Column 1 is
permanently taken over by the Tasks widget instead (`ENABLE_TODO`
defaults `True`), so that fallback path's `draw.text()` calls never
actually run — but the *reads* powering them still happen every cycle,
for a widget slot that's always hidden. Rather than compete for screen
space that's already spoken for, this reports the same kind of data
outward to the companion app instead, where it's free to be its own
page/panel. There's no `ENABLE_*` flag for this — like
`fetch_todos_data()`/`fetch_water_status()`, it self-gates purely on
`waveshare_api_config.json` existing (see "Companion web app" above),
consistent with every other companion-app call in this file.

**This is a fresh, independent read path, not a repurposing of
`data_store.sysload`/`data_store.ping`.** Those two still work exactly
as before and remain wired to the legacy on-screen fallback widgets
(untouched, in case Strava/Roborock/Antigravity ever get re-enabled and
that fallback path draws again) — `gather_system_status()` reads
`/proc`, `/sys`, and pings 8.8.8.8 on its own, independently of whether
those widgets are currently gated on or off, since the whole point is
this reports regardless of what's on screen.

**Fields sent** (`gather_system_status()`), each independently
best-effort — a failed read returns `None` for just that field rather
than dropping the whole report, since one unreadable `/proc` file
shouldn't cost every other metric; the companion app should treat a
`null` field as "unknown", not zero:
- `cpu_load_pct` — same heuristic as the SysLoad widget (1-min loadavg ×
  10, capped at 100); a rough load indicator, not a literal percentage
- `ram_free_mb` — from `/proc/meminfo`
- `cpu_temp_c` — from the Pi's thermal zone
  (`/sys/class/thermal/thermal_zone0/temp`); doesn't exist on non-Pi
  systems (this repo's own Windows/macOS `render_preview.py` path
  included), so `None` there is expected, not an error
- `disk_free_mb`/`disk_total_mb` — root filesystem, via
  `shutil.disk_usage('/')`; catches an SD card slowly filling with logs
  over months of unattended operation, the kind of failure that's only
  noticed once it's already full
- `uptime_seconds` — from `/proc/uptime`; flags a silent reboot (power
  blip, crash, watchdog) between checks
- `ping_ms` — same ICMP-to-8.8.8.8 check as the Ping fallback widget

Deliberately excludes crypto prices (BTC/ETH) — that's market-data
novelty from the same "hide the unused fallback widgets" family, not Pi
health, so it isn't part of this report.

**`ENABLE_CRYPTO` (default `False`) gates the BTC/ETH fetch itself.**
Unlike sysload/ping, crypto was never repurposed into anything this
deployment uses (see just above) — it was only ever the `else` branch
of `if ENABLE_BAMBU:`, so it ran unconditionally whenever `ENABLE_BAMBU`
was off, for a widget slot nothing ever draws. It also turned out to be
a real cost, not just a wasted one: CoinGecko's free tier rate-limits
(HTTP 429) readily, and `update_data_thread`'s first pass runs every
gated fetch sequentially — a stalled/retrying crypto call there ate
into the time every fetch after it in that same pass gets, including
`render_preview.py`'s `first_fetch_pass_done` wait. `ENABLE_BAMBU:
... elif ENABLE_CRYPTO: ...` now keeps crypto off unless explicitly
turned back on — same "keep it re-enablable without redeploying"
reasoning as every other `ENABLE_*` flag, rather than deleting the
fetch outright.

**Cadence: every 300s.** Slower than `away_message` (15s) or
`water`/`hydration` (20s) since nothing here needs to be caught within
minutes — it's a vitals check, not something driving an on-screen
pop-up. `post_system_status()` is fire-and-forget: unlike every GET
fetch in this file, there's no `data_store` field to write and no "last
known value" to keep, since nothing about this is ever displayed on the
Pi's own screen — that's the whole premise.

## Roadmap (not yet built — one session per item)

These are planned follow-ups; each is its own scoped session. Keep the
render/drive split in mind when touching related code, but don't build these
until asked:

1. **Todo widget fetch** and **remote widget config via
   `/api/widget-config`** are both now implemented — see "Todo widget
   fetch" and "Remote widget config" under "Companion web app" above.
2. **iPhone steps (yesterday only)**: push, not pull — an iOS Shortcuts
   automation POSTs the daily total to a small Flask receiver running on the
   Pi's LAN; the dashboard reads the stored value. (Strava is the fallback
   if this route isn't worth building.)
3. **Spotify now-playing** is already implemented via Spotify's own Web API
   (`SPOTIFY_CONF`, see above) and lives in the middle column (replacing
   Weather) — no work needed on the widget itself.
4. **Gmail vs Outlook** is already implemented via `EMAIL_PROVIDER` (see
   above), and is now also remotely switchable via `/api/widget-config`
   (see "Remote widget config" above) — no work needed on the widget itself.
5. **More middle-column widgets**: the Spotify widget doesn't fill the
   whole column, and weather's slot is otherwise idle now. Candidates for
   filling the remaining space, not yet scoped: a compact weather summary,
   a second usage/status widget, or something else entirely — ask before
   picking one.
6. **Hydration/growth widget fetch** is now implemented — see "Hydration
   widget fetch" under "Companion web app" above.

## Conventions

- Australian/British English in prose, comments, and user-facing copy
  (colour, organise, centre, licence as a noun).
- Fallback/placeholder everywhere: a dead API or missing token should
  degrade to last-known-value or a placeholder, never crash the frame.
- Stagger initial network requests (see `update_data_thread`) — don't
  parallelise widget fetches without a reason.
- Every `NetworkManager` failure is logged (`logging.error(...)` in
  `get_json()`/`get_image()`'s except blocks) rather than silently
  swallowed — degrading gracefully on screen doesn't mean the failure
  should be undiagnosable in `dashboard.log`. Always log through
  `_redact_url()`/`_redact_error()` rather than the raw `url`/exception
  text directly: NASA APOD and NewsAPI both pass their API key as a
  query-string parameter (per those APIs' own design, not something this
  project chose), and `requests`/`urllib3` connection-failure exceptions
  often embed that same query string verbatim inside their own message —
  so redacting only the URL you explicitly log isn't enough on its own.
- A response that arrives successfully (HTTP 200, valid JSON) but isn't
  shaped the way a fetch function expects — wrapped in an object instead
  of a bare list, an error payload, an empty dict — never reaches
  `NetworkManager`'s own error logging, since nothing raised an
  exception. Every companion-app/third-party fetch that checks the
  response's shape (`isinstance(data, list)`, etc.) logs through
  `_log_unexpected_response()` on a mismatch, for the same reason as the
  point above: without it, that case is indistinguishable in
  `dashboard.log` from a config file simply not existing yet. Exception:
  a documented, expected content variation that isn't actually a failure
  — e.g. NASA APOD publishing a video instead of a photo — stays silent,
  same as before; only the genuinely-malformed-shape case logs.
