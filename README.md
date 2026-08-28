# Waveshare-ePaper-10.85 Dashboard

A fully functional E-ink dashboard running on a Raspberry Pi Zero 1W or 2W. Designed for large Waveshare e-Paper displays (e.g., 10.85"), this project aggregates essential daily information and smart home status into a clean, minimalist interface.

## Key Features
* **(NEW!) Tasks widget:** Shows today's top tasks (title + optional due time) in the left column,
  replacing System Load/Crypto/Ping. Placeholder data for now (`ENABLE_TODO`, `TODO_PLACEHOLDER_TASKS`
  in `main.py`) — a real fetch will land once the companion todo-list app exists.
* **(NEW!) Hydration/growth widget:** Temporarily overlays whichever of Spotify/Weather is active in
  the middle column for about a minute whenever a new water log comes in, showing a plant that grows
  from a sprout to a full lush tree over 40 litres logged, resetting (and counting towards a lifetime
  total) whenever it reaches that target, however long that takes (`ENABLE_WATER` in `main.py`) — a
  real fetch will land once the companion app's water-logging endpoint exists.
* **(NEW!) Now supporting both Raspberry Pi Zero 1 & 2!**
* **(NEW!) Codex usage data:** Displays usage data for Codex, showing the limit, and limit reset time.
* **Antigravity usage data:** Displays usage data for Antigravity, showing the limit, and limit reset time.
* **Claude Code usage data:** Displays usage data for Claude Code, showing the daily limit, weekly limit, and limit reset time.
* **Weather & Air Quality:** Real-time temperature, humidity, wind direction/speed, UV index, 4-hour forecast, and AQI (with visual inversion for high pollution levels) using the Open-Meteo API. Shares the middle column with Spotify, switchable via `MIDDLE_COLUMN_WIDGET` in `main.py`; falls back to a placeholder if the fetch fails.
* **Strava Integration:** Displays total and yearly activity statistics (distance and ride counts), including specific breakdowns for biking and hiking.
* **Bambu Lab 3D Printer:** Live monitoring of print status, completion percentage, remaining time, and current layer progress.
* **Roborock Vacuum:** Live battery level, current status, and tracking for cleaned area during active cleaning.
* **Spotify:** Displays the currently playing track and artist.
* **Email (Gmail or Outlook):** Tracks the number of unread emails received today.
* **System Fallbacks:** Automatically switches to displaying System Load (CPU/RAM usage) or Cryptocurrency prices (BTC/ETH) if certain hardware integrations are disabled or offline for demonstration of dashboard capabilities. The fallback wedgets are not required tokens and ready to go.
* **Optimized Rendering:** Uses partial screen refreshes to prevent flickering, with scheduled full refreshes to clear e-ink ghosting.

<img width="2400" height="1792" alt="dashboard_primary" src="https://github.com/user-attachments/assets/20be2eae-4a06-48e2-9ad4-efcba00dcb7f" />
<img width="2400" height="1792" alt="dashboard_fallback" src="https://github.com/user-attachments/assets/158d65ee-9a12-4f09-a9d3-ea66ca3055bc" />

---

## Local preview (no Raspberry Pi / e-paper hardware needed)

Composing the frame is pure Python; only pushing it to the physical panel
needs real hardware. To iterate on layout/widgets from a regular
Windows/macOS/Linux machine and see the result as a PNG:

```shell
python -m venv .venv
# Windows: .venv\Scripts\activate | macOS/Linux: source .venv/bin/activate
pip install -r requirements-preview.txt
python render_preview.py
```

This writes `output.png` (1360×480) that you can open and eyeball.
`render_preview.py` auto-detects whether it's running on the Pi (with the
real display driver installed) or not — no flag to flip, same command
either way. See `CLAUDE.md` for how the hardware-import mocking works.

---

## Prerequisites & Installation

### Hardware
* [Raspberry Pi Zero 1W](https://www.raspberrypi.com/products/raspberry-pi-zero-w/) OR [Raspberry Pi Zero 2W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)
* [Waveshare E-Ink Display 10.85"](https://www.waveshare.com/10.85inch-e-paper-hat-plus.htm?sku=29790)

### 1. System Setup
Enable the SPI interface on your Raspberry Pi, which is required for communicating with the e-ink display:
```shell
sudo raspi-config
```
Go to Interfacing Options -> SPI -> Enable.

Update your system and install necessary system-level dependencies, including `tmux` for keeping the script running in the background:
```shell
sudo apt update
sudo apt install python3-pip python3-pil python3-numpy git tmux -y
```

### 2. Python Dependencies
Install the required standard Python packages:
```shell
pip3 install requests Pillow google-api-python-client google-auth-httplib2 google-auth-oauthlib aiomqtt roborock paho-mqtt gpiozero lgpio spidev
```

**Notes:**
The `bambulabs_api` library is already bundled in this package (in lib/), but its dependency paho-mqtt is not — it is included in the command above.
On Raspberry Pi OS (Bookworm) the GPIO/SPI packages are best installed via apt (a system-managed Python may reject pip for these). If pip3 complains about an "externally-managed-environment", install them with:
```shell
sudo apt install -y python3-gpiozero python3-lgpio python3-spidev
```

### 3. Display Library

* The **patched** version of the epd10in85 library with fixed partial refresh issue already included in this package.

* **(New)** Included support of Raspberry PI Zero 1.

---

## Configuration & Widget Setup

All widget toggles and API configurations are located at the top of the `main.py` script. You can enable or disable specific widgets using the `ENABLE_*` boolean variables.

### Codex (ChatGPT)
1. Codex limits are read from the official OpenAI Codex CLI tokens — unlike Claude, the dashboard does not run its own browser login flow.
2. Install the Codex CLI (for example `npm install -g @openai/codex`) and run codex login in the terminal.
3. A browser window opens — sign in with your ChatGPT account and click "Authorize". (Codex usage limits require a paid ChatGPT plan that includes Codex.)
4. After a successful login the CLI writes your tokens to `~/.codex/auth.json` (on Windows: `%USERPROFILE%\.codex\auth.json`).
5. Copy that file into the project root, next to `codex.py`, keeping the exact name `auth.json`.
6. Set `ENABLE_CODEX = True` in `main.py` and run the script. The dashboard reads `auth.json`, fetches your usage, and from then on refreshes and rotates the tokens automatically, updating auth.json in place.

### Claude Code
1. Run the `main.py` script from the terminal for the first time.
2. The script will pause, ask for your to copy the authorization URL and paste it on real browser.
3. Open that URL in your browser, click "Authorize", and you will be redirected to a dead `localhost` page.
7. Copy the whole URL containing `code=...` portion from your browser's address bar and paste it back into the terminal. The script will automatically fetch and save the required tokens to `claude_creds.json`.

### Strava
1. Go to your Strava API Settings and create an API Application. **Now only payed accounts supported**
2. Note down your **Client ID** and **Client Secret**.
3. Run the `main.py` script from the terminal for the first time.
4. The script will pause, ask for your ID/Secret, and print an authorization URL in the console. 
5. Open that URL in your browser, click "Authorize", and you will be redirected to a dead `localhost` page.
6. Copy the `code=...` portion from your browser's address bar and paste it back into the terminal. The script will automatically fetch and save the required `activity:read_all` tokens to `strava_token.json`.

### Roborock
1. Open `main.py` and input your Roborock account email address in the `ROBOROCK_CONF` dictionary.
2. Run the script from the terminal.
3. The script will request an OTP (One-Time Password) which will be sent to your email.
4. Enter the 6-digit code in the terminal. The script will securely save your session data locally.

### Bambu Lab 3D Printer
**You DON'T need to enable "LAN Mode" on your Bambu Lab printer to access local data.**
1. On your printer's screen, go to **Settings -> Network**.
2. Note your printer's **IP Address**, **Serial Number**, and **Access Code**. (Force on your router to map exact IP address)
3. Update the `PRINTER_CONF` dictionary in the script with these local credentials.

### Spotify
Uses Spotify's own Web API directly (no Last.fm, no local web server needed).
1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create an app.
   Add `https://127.0.0.1` as a Redirect URI in the app's settings (any App name/description is fine).
   Spotify's dashboard now rejects `http://localhost` (insecure hostname) and even plain
   `http://127.0.0.1` (insecure scheme) — it currently wants exactly the `https://` IP literal above
   (`main.py`'s `SPOTIFY_REDIRECT_URI` matches this). No real HTTPS server is needed for this to work:
   nothing is listening there at all, so the browser's connection fails before any TLS handshake.
2. Note the app's **Client ID** and **Client Secret**.
3. Set `ENABLE_SPOTIFY = True` in `main.py` and run it (`python main.py` on the Pi, or
   `python render_preview.py` on Windows/dev machines — both trigger the same one-time setup).
4. The script pauses, asks for your Client ID/Secret, then prints an authorization URL.
   Open it in your browser, click "Agree", and you'll be redirected to a dead `https://127.0.0.1` page.
5. Copy the whole URL containing `code=...` from your browser's address bar and paste it back into
   the terminal. The script fetches and saves the tokens to `spotify_token.json`, and refreshes them
   automatically in the background from then on — no further logins needed.

### Email (Gmail or Outlook — pick one)
The "Unread Today" widget reads from whichever provider `EMAIL_PROVIDER` in `main.py` names —
`"gmail"`, `"outlook"`, or `None` to disable it. Only one can be active at a time. Set up
whichever one you actually use; the widget itself is always visible either way (it just shows
`0` until configured).

#### Gmail
1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials), create a project
   (or use an existing one), and enable the **Gmail API** for it.
2. Create an OAuth Client ID credential, type **Desktop app**. Note the **Client ID** and **Client Secret**
   shown — no file download or redirect URI setup needed for this credential type.
3. Set `EMAIL_PROVIDER = "gmail"` in `main.py` and run it (`python main.py` on the Pi, or
   `python render_preview.py` on Windows/dev machines — both trigger the same one-time setup).
4. The script pauses, asks for your Client ID/Secret, then prints an authorization URL.
   Open it in your browser, click "Allow", and you'll be redirected to a dead `http://127.0.0.1` page.
5. Copy the whole URL containing `code=...` from your browser's address bar and paste it back into
   the terminal. The script fetches and saves the tokens to `token.json`, and refreshes them
   automatically in the background from then on — no further logins needed.

#### Outlook / Microsoft 365
**If this is your work/school (M365) account, read this first:** your organisation's IT policy
(Conditional Access, tenant-wide app consent restrictions) can block a personal Azure app
registration entirely, independent of anything below. If step 4 fails with an admin-consent or
blocked-by-policy error, that's IT policy — ask your admin, or use a personal Microsoft account
(`@outlook.com`/`@hotmail.com`) instead if that's an option for you.
1. Go to the [Azure Portal](https://portal.azure.com) → Azure Active Directory → App registrations
   → New registration. Under "Supported account types" choose **"Personal Microsoft accounts and
   work/school accounts"**.
2. On the app's page, go to Authentication → Add a platform → **"Mobile and desktop applications"**
   → check/add the redirect URI `http://localhost` (one of Azure's own suggested values).
3. Go to API permissions → Add a permission → Microsoft Graph → Delegated permissions →
   search for and add **`Mail.Read`**.
4. Note the **Application (client) ID** from the app's Overview page — no client secret needed
   (this app type authenticates via PKCE instead, so there's nothing else to copy).
5. Set `EMAIL_PROVIDER = "outlook"` in `main.py` and run it (`python main.py` on the Pi, or
   `python render_preview.py` on Windows/dev machines).
6. The script pauses, asks for the Client ID, then prints an authorization URL. Open it in your
   browser, click "Accept", and you'll be redirected to a dead `http://localhost` page.
7. Copy the whole URL containing `code=...` from your browser's address bar and paste it back into
   the terminal. The script fetches and saves the tokens to `outlook_token.json`, and refreshes
   them automatically in the background from then on — no further logins needed.

### Tasks widget filler: News or NASA, plus her calendar (always on)
The Tasks widget (`ENABLE_TODO`) shows a fixed `TODO_TASKS_PER_PAGE` (3) task rows, with a filler
carousel taking up the fixed amount of space below them — both are fixed-size blocks, neither one
growing or shrinking based on how many real tasks come back. Fewer than 3 tasks just leaves the
remaining rows blank; more than 3 pages through 3 at a time every `TODO_TASK_ROTATE_SECONDS` (60s).

The carousel combines two things: whichever of News or NASA `TODO_FILLER_WIDGET` in `main.py`
names (`"news"` or `"nasa"`, defaults to `"news"`) — only one of those two is active at a time, you
only need to set up the one you use — **plus** her next up-to-3 upcoming calendar events, which
always join the rotation regardless of that setting. It rotates through one slide at a time (a
headline, the NASA photo, or one calendar event) every 60 seconds.

#### News (`TODO_FILLER_WIDGET = "news"`)
Contributes her next 5 Australian headlines to the carousel, one slide each. Uses
[newsdata.io](https://newsdata.io/), not NewsAPI.org (used earlier in this project, dropped after it
stopped returning usable results in testing).
1. Get a free API key from [newsdata.io](https://newsdata.io/) (instant, no approval wait).
2. Create `news_api_config.json` in the project root (gitignored, same as every other credential
   file here):
   ```json
   { "api_key": "your-key-here" }
   ```
3. No further setup — `update_data_thread` starts polling it automatically once the file exists
   (every 4 hours). Without it (or on a fetch failure), those 5 slides just aren't offered — the
   carousel still shows calendar events if there are any, and only falls back to a cheerful face
   per empty row if there's nothing from either source. Each fetched batch of 5 is also pushed to
   the companion app so she can read past a headline there — the e-ink screen only ever shows the
   title.

#### NASA Astronomy Picture of the Day (`TODO_FILLER_WIDGET = "nasa"`)
Contributes NASA's photo of the day to the carousel as a single slide, cropped to fill the filler
block. When no calendar events are upcoming it's the only slide and fills the block edge-to-edge;
when calendar events share the rotation, it shrinks slightly to leave room for the position dots.
1. Get a free API key from [api.nasa.gov](https://api.nasa.gov/) (instant, no approval wait).
2. Create `nasa_apod_config.json` in the project root (gitignored):
   ```json
   { "api_key": "your-key-here" }
   ```
3. No further setup — same automatic polling as News above. Without it, on a fetch failure, or on a
   day NASA publishes a video instead of a photo, that slide just isn't offered — same fallback
   behaviour as News above.

#### Calendar (always on, not a `TODO_FILLER_WIDGET` option)
Contributes her next up-to-3 upcoming iPhone calendar events to the carousel, one slide each,
regardless of whether News or NASA is selected above. Unlike News/NASA, there's no third-party API
key to configure — the source of truth is her iPhone's own Calendar app, which reaches the
dashboard via an iPhone Shortcuts automation rather than a direct fetch:
1. On her iPhone, open the **Shortcuts** app and create a new automation (e.g. hourly, or "when I
   leave home") that:
   - Runs **Find Calendar Events** (sorted soonest-first, limited to the next few).
   - Builds a JSON array of `{"title": ..., "start_time": ...}` objects (an ISO 8601 timestamp with
     UTC offset, e.g. `2026-08-27T15:00:00+10:00`) from the results.
   - `POST`s that array to `https://YOUR-APP.pages.dev/api/calendar-events` with the same
     `X-Api-Key` header/value already in `waveshare_api_config.json`.
2. The companion app stores just the latest snapshot (trimmed to the 3 soonest events) — no
   history, no separate database table to manage on the Pi's side.
3. No further setup on the Pi — it polls that stored snapshot automatically once
   `waveshare_api_config.json` exists (the same file the todos/water/widget-config endpoints already
   use). Without it, on a fetch failure, or before her Shortcut has run for the first time, those
   slides just aren't offered — same fallback behaviour as News/NASA above.

---

## Running the Dashboard

To ensure the dashboard continues running even after you close your SSH connection, use `tmux`.

1. Start a new tmux session:
```shell
tmux new -s dashboard
```

2. Run the script inside the tmux session:
```shell
python3 main.py
```

3. Detach from the session (leave it running in the background) by pressing:
`Ctrl+B`, then release and press `D`.

To reattach to the session later and view the logs or stop the script:
```shell
tmux attach -t dashboard
```

### Forcing an immediate refresh

Every widget's data normally refreshes on its own schedule (weather every 10 minutes, todos every
2 minutes, water status every 20 seconds, etc.) — fine most of the time, but not if you just fixed
a config file or she just logged water and you want the screen to reflect it right now, without
waiting out that interval or restarting the whole dashboard (which would also briefly blank the
screen). While `main.py` is running, from another SSH session:
```shell
./force_refresh.sh
```
This forces every currently-enabled data source to re-fetch on its very next pass (within about a
second) — the same full-refresh behaviour `render_preview.py` always gets, just triggered on demand
against the live process instead of only at startup.

Charlotte can trigger the same thing herself from the companion app, without needing SSH access —
whatever button the app exposes for it calls `POST /api/force-refresh`, which the Pi polls every 15
seconds (same `waveshare_api_config.json` every other companion-app endpoint already uses).

## How It Works

The dashboard is built on a robust, multi-threaded architecture designed to keep the UI responsive and prevent hardware lockups.

* **Asynchronous Data Fetching:** Instead of fetching all data sequentially, the script spawns dedicated background threads. Each service (Weather, Strava, Roborock, Bambu Lab, etc.) pulls data asynchronously at its own specific interval. This ensures that a slow API response or a temporary network drop from one service will never block the others or freeze the system.
* **Scheduled Rendering:** The main application loop acts purely as a renderer. It collects the latest available information from a thread-safe global data store and pushes a new frame to the e-ink display exactly once per minute using a partial screen refresh. 

**Important Notes:**

* **Initial Data Population Delay:** When you first launch the script, you will notice that the widgets may show placeholders or zeros, and the full array of data takes a few minutes to completely appear on the screen. This is an intentional design choice to stagger initial network requests. It prevents sudden spikes in CPU usage, avoids overwhelming the Raspberry Pi's network stack, and respects the rate limits of the external APIs.
* **Hardware Refresh Limits:** The 60-second rendering interval is strictly enforced. Refreshing the screen more frequently than once a minute is strongly discouraged by the display manufacturer (Waveshare). Aggressive refresh rates on large e-paper panels can lead to severe ghosting and may cause permanent hardware damage to the display.

## The 3d printed case

You can download the case stl files [here](https://makerworld.com/en/models/2322517-epaper-dashboard-waveshare-10-85).

## Video assembly guide

**(Youtube clickable)**

[![Video Title](https://img.youtube.com/vi/H964RpaJvu0/0.jpg)](https://youtu.be/H964RpaJvu0)
