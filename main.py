#!/usr/bin/python3
# -*- coding:utf-8 -*-
import sys
import os
import time
import logging
import threading
import requests
import io
import gc
import socket
import signal
import json
import asyncio
import pickle
import subprocess
import math
import random
import urllib.parse
import hashlib
import base64
import secrets
from collections import deque
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
from logging.handlers import RotatingFileHandler

# --- GMAIL IMPORTS ---
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# --- SYSTEM LIMITS ---
# `resource` is POSIX-only (no such module on Windows), so the rendering
# path can run on Windows for local PNG previews without it.
try:
    import resource
except ImportError:
    resource = None

try:
    if resource:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
except Exception as e:
    print(f"Failed to set rlimit: {e}")

# --- PATHS ---
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(BASE_DIR, 'lib')
FONT_DIR = os.path.join(BASE_DIR, 'fnt')
ICON_DIR = os.path.join(BASE_DIR, 'icons')
LOG_FILE = os.path.join(BASE_DIR, 'dashboard.log')

# --- WIDGET TOGGLES ---
ENABLE_STRAVA = False # For payed tier only
ENABLE_BAMBU = False
ENABLE_ROBOROCK = False
ENABLE_ANTIGRAVITY = False
ENABLE_CODEX = False
ENABLE_CLAUDE = False
ENABLE_SPOTIFY = False
# Which widget the middle column shows: "spotify" or "weather". A single
# mode selector rather than an ENABLE_WEATHER boolean, same reasoning as
# EMAIL_PROVIDER below — exactly one of the two can occupy this slot at a
# time. Local placeholder for now, matching the companion app's own
# Spotify/Weather switcher — intended to eventually be set remotely via
# /api/widget-config instead of edited by hand, see CLAUDE.md roadmap.
MIDDLE_COLUMN_WIDGET = "spotify"
# Temporarily overlays the hydration/growth widget on top of whichever of
# Spotify/Weather is active in the middle column for WATER_SHOW_SECONDS
# right after a fresh water log arrives from the companion app, then
# reverts to that widget on its own — see WATER_GROWTH_TARGET_LITRES below
# and CLAUDE.md for how the timing and growth scale work. Takes priority
# over both Spotify and Weather regardless of MIDDLE_COLUMN_WIDGET.
ENABLE_WATER = False
# The left column shows the Tasks widget instead of System Load/Crypto/Ping
# (and their Strava/Bambu/Roborock/Antigravity/Codex alternates) while this
# is True — same "one column, one active widget" pattern as
# MIDDLE_COLUMN_WIDGET. Placeholder data only for now (TODO_PLACEHOLDER_TASKS
# below) — real data will come from the companion app's GET /api/top-todos
# once it exists, see CLAUDE.md roadmap.
ENABLE_TODO = True
# Which email account the "Unread Today" widget reads from: "gmail",
# "outlook", or None to disable it (shows 0, makes no API calls at all — the
# widget itself is otherwise unconditional and always visible). A single
# mode selector rather than two independent ENABLE_* flags, since exactly
# one provider (or none) can be active at a time — two booleans would allow
# an ambiguous "both on" state this can't. Only gates the interactive
# one-time auth prompts (auth_gmail()/auth_outlook()) so a fresh checkout
# never blocks on input(); update_data_thread's fetch already self-gates on
# whichever provider's token file exists, same as before this flag existed.
# Defaults to None (like every other ENABLE_* flag defaults False) so a
# fresh checkout never prompts — set to "gmail" or "outlook" locally once
# you've set up that provider's credentials. Intended to eventually be set
# remotely via the companion app's /api/widget-config endpoint instead of
# edited by hand — see CLAUDE.md.
EMAIL_PROVIDER = None

# --- API ENDPOINTS ---
API_ENDPOINTS = {
    'weather': 'https://api.open-meteo.com/v1/forecast',
    'aqi': 'https://air-quality-api.open-meteo.com/v1/air-quality',
    'strava_token': 'https://www.strava.com/oauth/token',
    'strava_auth': 'https://www.strava.com/oauth/authorize',
    'strava_activities': 'https://www.strava.com/api/v3/athlete/activities',
    'btc': 'https://api.coingecko.com/api/v3/coins/bitcoin/market_chart',
    'eth': 'https://api.coingecko.com/api/v3/coins/ethereum/market_chart',
    'spotify_auth': 'https://accounts.spotify.com/authorize',
    'spotify_token': 'https://accounts.spotify.com/api/token',
    'spotify_player': 'https://api.spotify.com/v1/me/player/currently-playing',
    'gmail_auth': 'https://accounts.google.com/o/oauth2/v2/auth',
    'gmail_token': 'https://oauth2.googleapis.com/token',
    'outlook_auth': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    'outlook_token': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    'outlook_messages': 'https://graph.microsoft.com/v1.0/me/messages',
    'affirmation': 'https://www.affirmations.dev',
}

# --- CONFIGURATION ---
LOCATION_LAT = 44.8140857
LOCATION_LON = 20.3934271

GREETING_NAME = "Charlotte"
# Occasionally swap the Morning/Afternoon/Evening word for "hello" in another
# language. Each entry is (word, font_key): Latin-script words use font key
# '35' (Aldrich-Regular.ttc); non-Latin ones use 'cjk_greeting', a small
# bundled subset font (fnt/CJK-Greeting.ttf) since Aldrich has no CJK/kana
# glyphs. See CLAUDE.md for how to add more non-Latin words.
GREETING_INTL_CHANCE = 0.25
GREETING_ZH_HELLO = "嗨"
GREETING_ZH_CHANCE = 0.5  # share of the international slot given to Chinese
GREETING_INTL_HELLOS = [
    ("Bonjour", '35'), ("Hola", '35'), ("Ciao", '35'), ("Hallo", '35'),
    ("Ola", '35'), ("Halo", '35'), ("Hei", '35'), ("こんにちは", 'cjk_greeting'),
    ("Czesc", '35'), ("Merhaba", '35'), ("Kumusta", '35'), ("Habari", '35'),
    ("Sawasdee", '35'), ("Namaste", '35'), ("Salut", '35'),
]
# Small-font line under the greeting: a short positive sentence from the
# affirmations.dev API (keyless, like the weather/crypto widgets). Used as
# the initial value before the first fetch completes, and kept as the
# on-screen value whenever a fetch fails — the API is a small hobby-scale
# service with no uptime guarantee, so a dead/unreachable endpoint should
# never blank the line.
GREETING_FALLBACK_AFFIRMATIONS = [
    "You make every day a little brighter.",
    "Small steps today, big wins ahead.",
    "You are stronger than you know.",
    "Today has good things waiting for you.",
    "You've got this, one step at a time.",
]

# --- TASKS WIDGET ---
# How many task rows the widget always reserves space for, regardless of how
# many real tasks come back — a short list just leaves empty rows rather
# than resizing them, so the layout doesn't jump around. May change later
# once the companion app's GET /api/top-todos is wired in; row height is
# computed from this rather than hardcoded, so changing it needs no other
# edits.
TODO_MAX_TASKS = 5
# Placeholder content until GET /api/top-todos exists (see CLAUDE.md
# roadmap) — a realistic mix of with/without a due time, and done/not-done,
# since "due" is optional per task and completed tasks can still be among
# today's top N (e.g. shown for the rest of the day as a done checkmark).
TODO_PLACEHOLDER_TASKS = [
    {"title": "Finish quarterly report", "due": "2:00 PM", "completed": False},
    {"title": "Buy groceries", "due": None, "completed": True},
    {"title": "Call dentist", "due": "10:30 AM", "completed": True},
    {"title": "Review pull request", "due": None, "completed": False},
    {"title": "Plan weekend trip", "due": "6:00 PM", "completed": False},
]

# --- HYDRATION / GROWTH WIDGET ---
# Growth is a running total since the last completed plant, not a calendar
# window — there's no monthly (or any other) reset on a timer. Once the
# total reaches WATER_GROWTH_TARGET_LITRES, the companion app resets it to
# 0 and bumps plants_grown_lifetime, however long that took; a slow week
# doesn't lose progress the way a calendar-month reset would. The companion
# app computes this running total server-side (see CLAUDE.md roadmap); the
# Pi only ever displays whatever total it's given, no date math here.
# 40L at 0.5L per logged step gives 80 distinct steps from seed to full
# growth — plenty of resolution for a plant meant to take a while to grow,
# though the drawing itself scales continuously off the fraction rather
# than switching between 80 discrete images.
WATER_GROWTH_TARGET_LITRES = 40.0  # litres for a fully-grown plant
# How long the widget stays up after a fresh log before reverting to
# Spotify. Deliberately matched to main()'s existing ~60s screen-redraw
# cadence rather than a dedicated timer/thread: each redraw just checks
# whether the last log happened within this window, so the widget naturally
# appears on the next redraw after a log and disappears on the one after
# that — no extra scheduling needed. One consequence of piggybacking on that
# cadence: it can take up to ~60s after the real-world log for the widget to
# first appear (whichever redraw catches it), not instantaneously.
WATER_SHOW_SECONDS = 60

PRINTER_CONF = {
    'IP': '192.168....',
    'SERIAL': '....',
    'ACCESS_CODE': '....'
}

ROBOROCK_CONF = {
    'EMAIL': 'your@email.com'
}

# Client ID/secret are entered once interactively (auth_spotify()) and
# persisted alongside the tokens in TOKEN_FILE, the same pattern STRAVA_CONF
# below uses — nothing to fill in here.
SPOTIFY_CONF = {
    'TOKEN_FILE': os.path.join(BASE_DIR, 'spotify_token.json')
}
# Spotify's dashboard rejects both the hostname "localhost" (flagged "not
# secure") and plain http:// on the loopback IP — it currently requires
# https://127.0.0.1 specifically. That's fine even without a real TLS
# listener: since nothing is listening at all, the browser's connection
# fails before any handshake, so no certificate is ever needed — the address
# bar still shows the attempted URL with ?code=... intact. Must match
# exactly what's registered in the Spotify Dashboard app.
SPOTIFY_REDIRECT_URI = "https://127.0.0.1"
# Album art is fetched at this size so the dithering is computed for the
# widget's actual display size, not upscaled afterwards (which would smear
# an already-dithered 1-bit image).
SPOTIFY_ART_SIZE = 190

STRAVA_CONF = {
    'TOKEN_FILE': os.path.join(BASE_DIR, 'strava_token.json')
}

# Companion app (Cloudflare Pages + D1) — see CLAUDE.md's "Companion web
# app" section. Unlike the OAuth widgets above, there's no token exchange:
# CONFIG_FILE holds the static shared secret plus the app's base URL,
# entered once by hand rather than obtained via a browser flow. Gitignored,
# same as every other credential in this file — a fresh checkout has no
# such file, which is what makes fetch_todos_data()/fetch_widget_config_data()
# degrade to "leave data_store as-is" below rather than crashing.
WAVESHARE_API_CONF = {
    'CONFIG_FILE': os.path.join(BASE_DIR, 'waveshare_api_config.json')
}

# --- FILES & SCOPES ---
GMAIL_TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')
ROBOROCK_TOKEN_FILE = os.path.join(BASE_DIR, 'roborock_session.pkl')
ROBOROCK_STATS_FILE = os.path.join(BASE_DIR, 'roborock_stats.json')
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
# Unlike Spotify, Google's "Desktop app" OAuth client type doesn't require
# pre-registering a redirect URI at all — it auto-accepts loopback addresses,
# and plain http:// (not https://) is fine here since that's exactly what
# Google's own InstalledAppFlow.run_local_server() helper uses internally.
GMAIL_REDIRECT_URI = "http://127.0.0.1"

OUTLOOK_TOKEN_PATH = os.path.join(BASE_DIR, 'outlook_token.json')
OUTLOOK_SCOPES = "offline_access Mail.Read"
# Matches the exact value Azure's "Mobile and desktop applications" platform
# type offers as a one-click suggested redirect URI, to sidestep a repeat of
# Spotify's redirect-URI back-and-forth. Registering as a public client
# (rather than a confidential/Web client) means no client secret is needed
# at all — auth uses PKCE (see auth_outlook()) instead.
OUTLOOK_REDIRECT_URI = "http://localhost"

if os.path.exists(LIB_DIR):
    sys.path.append(LIB_DIR)

# --- GPIO PIN FACTORY ---
# Prefer the lgpio backend: the native/sysfs factory is broken on Raspberry Pi
# OS Bookworm (kernel 6.x) and fails to export the pins. If lgpio is not
# available (older OS images), fall back to gpiozero's default instead of
# hard-crashing on import.
#
# On import, lgpio creates its ".lgd-nfy*" notification pipe in the current
# working directory. Import from /tmp so it never depends on the permissions
# or location of the project directory, then restore the working directory.
_prev_cwd = os.getcwd()
try:
    os.chdir('/tmp')
    from gpiozero import Device
    from gpiozero.pins.lgpio import LGPIOFactory
    Device.pin_factory = LGPIOFactory()
except Exception as _e:
    print(f"lgpio pin factory unavailable ({_e}); using gpiozero default")
finally:
    os.chdir(_prev_cwd)

try:
    from waveshare_epd import epd10in85
    import bambulabs_api as bl
    from roborock.web_api import RoborockApiClient
    from roborock.devices.device_manager import create_device_manager, UserParams
except Exception:
    # Broader than ImportError deliberately: on non-Linux hosts (e.g.
    # Windows), epdconfig.py's JetsonNano fallback finds the bundled
    # sysfs_software_spi.so (a real file, so os.path.exists() passes) and
    # calls ctypes.cdll.LoadLibrary() on it — a Linux ELF binary, which
    # raises OSError on Windows, not ImportError. This whole block is only
    # ever optional hardware imports, so any failure here is fine to ignore.
    pass

# --- LOGGING ---
logging.getLogger("bambulabs_api").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("roborock").setLevel(logging.CRITICAL)
logging.getLogger("aiomqtt").setLevel(logging.CRITICAL)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=1)
file_handler.setFormatter(formatter)

logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# --- DRIVER DEBUG ---
# Set True to log every stage of the e-paper driver (SPI load per controller,
# refresh, and how long BUSY stays low) to pinpoint where a partial update
# stalls. Only raises the driver's own logger; the rest of the app stays INFO.
EPD_DEBUG = False
if EPD_DEBUG:
    logging.getLogger("waveshare_epd").setLevel(logging.DEBUG)

# --- PANEL RE-INIT STRATEGY ---
# The single-core Pi Zero 1 latches the panel BUSY line after the first partial
# update, so it needs a hardware reset (init_Part) before every frame. Faster
# multi-core boards (Zero 2 W, Pi 3/4/5) do not have this issue, so we skip the
# extra re-init there to avoid needless latency and ghosting. Detected by core
# count: Zero 1 = 1 core, everything newer = 4+.
PANEL_REINIT_EACH_FRAME = (os.cpu_count() or 1) < 2

icon_cache = {}
global_printer = None


class HardwareTimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    raise HardwareTimeoutError("Hardware Busy-Wait Timeout")


# --- ROBUST NETWORK MANAGER ---
class NetworkManager:
    def __init__(self):
        self.session = None
        self.create_session()

    def create_session(self):
        if self.session:
            try:
                self.session.close()
            except:
                pass
        gc.collect()
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5, pool_maxsize=10,
            max_retries=requests.adapters.Retry(total=1, backoff_factor=0.5)
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def get_json(self, url, headers=None, data=None, method='GET', timeout=10):
        try:
            if self.session is None: self.create_session()
            if method == 'POST':
                resp = self.session.post(url, headers=headers, data=data, timeout=timeout)
            else:
                resp = self.session.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.create_session()
            return None

    def get_image(self, url, timeout=15):
        try:
            if self.session is None: self.create_session()
            resp = self.session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            self.create_session()
            return None


net = NetworkManager()


# --- GLOBAL DATA STORE ---
class DataStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.weather = {}
        self.aqi = 0
        self.strava = {
            'rides': 0, 'total_distance': 0,
            'rides_curr': 0, 'distance_curr': 0,
            'rides_prev': 0, 'distance_prev': 0,
            'bike_total': 0, 'hike_total': 0
        }
        self.printer = {'status': 'OFFLINE'}
        self.email_unread_today = 0
        self.spotify = {'status': 'PAUSED', 'text': '', 'cover': None}
        self.claude = {'error': False, 'five_hour': {}, 'seven_day': {}}
        self.antigravity = {'error': False, 'models': []}
        self.codex = {'error': False, 'five_hour': {}, 'seven_day': {}}
        self.roborock = {
            'status': 'OFFLINE', 'battery': 0, 'is_cleaning': False,
            'current_area': 0.0, 'ref_area': 0.0, 'pct': 0.0, 'last_date': '-'
        }
        self.sysload = {'cpu': 0, 'ram_free': 0, 'history': deque(maxlen=30)}
        self.crypto = {'btc': 0, 'eth': 0, 'btc_hist': [], 'eth_hist': []}
        self.ping = {'current': 0, 'history': deque(maxlen=50)}
        self.affirmation = random.choice(GREETING_FALLBACK_AFFIRMATIONS)
        self.todos = list(TODO_PLACEHOLDER_TASKS)
        # last_logged_at is a Unix timestamp (seconds); None means "no log
        # seen yet", which always keeps the widget hidden — a safe default
        # until GET /api/water-status is wired in (see CLAUDE.md roadmap).
        # progress_litres resets to 0 whenever it reaches
        # WATER_GROWTH_TARGET_LITRES, however long that took — not tied to
        # a calendar month. plants_grown_lifetime is a running count of how
        # many times that's happened across all time — computed
        # server-side, same as progress_litres itself.
        self.water = {
            'progress_litres': 0.0, 'last_logged_at': None,
            'last_amount_litres': None, 'plants_grown_lifetime': 0
        }

        self.last_update = {
            'weather': 0, 'strava': 0, 'printer': 0, 'email': 0,
            'spotify': 0, 'crypto': 0, 'sysload': 0, 'ping': 0,
            'claude': 0, 'antigravity': 0, 'codex': 0, 'affirmation': 0,
            'todos': 0, 'widget_config': 0
        }


data_store = DataStore()


# --- HELPERS ---
def ping_printer(ip):
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '1', ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except:
        return False


def get_cached_icon(name, size, is_white=False):
    key = f"{name}_{size[0]}x{size[1]}_{'white' if is_white else 'black'}"
    if key not in icon_cache:
        path = os.path.join(ICON_DIR, f"{name}.bmp")
        if os.path.exists(path):
            try:
                with Image.open(path) as f_img:
                    img = f_img.convert("L").resize(size)
                    img = ImageOps.invert(img)
                    icon_cache[key] = img.convert("1")
            except:
                return None
        else:
            icon_cache[key] = None
    return icon_cache.get(key)


def time_until(iso_str):
    if not iso_str: return "N/A"
    try:
        # Handling the explicit +00:00 timezone format
        target = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        diff = target - now
        if diff.total_seconds() < 0: return "Resetting..."
        hours, rem = divmod(diff.total_seconds(), 3600)
        days, hours = divmod(hours, 24)
        if days > 0:
            return f"{int(days)}d {int(hours)}h"
        else:
            minutes = rem // 60
            return f"{int(hours)}h {int(minutes)}m"
    except Exception:
        return "N/A"


# --- AUTH & FETCH THREADS ---

def auth_claude():
    global ENABLE_CLAUDE
    if not ENABLE_CLAUDE: return
    try:
        import claude
        success = claude.interactive_auth()
        if not success:
            ENABLE_CLAUDE = False
            print("Claude widget is disabled.")
    except ImportError:
        print("claude.py not found. Claude widget disabled.")
        ENABLE_CLAUDE = False


def auth_codex():
    global ENABLE_CODEX
    if not ENABLE_CODEX: return
    # codex.py has no interactive login; it just needs a valid auth.json that
    # the user provides. Disable the widget if either file is missing.
    if not os.path.exists(os.path.join(BASE_DIR, 'codex.py')):
        print("codex.py not found. Codex widget disabled.")
        ENABLE_CODEX = False
        return
    if not os.path.exists(os.path.join(BASE_DIR, 'auth.json')):
        print("auth.json not found. Codex widget disabled.")
        ENABLE_CODEX = False


def auth_antigravity():
    global ENABLE_ANTIGRAVITY
    if not ENABLE_ANTIGRAVITY: return
    try:
        import antigravity
        success = antigravity.interactive_auth()
        if not success:
            ENABLE_ANTIGRAVITY = False
            print("Antigravity widget is disabled.")
    except ImportError:
        print("antigravity.py not found. Antigravity widget disabled.")
        ENABLE_ANTIGRAVITY = False


def auth_strava():
    global ENABLE_STRAVA
    if not ENABLE_STRAVA: return

    if os.path.exists(STRAVA_CONF['TOKEN_FILE']):
        return

    print("\n--- STRAVA CONFIGURATION REQUIRED ---")
    c_id = input("Enter Strava Client ID (or press Enter to disable): ").strip()
    if not c_id:
        print("Strava is disabled. Fallback widget (System Load) will be used.\n")
        ENABLE_STRAVA = False
        return

    c_secret = input("Enter Strava Client Secret: ").strip()

    auth_url = (
        f"{API_ENDPOINTS['strava_auth']}?"
        f"client_id={c_id}&"
        f"response_type=code&"
        f"redirect_uri=http://localhost&"
        f"approval_prompt=force&"
        f"scope=activity:read_all"
    )

    print("\n[!] To get a token with the correct permissions, open this link in your browser:\n")
    print(f"--> {auth_url} <--\n")
    print("Click 'Authorize'. You will be redirected to an empty/error page (localhost).")
    print("Look at the address bar. Copy the 'code' parameter.")

    code_input = input("Enter the 'code' from the URL (or paste the full URL): ").strip()

    if not code_input:
        print("Authorization cancelled. Strava is disabled.\n")
        ENABLE_STRAVA = False
        return

    if 'code=' in code_input:
        try:
            parsed = urllib.parse.urlparse(code_input)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get('code', [code_input])[0]
        except:
            code = code_input.split('code=')[1].split('&')[0]
    else:
        code = code_input

    print("Fetching Access Token...")
    data = {'client_id': c_id, 'client_secret': c_secret, 'code': code, 'grant_type': 'authorization_code'}

    try:
        resp = requests.post(API_ENDPOINTS['strava_token'], data=data)
        resp.raise_for_status()
        token_data = resp.json()
        token_data['client_id'] = c_id
        token_data['client_secret'] = c_secret

        with open(STRAVA_CONF['TOKEN_FILE'], 'w') as f:
            json.dump(token_data, f, indent=4)
        print("Strava Authorization Successful!\n")
    except Exception as e:
        print(f"Failed to fetch Strava tokens: {e}")
        ENABLE_STRAVA = False


def fetch_strava_data():
    if not os.path.exists(STRAVA_CONF['TOKEN_FILE']): return None
    with open(STRAVA_CONF['TOKEN_FILE'], 'r') as f:
        token_data = json.load(f)

    c_id = token_data.get('client_id')
    c_secret = token_data.get('client_secret')

    if time.time() > token_data.get('expires_at', 0):
        data = {'client_id': c_id, 'client_secret': c_secret, 'grant_type': 'refresh_token',
                'refresh_token': token_data.get('refresh_token')}
        new_token = net.get_json(API_ENDPOINTS['strava_token'], data=data, method='POST')
        if new_token and 'access_token' in new_token:
            new_token['client_id'] = c_id
            new_token['client_secret'] = c_secret
            token_data = new_token
            with open(STRAVA_CONF['TOKEN_FILE'], 'w') as f:
                json.dump(token_data, f, indent=4)
        else:
            return None

    access_token = token_data['access_token']

    now_year = datetime.now().year
    start_curr_ts = datetime(now_year, 1, 1).timestamp()
    start_prev_ts = datetime(now_year - 1, 1, 1).timestamp()
    end_prev_ts = datetime(now_year - 1, 12, 31, 23, 59, 59).timestamp()

    page = 1
    total_rides, total_dist = 0, 0
    rides_curr, dist_curr = 0, 0
    rides_prev, dist_prev = 0, 0
    bike_total, hike_total = 0, 0

    headers = {"Authorization": f"Bearer {access_token}"}

    while True:
        url = f"{API_ENDPOINTS['strava_activities']}?page={page}&per_page=100"
        activities = net.get_json(url, headers=headers)
        if not activities: break

        for act in activities:
            t = act.get('type')
            d = act.get('distance', 0)
            act_time = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()

            if t in ['Ride', 'VirtualRide', 'EBikeRide', 'GravelRide', 'MountainBikeRide']:
                total_rides += 1
                total_dist += d
                bike_total += d
                if act_time >= start_curr_ts:
                    rides_curr += 1
                    dist_curr += d
                elif start_prev_ts <= act_time <= end_prev_ts:
                    rides_prev += 1
                    dist_prev += d
            elif t in ['Hike', 'Walk']:
                hike_total += d

        if len(activities) < 100: break
        page += 1

    return {
        "rides": total_rides,
        "total_distance": round(total_dist / 1000, 1),
        "rides_curr": rides_curr,
        "distance_curr": round(dist_curr / 1000, 1),
        "rides_prev": rides_prev,
        "distance_prev": round(dist_prev / 1000, 1),
        "bike_total": round(bike_total / 1000, 1),
        "hike_total": round(hike_total / 1000, 1)
    }


def auth_spotify():
    global ENABLE_SPOTIFY
    if not ENABLE_SPOTIFY: return

    if os.path.exists(SPOTIFY_CONF['TOKEN_FILE']):
        return

    print("\n--- SPOTIFY CONFIGURATION REQUIRED ---")
    print("Register an app at https://developer.spotify.com/dashboard first")
    print(f"(add {SPOTIFY_REDIRECT_URI} as a Redirect URI there — Spotify rejects")
    print("both http://localhost and plain http://127.0.0.1 as insecure now,")
    print("it must be exactly the https:// IP literal above).\n")
    c_id = input("Enter Spotify Client ID (or press Enter to disable): ").strip()
    if not c_id:
        print("Spotify is disabled. Fallback (Nothing Playing) will be used.\n")
        ENABLE_SPOTIFY = False
        return

    c_secret = input("Enter Spotify Client Secret: ").strip()

    auth_url = (
        f"{API_ENDPOINTS['spotify_auth']}?"
        f"client_id={c_id}&"
        f"response_type=code&"
        f"redirect_uri={urllib.parse.quote(SPOTIFY_REDIRECT_URI, safe='')}&"
        f"scope=user-read-currently-playing"
    )

    print("\n[!] To authorize, open this link in your browser:\n")
    print(f"--> {auth_url} <--\n")
    print(f"Click 'Agree'. You will be redirected to an empty/error page ({SPOTIFY_REDIRECT_URI}).")
    print("Look at the address bar. Copy the 'code' parameter.")

    code_input = input("Enter the 'code' from the URL (or paste the full URL): ").strip()

    if not code_input:
        print("Authorization cancelled. Spotify is disabled.\n")
        ENABLE_SPOTIFY = False
        return

    if 'code=' in code_input:
        try:
            parsed = urllib.parse.urlparse(code_input)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get('code', [code_input])[0]
        except:
            code = code_input.split('code=')[1].split('&')[0]
    else:
        code = code_input

    print("Fetching Access Token...")
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': SPOTIFY_REDIRECT_URI,
        'client_id': c_id,
        'client_secret': c_secret,
    }

    try:
        resp = requests.post(API_ENDPOINTS['spotify_token'], data=data)
        resp.raise_for_status()
        token_data = resp.json()
        token_data['client_id'] = c_id
        token_data['client_secret'] = c_secret
        token_data['expires_at'] = time.time() + token_data.get('expires_in', 3600)

        with open(SPOTIFY_CONF['TOKEN_FILE'], 'w') as f:
            json.dump(token_data, f, indent=4)
        print("Spotify Authorization Successful!\n")
    except Exception as e:
        print(f"Failed to fetch Spotify tokens: {e}")
        ENABLE_SPOTIFY = False


def fetch_spotify_data():
    if not os.path.exists(SPOTIFY_CONF['TOKEN_FILE']): return None
    with open(SPOTIFY_CONF['TOKEN_FILE'], 'r') as f:
        token_data = json.load(f)

    c_id = token_data.get('client_id')
    c_secret = token_data.get('client_secret')

    if time.time() > token_data.get('expires_at', 0):
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': token_data.get('refresh_token'),
            'client_id': c_id,
            'client_secret': c_secret,
        }
        new_token = net.get_json(API_ENDPOINTS['spotify_token'], data=data, method='POST')
        if new_token and 'access_token' in new_token:
            token_data['access_token'] = new_token['access_token']
            token_data['expires_at'] = time.time() + new_token.get('expires_in', 3600)
            # Spotify doesn't always issue a new refresh_token on refresh —
            # only overwrite ours if it actually sent one.
            if 'refresh_token' in new_token:
                token_data['refresh_token'] = new_token['refresh_token']
            with open(SPOTIFY_CONF['TOKEN_FILE'], 'w') as f:
                json.dump(token_data, f, indent=4)
        else:
            return None

    headers = {"Authorization": f"Bearer {token_data['access_token']}"}

    # Not net.get_json(): Spotify returns 204 No Content (empty body, not an
    # error) when nothing is playing, which get_json's resp.json() call would
    # turn into a swallowed exception indistinguishable from a real failure —
    # and unlike a real failure, "nothing playing" must still update the
    # widget (otherwise pausing would leave stale "still playing" text on
    # screen indefinitely).
    try:
        resp = net.session.get(API_ENDPOINTS['spotify_player'], headers=headers, timeout=8)
    except Exception as e:
        logging.error(f"Spotify player fetch error: {e}")
        return None

    if resp.status_code == 204:
        return {'status': 'PAUSED', 'text': '', 'cover': None}
    if resp.status_code == 401:
        logging.error("Spotify token rejected (401) - re-run auth_spotify() to re-authorize.")
        return None
    if resp.status_code != 200:
        logging.error(f"Spotify player fetch failed: HTTP {resp.status_code}")
        return None

    try:
        payload = resp.json()
    except Exception:
        return None

    if not payload.get('is_playing') or not payload.get('item'):
        return {'status': 'PAUSED', 'text': '', 'cover': None}

    item = payload['item']
    track_name = item.get('name', 'Unknown')
    artist = ", ".join(a.get('name', '') for a in item.get('artists', [])) or "Unknown"

    images = item.get('album', {}).get('images', [])
    img_url = ""
    # Spotify lists a few sizes (typically 640/300/64px) — pick the smallest
    # one that still covers our target display size, to avoid downloading a
    # bigger image than we'll actually use.
    for img in sorted(images, key=lambda i: i.get('width', 0)):
        if img.get('width', 0) >= SPOTIFY_ART_SIZE:
            img_url = img.get('url', '')
            break
    if not img_url and images:
        img_url = images[0].get('url', '')

    cover_dithered = None
    if img_url:
        img_bytes = net.get_image(img_url)
        if img_bytes:
            img_pil = Image.open(io.BytesIO(img_bytes)).convert("L").resize(
                (SPOTIFY_ART_SIZE, SPOTIFY_ART_SIZE))
            enhancer = ImageEnhance.Contrast(img_pil)
            img_pil = enhancer.enhance(3.0)
            cover_dithered = img_pil.convert("1", dither=Image.NONE)

    return {'status': 'PLAYING', 'text': f"{artist} - {track_name}", 'cover': cover_dithered}


def auth_gmail():
    if EMAIL_PROVIDER != 'gmail': return

    if os.path.exists(GMAIL_TOKEN_PATH):
        return

    print("\n--- GMAIL CONFIGURATION REQUIRED ---")
    print("Create an OAuth Client ID (type: Desktop app) at")
    print("https://console.cloud.google.com/apis/credentials first —")
    print("enable the Gmail API on that project if you haven't already.\n")
    c_id = input("Enter Google Client ID (or press Enter to disable): ").strip()
    if not c_id:
        print("Gmail is disabled. Unread count will stay at 0.\n")
        return

    c_secret = input("Enter Google Client Secret: ").strip()

    auth_url = (
        f"{API_ENDPOINTS['gmail_auth']}?"
        f"client_id={c_id}&"
        f"response_type=code&"
        f"redirect_uri={urllib.parse.quote(GMAIL_REDIRECT_URI, safe='')}&"
        f"scope={urllib.parse.quote(' '.join(GMAIL_SCOPES), safe='')}&"
        f"access_type=offline&"
        f"prompt=consent"
    )

    print("\n[!] To authorize, open this link in your browser:\n")
    print(f"--> {auth_url} <--\n")
    print(f"Click 'Allow'. You will be redirected to an empty/error page ({GMAIL_REDIRECT_URI}).")
    print("Look at the address bar. Copy the 'code' parameter.")

    code_input = input("Enter the 'code' from the URL (or paste the full URL): ").strip()

    if not code_input:
        print("Authorization cancelled. Unread count will stay at 0.\n")
        return

    if 'code=' in code_input:
        try:
            parsed = urllib.parse.urlparse(code_input)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get('code', [code_input])[0]
        except:
            code = code_input.split('code=')[1].split('&')[0]
    else:
        code = code_input

    print("Fetching Access Token...")
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': GMAIL_REDIRECT_URI,
        'client_id': c_id,
        'client_secret': c_secret,
    }

    try:
        resp = requests.post(API_ENDPOINTS['gmail_token'], data=data)
        resp.raise_for_status()
        token_data = resp.json()

        # Build via Credentials + to_json() rather than hand-writing the
        # JSON, so the file's shape/date-format is guaranteed to match what
        # Credentials.from_authorized_user_file() (used by the fetch code
        # below) expects — it's the exact same round-trip that code already
        # does on every token refresh.
        creds = Credentials(
            token=token_data['access_token'],
            refresh_token=token_data.get('refresh_token'),
            token_uri=API_ENDPOINTS['gmail_token'],
            client_id=c_id,
            client_secret=c_secret,
            scopes=GMAIL_SCOPES,
        )
        creds.expiry = datetime.utcnow() + timedelta(seconds=token_data.get('expires_in', 3600))

        with open(GMAIL_TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
        print("Gmail Authorization Successful!\n")
    except Exception as e:
        print(f"Failed to fetch Gmail tokens: {e}")


def _pkce_pair():
    """Random (code_verifier, code_challenge) pair per RFC 7636. Azure's
    public-client (no client secret) flow requires PKCE in place of a
    secret — the verifier is only needed within this one auth_outlook()
    call, never persisted."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('ascii')
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode('ascii')).digest()
    ).rstrip(b'=').decode('ascii')
    return verifier, challenge


def auth_outlook():
    if EMAIL_PROVIDER != 'outlook': return

    if os.path.exists(OUTLOOK_TOKEN_PATH):
        return

    print("\n--- OUTLOOK CONFIGURATION REQUIRED ---")
    print("Register an app at https://portal.azure.com -> Azure Active Directory")
    print("-> App registrations -> New registration. Supported account types:")
    print("'Personal Microsoft accounts and work/school accounts'. Then add a")
    print("platform: 'Mobile and desktop applications', redirect URI")
    print(f"{OUTLOOK_REDIRECT_URI} — under API permissions add the Microsoft")
    print("Graph delegated permission 'Mail.Read'.\n")
    print("If this is a work/school (M365) account: your organisation's admin")
    print("policy can block this outright (Conditional Access, tenant-wide app")
    print("consent restrictions) regardless of anything done here — if the")
    print("browser step below fails with an admin-consent or blocked-by-policy")
    print("error, that's IT policy, not a bug in this script.\n")

    c_id = input("Enter Application (client) ID (or press Enter to disable): ").strip()
    if not c_id:
        print("Outlook is disabled. Unread count will stay at 0.\n")
        return

    verifier, challenge = _pkce_pair()

    auth_url = (
        f"{API_ENDPOINTS['outlook_auth']}?"
        f"client_id={c_id}&"
        f"response_type=code&"
        f"redirect_uri={urllib.parse.quote(OUTLOOK_REDIRECT_URI, safe='')}&"
        f"scope={urllib.parse.quote(OUTLOOK_SCOPES, safe='')}&"
        f"code_challenge={challenge}&"
        f"code_challenge_method=S256"
    )

    print("\n[!] To authorize, open this link in your browser:\n")
    print(f"--> {auth_url} <--\n")
    print(f"Click 'Accept'. You will be redirected to an empty/error page ({OUTLOOK_REDIRECT_URI}).")
    print("Look at the address bar. Copy the 'code' parameter.")

    code_input = input("Enter the 'code' from the URL (or paste the full URL): ").strip()

    if not code_input:
        print("Authorization cancelled. Unread count will stay at 0.\n")
        return

    if 'code=' in code_input:
        try:
            parsed = urllib.parse.urlparse(code_input)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get('code', [code_input])[0]
        except:
            code = code_input.split('code=')[1].split('&')[0]
    else:
        code = code_input

    print("Fetching Access Token...")
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': OUTLOOK_REDIRECT_URI,
        'client_id': c_id,
        'code_verifier': verifier,
        'scope': OUTLOOK_SCOPES,
    }

    try:
        resp = requests.post(API_ENDPOINTS['outlook_token'], data=data)
        resp.raise_for_status()
        token_data = resp.json()
        token_data['client_id'] = c_id
        token_data['expires_at'] = time.time() + token_data.get('expires_in', 3600)

        with open(OUTLOOK_TOKEN_PATH, 'w') as f:
            json.dump(token_data, f, indent=4)
        print("Outlook Authorization Successful!\n")
    except Exception as e:
        print(f"Failed to fetch Outlook tokens: {e}")


def fetch_outlook_unread_today():
    if not os.path.exists(OUTLOOK_TOKEN_PATH): return None
    with open(OUTLOOK_TOKEN_PATH, 'r') as f:
        token_data = json.load(f)

    c_id = token_data.get('client_id')

    if time.time() > token_data.get('expires_at', 0):
        # Public client (no client secret) refresh — same PKCE-registered
        # app, just client_id, no code_verifier needed for this grant type.
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': token_data.get('refresh_token'),
            'client_id': c_id,
            'scope': OUTLOOK_SCOPES,
        }
        new_token = net.get_json(API_ENDPOINTS['outlook_token'], data=data, method='POST')
        if new_token and 'access_token' in new_token:
            token_data.update(new_token)
            token_data['client_id'] = c_id
            token_data['expires_at'] = time.time() + new_token.get('expires_in', 3600)
            with open(OUTLOOK_TOKEN_PATH, 'w') as f:
                json.dump(token_data, f, indent=4)
        else:
            return None

    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    # receivedDateTime ge <local midnight, with offset> — Graph's
    # DateTimeOffset comparison is offset-aware, so no UTC conversion needed,
    # unlike Gmail's Unix-epoch approach to the same "since local midnight"
    # problem. Counting len(value) rather than trusting a returned count,
    # same reasoning as the Gmail widget: it's what's actually returned, not
    # an estimate.
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    params = {
        '$filter': f"isRead eq false and receivedDateTime ge {midnight.isoformat()}",
        '$select': 'id',
        '$top': 500,
    }

    try:
        resp = net.session.get(API_ENDPOINTS['outlook_messages'], headers=headers, params=params, timeout=8)
    except Exception as e:
        logging.error(f"Outlook fetch error: {e}")
        return None
    if resp.status_code == 401:
        logging.error("Outlook token rejected (401) - re-run auth_outlook() to re-authorize.")
        return None
    if resp.status_code != 200:
        logging.error(f"Outlook fetch failed: HTTP {resp.status_code} {resp.text[:200]}")
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    return len(payload.get('value', []))


def auth_roborock(email):
    global ENABLE_ROBOROCK
    if not ENABLE_ROBOROCK: return None

    if os.path.exists(ROBOROCK_TOKEN_FILE):
        try:
            with open(ROBOROCK_TOKEN_FILE, "rb") as f:
                return pickle.load(f)
        except:
            pass

    print("\n--- ROBOROCK AUTHORIZATION REQUIRED ---")

    async def _do_auth():
        web_api = RoborockApiClient(username=email)
        await web_api.request_code()
        code = input(f"Enter 6-digit Roborock auth code sent to {email} (or press Enter to disable): ").strip()
        if not code: return None
        user_data = await web_api.code_login(code)
        with open(ROBOROCK_TOKEN_FILE, "wb") as f: pickle.dump(user_data, f)
        print("Roborock Authorization Successful!\n")
        return user_data

    if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        user_data = asyncio.run(_do_auth())
        if not user_data:
            print("Roborock is disabled. Fallback widget (Ping) will be used.\n")
            ENABLE_ROBOROCK = False
        return user_data
    except Exception as e:
        print(f"Failed to auth Roborock: {e}")
        ENABLE_ROBOROCK = False
        return None


def roborock_update_thread(user_data, email):
    if not ENABLE_ROBOROCK or not user_data: return

    async def _loop():
        ref_area, last_date = 0.0, "-"
        if os.path.exists(ROBOROCK_STATS_FILE):
            try:
                with open(ROBOROCK_STATS_FILE, "r") as f:
                    stats = json.load(f)
                    ref_area, last_date = stats.get("ref_area", 0.0), stats.get("last_date", "-")
            except:
                pass

        user_params = UserParams(username=email, user_data=user_data)
        device_manager = await create_device_manager(user_params)

        short_states = {
            5: "Clean", 6: "Return", 8: "Charge", 10: "Pause",
            17: "Spot", 18: "Room", 22: "Empty", 23: "Wash",
            26: "ToWash", 29: "Map"
        }

        while True:
            try:
                devices = await device_manager.get_devices()
                if devices and devices[0].v1_properties:
                    device = devices[0]
                    status_trait = device.v1_properties.status
                    await status_trait.refresh()
                    current_area = (status_trait.clean_area / 1000000) if status_trait.clean_area else 0

                    is_cleaning = status_trait.state in [5, 6, 10, 17, 18, 22, 23, 26, 29]
                    status_str = short_states.get(status_trait.state, f"S:{status_trait.state}")

                    if not is_cleaning and current_area > 0 and current_area != ref_area:
                        ref_area = current_area
                        last_date = datetime.now().strftime("%d %b %H:%M")
                        with open(ROBOROCK_STATS_FILE, "w") as f: json.dump(
                            {"ref_area": ref_area, "last_date": last_date}, f)

                    pct = (current_area / ref_area) * 100 if is_cleaning and ref_area > 0 else 0.0

                    with data_store.lock:
                        data_store.roborock = {
                            'status': status_str, 'battery': status_trait.battery,
                            'is_cleaning': is_cleaning, 'current_area': current_area,
                            'ref_area': ref_area, 'pct': pct, 'last_date': last_date
                        }
            except Exception as e:
                logging.error(f"Roborock error: {e}")
            await asyncio.sleep(60)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_loop())


def _load_waveshare_api_config():
    """Reads {"base_url": ..., "api_key": ...} from WAVESHARE_API_CONF's
    gitignored CONFIG_FILE. Read fresh on every call (cheap — one small local
    file) rather than cached at import time, so editing the file takes effect
    on the next fetch without a restart. Missing file or malformed/incomplete
    contents both return None, which is what makes the two fetchers below
    degrade to a no-op instead of crashing."""
    if not os.path.exists(WAVESHARE_API_CONF['CONFIG_FILE']):
        return None
    try:
        with open(WAVESHARE_API_CONF['CONFIG_FILE'], 'r') as f:
            conf = json.load(f)
        if conf.get('base_url') and conf.get('api_key'):
            return conf
    except Exception:
        pass
    return None


def fetch_todos_data():
    """GET /api/tasks/top. The companion app's field names (text/dueTime)
    differ from data_store.todos' shape (title/due) that the Tasks widget's
    draw code expects — see CLAUDE.md's "Todo widget fetch" section — so this
    remaps rather than storing the response as-is. The API already returns
    only the top N tasks (per /api/widget-config's top_n, server-side), so no
    further slicing happens here."""
    conf = _load_waveshare_api_config()
    if not conf:
        return None
    url = f"{conf['base_url'].rstrip('/')}/api/tasks/top"
    data = net.get_json(url, headers={'X-Api-Key': conf['api_key']})
    if not isinstance(data, list):
        return None
    return [
        {
            'title': task.get('text', ''),
            'due': _format_due_time(task.get('dueTime')),
            'completed': bool(task.get('completed', False)),
        }
        for task in data if isinstance(task, dict)
    ]


def _format_due_time(due_time):
    """The API sends dueTime as 24h "HH:MM"; the Tasks widget's due-time
    column was sized for "H:MM AM/PM" (see CLAUDE.md), so reformat rather
    than pass the raw string through. Falls back to the raw value on
    anything that doesn't parse, rather than dropping it."""
    if not due_time:
        return None
    try:
        return datetime.strptime(due_time, '%H:%M').strftime('%I:%M %p').lstrip('0')
    except ValueError:
        return due_time


def fetch_widget_config_data():
    """GET /api/widget-config. Only email_provider/middle_widget are acted
    on (see CLAUDE.md roadmap) — top_n is informational only, since the
    companion app already trims /api/tasks/top to that count server-side."""
    conf = _load_waveshare_api_config()
    if not conf:
        return None
    url = f"{conf['base_url'].rstrip('/')}/api/widget-config"
    data = net.get_json(url, headers={'X-Api-Key': conf['api_key']})
    return data if isinstance(data, dict) else None


def update_data_thread():
    global global_printer, EMAIL_PROVIDER, MIDDLE_COLUMN_WIDGET

    if ENABLE_BAMBU:
        try:
            global_printer = bl.Printer(PRINTER_CONF['IP'], PRINTER_CONF['ACCESS_CODE'], PRINTER_CONF['SERIAL'])
        except Exception as e:
            logging.error(f"Bambu init error: {e}")
            global_printer = None

    is_connected = False

    while True:
        now = time.time()

        if now - data_store.last_update['weather'] > 600:
            weather_url = f"{API_ENDPOINTS['weather']}?latitude={LOCATION_LAT}&longitude={LOCATION_LON}&current=temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,weather_code,is_day,uv_index&hourly=temperature_2m,precipitation_probability,weather_code,cloud_cover&timezone=auto&forecast_days=2"
            aqi_url = f"{API_ENDPOINTS['aqi']}?latitude={LOCATION_LAT}&longitude={LOCATION_LON}&current=european_aqi&timezone=auto"
            w_data = net.get_json(weather_url)
            a_data = net.get_json(aqi_url)
            with data_store.lock:
                if w_data: data_store.weather = w_data
                if a_data and 'current' in a_data: data_store.aqi = a_data['current'].get('european_aqi', 0)
            data_store.last_update['weather'] = now

        # Slow-changing on purpose: a phrase to read over time, not something
        # that needs to update every render like the clock does.
        if now - data_store.last_update['affirmation'] > 900:
            aff_data = net.get_json(API_ENDPOINTS['affirmation'], timeout=8)
            text = aff_data.get('affirmation') if isinstance(aff_data, dict) else None
            if isinstance(text, str) and text.strip():
                with data_store.lock: data_store.affirmation = text.strip()
            data_store.last_update['affirmation'] = now

        if ENABLE_STRAVA:
            if now - data_store.last_update['strava'] > 900:
                s_data = fetch_strava_data()
                if s_data:
                    with data_store.lock: data_store.strava = s_data
                data_store.last_update['strava'] = now
        else:
            if now - data_store.last_update['sysload'] > 30:
                try:
                    with open('/proc/loadavg', 'r') as f:
                        cpu = float(f.read().split()[0]) * 10
                    with open('/proc/meminfo', 'r') as f:
                        lines = f.readlines()
                        free = int(lines[1].split()[1]) // 1024
                    with data_store.lock:
                        data_store.sysload['cpu'] = min(int(cpu), 100)
                        data_store.sysload['ram_free'] = free
                        data_store.sysload['history'].append(min(int(cpu), 100))
                except:
                    pass
                data_store.last_update['sysload'] = now

        if ENABLE_BAMBU:
            update_interval = 5 if is_connected else 15
            if now - data_store.last_update['printer'] > update_interval:
                is_alive = ping_printer(PRINTER_CONF['IP'])
                if is_alive:
                    try:
                        if not is_connected and global_printer:
                            global_printer.connect()
                            time.sleep(1)
                            is_connected = True
                        if global_printer:
                            status = global_printer.get_state()
                            if status and status != "UNKNOWN":
                                with data_store.lock:
                                    data_store.printer = {
                                        'status': status,
                                        'percentage': global_printer.get_percentage(),
                                        'remaining_time': global_printer.get_time(),
                                        'layers': f"{global_printer.current_layer_num()}/{global_printer.total_layer_num()}"
                                    }
                    except Exception as e:
                        is_connected = False
                        with data_store.lock:
                            data_store.printer['status'] = 'OFFLINE'
                        try:
                            if global_printer: global_printer.disconnect()
                        except:
                            pass
                else:
                    if is_connected:
                        is_connected = False
                        try:
                            global_printer.disconnect()
                        except:
                            pass
                    with data_store.lock:
                        data_store.printer['status'] = 'OFFLINE'
                data_store.last_update['printer'] = now
        else:
            if now - data_store.last_update['crypto'] > 600:
                btc_url = f"{API_ENDPOINTS['btc']}?vs_currency=usd&days=7"
                eth_url = f"{API_ENDPOINTS['eth']}?vs_currency=usd&days=7"
                btc_data = net.get_json(btc_url)
                eth_data = net.get_json(eth_url)
                with data_store.lock:
                    if btc_data:
                        prices = [p[1] for p in btc_data.get('prices', [])]
                        if prices:
                            data_store.crypto['btc'] = int(prices[-1])
                            data_store.crypto['btc_hist'] = prices[::len(prices) // 50][:50]
                    if eth_data:
                        prices = [p[1] for p in eth_data.get('prices', [])]
                        if prices:
                            data_store.crypto['eth'] = int(prices[-1])
                            data_store.crypto['eth_hist'] = prices[::len(prices) // 50][:50]
                data_store.last_update['crypto'] = now

        if not ENABLE_ROBOROCK and not ENABLE_ANTIGRAVITY:
            if now - data_store.last_update['ping'] > 20:
                try:
                    out = subprocess.check_output(['ping', '-c', '1', '-W', '1', '8.8.8.8']).decode('utf-8')
                    ms = float(out.split('time=')[1].split(' ms')[0])
                except:
                    ms = 0
                with data_store.lock:
                    data_store.ping['current'] = int(ms)
                    data_store.ping['history'].append(int(ms))
                data_store.last_update['ping'] = now

        if EMAIL_PROVIDER == 'gmail' and now - data_store.last_update['email'] > 300:
            try:
                creds = None
                if os.path.exists(GMAIL_TOKEN_PATH):
                    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                        with open(GMAIL_TOKEN_PATH, 'w') as t: t.write(creds.to_json())
                if creds and creds.valid:
                    service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
                    # Total inbox unread isn't very actionable (it accumulates
                    # forever); count only unread received since local
                    # midnight instead. after: takes a Unix timestamp, so
                    # this is exact regardless of the Pi's timezone, unlike
                    # Gmail's after:YYYY/MM/DD form which is UTC-based.
                    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    query = f"is:unread after:{int(midnight.timestamp())}"
                    result = service.users().messages().list(userId='me', q=query, maxResults=500).execute()
                    with data_store.lock: data_store.email_unread_today = len(result.get('messages', []))
            except:
                pass
            data_store.last_update['email'] = now

        elif EMAIL_PROVIDER == 'outlook' and now - data_store.last_update['email'] > 300:
            count = fetch_outlook_unread_today()
            if count is not None:
                with data_store.lock: data_store.email_unread_today = count
            data_store.last_update['email'] = now

        # Claude Data Fetching (Run external script every 10 min)
        if ENABLE_CLAUDE and now - data_store.last_update['claude'] > 600:
            try:
                subprocess.run([sys.executable, os.path.join(BASE_DIR, 'claude.py')], capture_output=True, timeout=30)
                usage_path = os.path.join(BASE_DIR, 'usage.json')
                if os.path.exists(usage_path):
                    with open(usage_path, 'r') as f:
                        usage_data = json.load(f)
                    with data_store.lock:
                        data_store.claude = usage_data
                        if "error" in usage_data and "five_hour" not in usage_data:
                            data_store.claude['error'] = True
                        else:
                            data_store.claude['error'] = False
                else:
                    with data_store.lock:
                        data_store.claude['error'] = True
            except Exception as e:
                logging.error(f"Claude update error: {e}")
                with data_store.lock:
                    data_store.claude['error'] = True
            data_store.last_update['claude'] = now

        # Codex Data Fetching (Run external script every 10 min)
        if ENABLE_CODEX and now - data_store.last_update['codex'] > 600:
            try:
                subprocess.run([sys.executable, os.path.join(BASE_DIR, 'codex.py'), '--once'],
                               capture_output=True, timeout=30)
                usage_path = os.path.join(BASE_DIR, 'codex_usage.json')
                if os.path.exists(usage_path):
                    with open(usage_path, 'r') as f:
                        usage_data = json.load(f)
                    with data_store.lock:
                        data_store.codex = usage_data
                        # codex.py signals failure with utilization = -1.0 (not an
                        # "error" key like claude.py), so check both.
                        sd = usage_data.get('seven_day', {})
                        if usage_data.get('error') or 'seven_day' not in usage_data \
                                or sd.get('utilization', -1) < 0:
                            data_store.codex['error'] = True
                        else:
                            data_store.codex['error'] = False
                else:
                    with data_store.lock:
                        data_store.codex['error'] = True
            except Exception as e:
                logging.error(f"Codex update error: {e}")
                with data_store.lock:
                    data_store.codex['error'] = True
            data_store.last_update['codex'] = now

        if ENABLE_ANTIGRAVITY and now - data_store.last_update['antigravity'] > 60:
            try:
                subprocess.run([sys.executable, os.path.join(BASE_DIR, 'antigravity.py')], capture_output=True, timeout=30)
                limits_path = os.path.join(BASE_DIR, 'limits.json')
                if os.path.exists(limits_path):
                    with open(limits_path, 'r', encoding='utf-8') as f:
                        limits_data = json.load(f)
                    with data_store.lock:
                        data_store.antigravity = limits_data
                        if "error" in limits_data:
                            data_store.antigravity['error'] = True
                        else:
                            data_store.antigravity['error'] = False
                else:
                    with data_store.lock:
                        data_store.antigravity['error'] = True
            except Exception as e:
                logging.error(f"Antigravity update error: {e}")
                with data_store.lock:
                    data_store.antigravity['error'] = True
            data_store.last_update['antigravity'] = now

        if ENABLE_SPOTIFY and now - data_store.last_update['spotify'] > 20:
            s_data = fetch_spotify_data()
            if s_data is not None:
                with data_store.lock:
                    data_store.spotify = s_data
            data_store.last_update['spotify'] = now

        # Companion app: todos want to feel reasonably fresh (120s), widget
        # config is more "check every so often" (600s) — see CLAUDE.md's
        # "one endpoint per resource" section for why these poll
        # independently rather than sharing one combined fetch/cadence.
        if now - data_store.last_update['todos'] > 120:
            todos = fetch_todos_data()
            if todos is not None:
                with data_store.lock:
                    data_store.todos = todos
            data_store.last_update['todos'] = now

        if now - data_store.last_update['widget_config'] > 600:
            cfg = fetch_widget_config_data()
            if cfg is not None:
                email_provider = cfg.get('email_provider')
                if email_provider in ('gmail', 'outlook', None):
                    EMAIL_PROVIDER = email_provider
                middle_widget = cfg.get('middle_widget')
                if middle_widget in ('spotify', 'weather'):
                    MIDDLE_COLUMN_WIDGET = middle_widget
            data_store.last_update['widget_config'] = now

        gc.collect()
        time.sleep(1)


# --- GRAPHICS FUNCTIONS ---
def needs_intl_font(text):
    """True if text has any CJK/Kana/Cyrillic character — Aldrich (this
    dashboard's default font) has none of these, but fnt/CJK-Cyrillic-
    Spotify.ttf ('intl_24'/'intl_28') covers all three plus plain ASCII, so
    a whole field can render in one font call once this is True. Deliberately
    narrower than "any non-ASCII": an accented-Latin name (e.g. "Beyoncé")
    isn't covered by that font either, so there's no point rerouting it away
    from Aldrich, which renders the rest of the name fine."""
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3040 <= cp <= 0x30FF or 0x0400 <= cp <= 0x04FF:
            return True
    return False


def text_width(draw, text, font):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except AttributeError:
        return draw.textsize(text, font=font)[0]


def draw_mixed_text(draw, x, y_center, segments, fill=0):
    """Draw left-to-right (text, font) segments that may mix fonts with
    different vertical metrics (e.g. a CJK glyph next to Latin text),
    vertically centring each segment's own bounding box on `y_center`
    rather than aligning by baseline or top."""
    cur_x = x
    for text, font in segments:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((cur_x, y_center - (bbox[3] - bbox[1]) / 2 - bbox[1]), text, font=font, fill=fill)
        cur_x += bbox[2] - bbox[0]
    return cur_x - x


def _break_oversized_word(draw, word, font, max_width):
    """Hard-split a single word that's wider than max_width on its own into
    character chunks that each fit — a normal word never hits this, but
    third-party API text is otherwise unbounded."""
    if text_width(draw, word, font) <= max_width:
        return [word]
    chunks = []
    cur = ""
    for ch in word:
        candidate = cur + ch
        if cur and text_width(draw, candidate, font) > max_width:
            chunks.append(cur)
            cur = ch
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def wrap_text(draw, text, font, max_width):
    """Greedily word-wrap `text` into lines that each fit within max_width
    pixels (measured, not char-counted — fonts aren't monospace)."""
    words = []
    for word in text.split():
        words.extend(_break_oversized_word(draw, word, font, max_width))
    lines = []
    cur = ""
    for word in words:
        candidate = f"{cur} {word}".strip()
        if cur and text_width(draw, candidate, font) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines


def wrap_lines_limited(draw, text, font, max_width, max_lines):
    """wrap_text(), but capped at max_lines — text that doesn't fit even
    then has its last line trimmed word-by-word and ellipsised, so an
    API response of unpredictable length can never overflow the widget."""
    lines = wrap_text(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]
    while last and text_width(draw, f"{last} …", font) > max_width:
        last = " ".join(last.split()[:-1])
    lines[-1] = f"{last} …" if last else "…"
    return lines


def draw_icon(draw, x, y, name, size=(40, 40), is_white=False):
    icon = get_cached_icon(name, size, is_white)
    if icon:
        draw.bitmap((x, y), icon, fill=255 if is_white else 0)
    else:
        draw.rectangle((x, y, x + size[0], y + size[1]), outline=255 if is_white else 0)


# Used to space canopy leaves via a sunflower-seed (Fibonacci) packing —
# each leaf's angle advances by this irrational fraction of a turn, which
# is what keeps the packing gap-free and non-repeating at any leaf count
# rather than falling into the spoke/ring patterns a fixed angle step would.
GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))


def draw_growth_plant(draw, cx, ground_y, growth_fraction):
    """Procedural potted plant, from a two-leaf sprout to a full lush
    canopy, driven by a single 0.0-1.0 fraction — pot is fixed, but stem
    height/width and canopy radius/leaf size/leaf count all scale
    continuously from it, rather than switching between a handful of
    discrete stage images. That also means (deliberately) every half-litre
    step nudges the canopy's leaf count and/or spacing by a little, since
    80 logged steps from seed to full growth needs far more
    visibly-distinct steps than a few discrete growth stages could give
    it. Needs no extra art assets, matching how the rest of the dashboard
    draws things (sparklines, checkboxes) instead of importing icons for
    everything."""
    t = max(0.0, min(1.0, growth_fraction))

    # Pot: solid trapezoid body (matches the bold/filled style of the rest
    # of the icon set), with a white rim ring cut into the top edge and a
    # black soil disc inside it, so it reads as an open pot rather than a
    # solid black block.
    pot_top_w, pot_bottom_w, pot_h = 74, 54, 46
    pot_top_y = ground_y - pot_h
    draw.polygon([
        (cx - pot_top_w / 2, pot_top_y), (cx + pot_top_w / 2, pot_top_y),
        (cx + pot_bottom_w / 2, ground_y), (cx - pot_bottom_w / 2, ground_y),
    ], fill=0)
    draw.ellipse((cx - pot_top_w / 2, pot_top_y - 8, cx + pot_top_w / 2, pot_top_y + 8), fill=255)
    draw.ellipse((cx - pot_top_w / 2 + 7, pot_top_y - 4, cx + pot_top_w / 2 - 7, pot_top_y + 4), fill=0)

    # Stem: short and thin at t=0 (a sprout just clearing the soil) up to
    # tall and thick at t=1.
    stem_h = 14 + t * (140 - 14)
    stem_w = 3 + t * (10 - 3)
    stem_top_y = pot_top_y - stem_h
    draw.rectangle((cx - stem_w / 2, stem_top_y, cx + stem_w / 2, pot_top_y), fill=0)

    # Canopy: leaves packed via the golden-angle spiral above, both radius
    # and count growing with t — at t=0 this is just the sprout's two seed
    # leaves; by t=1 it's a dense, lush cluster rather than a smooth blob.
    canopy_r = 9 + t * (82 - 9)
    leaf_r_base = 6 + t * (15 - 6)
    n_leaves = int(round(2 + t * (46 - 2)))
    canopy_cy = stem_top_y - canopy_r * 0.35

    for i in range(n_leaves):
        frac = (i + 0.5) / n_leaves
        r = canopy_r * math.sqrt(frac)
        angle = i * GOLDEN_ANGLE
        lx = cx + r * math.cos(angle)
        ly = canopy_cy + r * math.sin(angle) * 0.8
        leaf_r = leaf_r_base * (0.55 + 0.45 * (1 - frac))
        draw.ellipse((lx - leaf_r, ly - leaf_r, lx + leaf_r, ly + leaf_r), fill=0)


def draw_sparkline(draw, x, y, data, max_items=50, width=400, height=60, color=0, style="bar"):
    if not data: return
    max_val = max(data) if max(data) > 0 else 1
    step = width / max(max_items - 1, 1)

    if style == "line":
        points = []
        for i, val in enumerate(data):
            px = x + i * step
            py = y + height - (val / max_val) * height
            points.append((px, py))
        if len(points) > 1: draw.line(points, fill=color, width=2)
    elif style == "bar":
        bar_w = max(int(step) - 1, 1)
        for i, val in enumerate(data):
            bh = int((val / max_val) * height)
            bx = x + i * step
            by = y + height - bh
            draw.rectangle((bx, by, bx + bar_w, y + height), fill=color)


def get_weather_icon(code, is_day=1):
    if code == 0:
        return "icon_sun" if is_day else "icon_moon"
    elif code in [1, 2]:
        return "icon_partly-cloudy-day"
    elif code == 3:
        return "icon_clouds"
    elif code in [45, 48]:
        return "icon_wind"
    elif code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
        return "icon_rain"
    elif code in [71, 73, 75, 85, 86]:
        return "icon_snow"
    elif code in [95, 96, 99]:
        return "icon_lightning"
    return "icon_sun"


def render_screen(epd, fonts):
    Himage = Image.new('1', (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(Himage)

    if not data_store.lock.acquire(timeout=2.0): return Himage
    try:
        weather = data_store.weather.copy()
        aqi = data_store.aqi
        strava = data_store.strava.copy()
        printer = data_store.printer.copy()
        rob = data_store.roborock.copy()
        email_unread_today = data_store.email_unread_today
        spotify = data_store.spotify.copy()
        claude = data_store.claude.copy()
        antigravity = data_store.antigravity.copy()
        codex = data_store.codex.copy()
        sysload = data_store.sysload.copy()
        crypto = data_store.crypto.copy()
        ping = data_store.ping.copy()
        affirmation = data_store.affirmation
        todos = data_store.todos.copy()
        water = data_store.water.copy()
    finally:
        data_store.lock.release()

    col_w = epd.width // 3

    # --- COLUMN 1 (Widgets, or Tasks) ---
    col1_x = 20

    if ENABLE_TODO:
        content_w = col_w - 20 - col1_x

        draw_icon(draw, col1_x, 15, "icon_task", (32, 32))
        draw.text((col1_x + 45, 15), "TASKS OF THE DAY", font=fonts['28'], fill=0)
        draw.line((col1_x, 60, col1_x + content_w, 60), fill=0, width=2)

        if not todos:
            # Placeholder — no tasks today, or the fetch hasn't populated
            # data_store.todos yet (fresh boot, or the companion app
            # unreachable). Same "icon plus centred message" shape as
            # Spotify's "Nothing Playing" and the weather placeholder, so an
            # empty/failed fetch degrades gracefully instead of leaving the
            # column as a grid of empty checkboxes with no explanation.
            icon_size = 100
            icon_x = col1_x + max(0, (content_w - icon_size) / 2)
            icon_y = 220
            draw_icon(draw, int(icon_x), icon_y, "icon_task", (icon_size, icon_size))
            msg = "No Tasks Today"
            mw = text_width(draw, msg, fonts['28'])
            draw.text((col1_x + max(0, (content_w - mw) / 2), icon_y + icon_size + 30), msg, font=fonts['28'], fill=0)

        else:
            row_top, row_bottom = 75, 460
            row_h = (row_bottom - row_top) / TODO_MAX_TASKS
            checkbox_size = 16
            # Wide enough for "HH:MM AM/PM" (measured ~94px at fonts['20']) with
            # a small margin — not just a round number, since that string is
            # wider than it looks.
            due_col_w = 100
            title_x = col1_x + checkbox_size + 8
            title_max_w = content_w - checkbox_size - 8 - due_col_w - 6

            for i in range(TODO_MAX_TASKS):
                row_y = row_top + i * row_h
                cb_y = row_y + 4
                completed = i < len(todos) and todos[i].get('completed', False)

                if completed:
                    # Filled box + a light checkmark cut into it, rather than an
                    # outline — reads as "done" at a glance, matching the
                    # strikethrough on the title below.
                    draw.rectangle((col1_x, cb_y, col1_x + checkbox_size, cb_y + checkbox_size), fill=0)
                    draw.line((col1_x + 3, cb_y + 8, col1_x + 6, cb_y + 12), fill=255, width=2)
                    draw.line((col1_x + 6, cb_y + 12, col1_x + 13, cb_y + 3), fill=255, width=2)
                else:
                    draw.rectangle((col1_x, cb_y, col1_x + checkbox_size, cb_y + checkbox_size), outline=0, width=2)

                if i < len(todos):
                    task = todos[i]
                    # Task titles will eventually be arbitrary text from the
                    # companion app's API — same "unpredictable external text"
                    # category as artist/track names and the affirmation line,
                    # so truncate in pixels rather than assume they fit.
                    title = wrap_lines_limited(draw, task.get('title', ''), fonts['24'], title_max_w, max_lines=1)
                    title = title[0] if title else ''
                    draw.text((title_x, row_y), title, font=fonts['24'], fill=0)
                    if completed and title:
                        tw = text_width(draw, title, fonts['24'])
                        bbox = draw.textbbox((title_x, row_y), title, font=fonts['24'])
                        strike_y = (bbox[1] + bbox[3]) / 2
                        draw.line((title_x, strike_y, title_x + tw, strike_y), fill=0, width=2)

                    due = task.get('due')
                    if due:
                        due_line = wrap_lines_limited(draw, due, fonts['20'], due_col_w, max_lines=1)
                        due_text = due_line[0] if due_line else ''
                        dw = text_width(draw, due_text, fonts['20'])
                        draw.text((col1_x + content_w - dw, row_y + 3), due_text, font=fonts['20'], fill=0)

                if i < TODO_MAX_TASKS - 1:
                    sep_y = row_y + row_h - 6
                    draw.line((col1_x, sep_y, col1_x + content_w, sep_y), fill=0, width=1)

    else:
        # Widget 1: Strava or SysLoad
        y1 = 20
        if ENABLE_STRAVA:
            draw_icon(draw, col1_x, y1, "icon_strava", (60, 60))
            draw.text((col1_x + 70, y1), "STRAVA STATS", font=fonts['28'], fill=0)
    
            now_y = datetime.now().year
            draw.text((col1_x + 70, y1 + 35),
                      f"{now_y}: {strava.get('distance_curr', 0)} km | {now_y - 1}: {strava.get('distance_prev', 0)} km",
                      font=fonts['20'], fill=0)
            draw.text((col1_x + 70, y1 + 60),
                      f"Total: {strava.get('total_distance', 0)} km | {strava.get('rides', 0)} acts", font=fonts['20'],
                      fill=0)
    
            draw_icon(draw, col1_x + 70, y1 + 85, "icon_bike", (30, 30))
            draw.text((col1_x + 105, y1 + 90), f"{strava.get('bike_total', 0)} km", font=fonts['20'], fill=0)
    
            draw_icon(draw, col1_x + 220, y1 + 85, "icon_hike", (30, 30))
            draw.text((col1_x + 255, y1 + 90), f"{strava.get('hike_total', 0)} km", font=fonts['20'], fill=0)
    
        else:
            draw_icon(draw, col1_x, y1, "icon_cpu", (50, 50))
            draw.text((col1_x + 60, y1), f"SYSTEM LOAD: {sysload['cpu']}%", font=fonts['28'], fill=0)
            draw.text((col1_x + 60, y1 + 35), f"RAM Free: {sysload['ram_free']} MB", font=fonts['20'], fill=0)
            draw_sparkline(draw, col1_x + 60, y1 + 60, list(sysload['history']), max_items=30, width=350, height=40,
                           style="bar")
    
        draw.line((col1_x, 150, col_w - 20, 150), fill=0, width=2)
    
        # Widget 2: Bambu or Crypto
        y2 = 170
        if ENABLE_BAMBU:
            p_status = str(printer.get('status', 'OFFLINE')).upper()
            draw_icon(draw, col1_x, y2, "icon_3d", (60, 60))
            draw.text((col1_x + 70, y2), f"PRINTER: {p_status}", font=fonts['28'], fill=0)
            if p_status not in ["OFFLINE", "UNKNOWN", "FINISH"]:
                percent = printer.get('percentage', 0)
                draw.rectangle((col1_x + 70, y2 + 40, col1_x + 400, y2 + 60), outline=0)
                draw.rectangle((col1_x + 70, y2 + 40, col1_x + 70 + int(330 * (percent / 100)), y2 + 60), fill=0)
                draw.text((col1_x + 70, y2 + 70),
                          f"{percent}% | Rem: {printer.get('remaining_time', '0')}m | {printer.get('layers', '0/0')} L",
                          font=fonts['20'], fill=0)
        else:
            draw_icon(draw, col1_x, y2, "icon_btc", (50, 50))
            draw.text((col1_x + 60, y2), f"BTC: ${crypto['btc']}", font=fonts['28'], fill=0)
            draw_sparkline(draw, col1_x + 60, y2 + 35, crypto['btc_hist'], max_items=50, width=350, height=35, style="bar")
    
            draw_icon(draw, col1_x, y2 + 80, "icon_eth", (50, 50))
            draw.text((col1_x + 60, y2 + 80), f"ETH: ${crypto['eth']}", font=fonts['28'], fill=0)
            draw_sparkline(draw, col1_x + 60, y2 + 115, crypto['eth_hist'], max_items=50, width=350, height=35, style="bar")
    
        draw.line((col1_x, 320, col_w - 20, 320), fill=0, width=2)
    
        # Widget 3: Roborock or Ping
        y3 = 340
        if ENABLE_ROBOROCK:
            draw_icon(draw, col1_x, y3, "icon_roborock", (50, 50))
            draw.text((col1_x + 60, y3), f"Bat: {rob['battery']}% | {rob['status']}", font=fonts['28'], fill=0)
            if rob['is_cleaning']:
                draw.text((col1_x + 60, y3 + 35), f"Clean: {rob['current_area']:.1f} m2 ({rob['pct']:.0f}%)",
                          font=fonts['24'], fill=0)
                clamped_pct = min(rob['pct'], 100)
                draw.rectangle((col1_x + 60, y3 + 70, col1_x + 390, y3 + 90), outline=0)
                draw.rectangle((col1_x + 60, y3 + 70, col1_x + 60 + int(330 * (clamped_pct / 100)), y3 + 90), fill=0)
            else:
                draw.text((col1_x + 60, y3 + 35), f"Last: {rob['last_date']} | {rob['ref_area']:.1f} m2", font=fonts['24'],
                          fill=0)
        elif ENABLE_ANTIGRAVITY:
            draw_icon(draw, col1_x, y3, "icon_cpu", (50, 50))
            draw.text((col1_x + 60, y3), "ANTIGRAVITY USAGE", font=fonts['28'], fill=0)
            
            if antigravity.get('error'):
                draw.text((col1_x + 60, y3 + 35), "Error loading data", font=fonts['20'], fill=0)
            else:
                models = antigravity.get('models', [])
                opus = next((m for m in models if m.get('modelId') == 'claude-opus-4-6-thinking'), None)
                gemini = next((m for m in models if m.get('modelId') == 'gemini-3-pro-high'), None)
                
                y_off = y3 + 35
                for m_data in (opus, gemini):
                    if m_data:
                        label = "Opus 4.6" if m_data.get('modelId') == 'claude-opus-4-6-thinking' else "Gemini 3Pro"
                        pct = m_data.get('usedPercentage', 0)
                        rem_time = time_until(m_data.get('resetDate'))
                        
                        draw.text((col1_x + 60, y_off), f"{label} {pct}% | In {rem_time}", font=fonts['20'], fill=0)
                        
                        bx, bw, bh = col1_x + 60, 330, 15
                        draw.rectangle((bx, y_off + 25, bx + bw, y_off + 25 + bh), outline=0, width=2)
                        fill_w = int((bw - 4) * min(pct / 100.0, 1.0))
                        if fill_w > 0: draw.rectangle((bx + 2, y_off + 27, bx + 2 + fill_w, y_off + 25 + bh - 2), fill=0)
                        
                        y_off += 50
        elif ENABLE_CODEX:
            draw_icon(draw, col1_x, y3, "icon_cpu", (50, 50))
            draw.text((col1_x + 60, y3), "CODEX AI USAGE", font=fonts['28'], fill=0)
    
            if codex.get('error'):
                draw.text((col1_x + 60, y3 + 40), "Codex Usage Error", font=fonts['20'], fill=0)
            else:
                # 7-Day limit only: OpenAI has disabled the 5-hour window for now.
                pct_7d = codex.get('seven_day', {}).get('utilization', 0)
                resets_7d = codex.get('seven_day', {}).get('resets_at')
                rem_7d = time_until(resets_7d)
    
                draw.text((col1_x + 60, y3 + 40), f"7-Day Limit: {round(pct_7d)}% (In {rem_7d})",
                          font=fonts['20'], fill=0)
                bx, bw, bh = col1_x + 60, 330, 15
                draw.rectangle((bx, y3 + 70, bx + bw, y3 + 70 + bh), outline=0, width=2)
                fill_w = int((bw - 4) * min(pct_7d / 100.0, 1.0))
                if fill_w > 0:
                    draw.rectangle((bx + 2, y3 + 72, bx + 2 + fill_w, y3 + 70 + bh - 2), fill=0)
        else:
            draw_icon(draw, col1_x, y3, "icon_wifi", (50, 50))
            draw.text((col1_x + 60, y3), f"Internet Quality: {ping['current']} ms", font=fonts['28'], fill=0)
            draw_sparkline(draw, col1_x, y3 + 60, list(ping['history']), max_items=50, width=400, height=40, style="bar")

    draw.line((col_w, 10, col_w, 470), fill=0, width=2)

    # --- COLUMN 2 (Weather or Spotify) ---
    col2_x = col_w + 20

    water_last_logged_at = water.get('last_logged_at')
    show_water = (
        ENABLE_WATER and water_last_logged_at is not None
        and (time.time() - water_last_logged_at) < WATER_SHOW_SECONDS
    )

    if show_water:
        # Hydration/growth widget — temporarily overlays whichever of
        # Spotify/Weather is active in this slot for WATER_SHOW_SECONDS
        # right after a fresh water log, then reverts on its own once
        # show_water goes false again on a later redraw (see
        # WATER_SHOW_SECONDS above for why no dedicated timer is needed).
        # Takes priority over both, regardless of MIDDLE_COLUMN_WIDGET.
        draw_icon(draw, col2_x, 20, "icon_droplet", (40, 40))
        draw.text((col2_x + 50, 28), "STAY HYDRATED", font=fonts['28'], fill=0)
        draw.line((col2_x, 85, col2_x + col_w - 40, 85), fill=0, width=2)

        # Lifetime badge — how many times she's grown a plant to full
        # (i.e. hit WATER_GROWTH_TARGET_LITRES) across all time, not
        # just the current one. A small mini-plant icon rather than the
        # same droplet as the header, so it reads as a distinct
        # "trophy count" at a glance instead of a second copy of the
        # header.
        badge_size = 26
        draw_icon(draw, col2_x, 94, "icon_plant_grown", (badge_size, badge_size))
        lifetime_count = water.get('plants_grown_lifetime', 0)
        draw.text((col2_x + badge_size + 8, 96), f"x {lifetime_count}", font=fonts['24'], fill=0)

        content_w = col_w - 40
        cx = col2_x + content_w / 2
        ground_y = 415
        progress_litres = water.get('progress_litres', 0.0)
        growth_fraction = progress_litres / WATER_GROWTH_TARGET_LITRES
        draw_growth_plant(draw, cx, ground_y, growth_fraction)

        # No calendar window to anchor the label to any more (growth
        # just resets on completion, whenever that happens), so show
        # progress against the fixed target instead of a bare total.
        total_text = f"{progress_litres:.1f} / {WATER_GROWTH_TARGET_LITRES:.0f} L grown"
        tw = text_width(draw, total_text, fonts['28'])
        draw.text((col2_x + max(0, (content_w - tw) / 2), ground_y + 15), total_text, font=fonts['28'], fill=0)

        last_amount = water.get('last_amount_litres')
        sub_text = f"+{last_amount:.1f} L just logged!" if last_amount else "New log recorded!"
        sw = text_width(draw, sub_text, fonts['20'])
        draw.text((col2_x + max(0, (content_w - sw) / 2), ground_y + 50), sub_text, font=fonts['20'], fill=0)

    elif MIDDLE_COLUMN_WIDGET == "weather" and 'current' in weather:
        cur = weather['current']
        temp = cur.get('temperature_2m', 0)
        hum = cur.get('relative_humidity_2m', 0)
        pres = cur.get('surface_pressure', 0)
        w_code = cur.get('weather_code', 0)
        wind_dir = cur.get('wind_direction_10m', 0)
        wind_spd = cur.get('wind_speed_10m', 0)
        is_day = cur.get('is_day', 1)
        uv_index = cur.get('uv_index', 0.0)

        temp_rounded = math.floor(temp + 0.5)

        draw_icon(draw, col2_x, 20, get_weather_icon(w_code, is_day), (90, 90))
        draw.text((col2_x + 100, 10), f"{temp_rounded}°C", font=fonts['80'], fill=0)

        uv_x, uv_y = col2_x + 320, 25
        uv_rounded = math.floor(uv_index + 0.5)
        draw.text((uv_x, uv_y), "UV", font=fonts['28'], fill=0)
        uv_val_str = str(uv_rounded)
        try:
            bbox = draw.textbbox((0, 0), uv_val_str, font=fonts['60'])
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(uv_val_str, font=fonts['60'])

        uv_val_x, uv_val_y = uv_x + 45, 5
        if uv_rounded >= 6:
            pad = 5
            draw.rectangle((uv_val_x - pad, uv_val_y - pad + 10, uv_val_x + tw + pad, uv_val_y + th + pad), fill=0)
            draw.text((uv_val_x, uv_val_y), uv_val_str, font=fonts['60'], fill=255)
        else:
            draw.text((uv_val_x, uv_val_y), uv_val_str, font=fonts['60'], fill=0)

        draw.text((col2_x + 100, 95), f"Humidity: {hum}%", font=fonts['20'], fill=0)
        draw.text((col2_x + 100, 120), f"Press: {pres} hPa", font=fonts['20'], fill=0)

        draw.line((col2_x, 140, col2_x + col_w - 40, 140), fill=0, width=2)

        y_c2 = 160
        draw_icon(draw, col2_x + 5, y_c2, "icon_wind", (30, 30))

        cx, cy, r = col2_x + 80, y_c2 + 80, 60
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=0, width=2)

        for angle in range(0, 360, 45):
            rad_tick = math.radians(angle)
            inner_r = r - 8 if angle % 90 == 0 else r - 4
            tx1, ty1 = cx + inner_r * math.cos(rad_tick), cy + inner_r * math.sin(rad_tick)
            tx2, ty2 = cx + r * math.cos(rad_tick), cy + r * math.sin(rad_tick)
            draw.line((tx1, ty1, tx2, ty2), fill=0, width=2)

        draw.text((cx - 8, cy - r - 22), "N", font=fonts['20'], fill=0)
        draw.text((cx - 8, cy + r + 4), "S", font=fonts['20'], fill=0)
        draw.text((cx + r + 6, cy - 10), "E", font=fonts['20'], fill=0)
        draw.text((cx - r - 24, cy - 10), "W", font=fonts['20'], fill=0)

        rad_arrow = math.radians(wind_dir - 90)
        tip_x = cx + (r - 12) * math.cos(rad_arrow)
        tip_y = cy + (r - 12) * math.sin(rad_arrow)
        base_angle = math.radians(150)
        left_x = cx + 20 * math.cos(rad_arrow + base_angle)
        left_y = cy + 20 * math.sin(rad_arrow + base_angle)
        right_x = cx + 20 * math.cos(rad_arrow - base_angle)
        right_y = cy + 20 * math.sin(rad_arrow - base_angle)
        draw.polygon([(tip_x, tip_y), (left_x, left_y), (right_x, right_y)], fill=0)
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=0)

        spd_text = f"{wind_spd} km/h"
        try:
            bbox = draw.textbbox((0, 0), spd_text, font=fonts['20'])
            tw = bbox[2] - bbox[0]
        except AttributeError:
            tw = draw.textsize(spd_text, font=fonts['20'])[0]

        draw.text((cx - tw / 2, cy + 25), spd_text, font=fonts['20'], fill=0)

        aqi_x = col2_x + 180
        draw.text((aqi_x, y_c2 + 10), "AIR QUALITY", font=fonts['20'], fill=0)
        draw.text((aqi_x, y_c2 + 55), "AQI:", font=fonts['28'], fill=0)

        aqi_str = str(aqi)
        try:
            bbox = draw.textbbox((0, 0), aqi_str, font=fonts['80'])
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(aqi_str, font=fonts['80'])

        val_x, val_y = aqi_x + 80, y_c2 + 66

        if aqi >= 50:
            pad = 20
            draw.rectangle((val_x - pad, val_y - pad + 15, val_x + tw + pad, val_y + th + pad - 5), fill=0)
            draw.text((val_x, val_y), aqi_str, font=fonts['80'], fill=255)
        else:
            draw.text((val_x, val_y), aqi_str, font=fonts['80'], fill=0)

        draw.line((col2_x, 320, col2_x + col_w - 40, 320), fill=0, width=2)

        hourly = weather.get('hourly', {})
        times = hourly.get('time', [])
        temps = hourly.get('temperature_2m', [])
        codes = hourly.get('weather_code', [])

        cur_iso = datetime.now().strftime("%Y-%m-%dT%H:00")
        try:
            start_idx = times.index(cur_iso) + 1
        except:
            start_idx = 0

        for i in range(4):
            idx = start_idx + i
            if idx < len(times):
                off_x = col2_x + (i * 105)
                draw.text((off_x + 10, 340), f"{times[idx].split('T')[1][:5]}", font=fonts['24'], fill=0)
                draw_icon(draw, off_x + 15, 375, get_weather_icon(codes[idx], 1), (60, 60))
                f_temp = math.floor(temps[idx] + 0.5)
                draw.text((off_x + 15, 440), f"{f_temp}°C", font=fonts['24'], fill=0)

    elif MIDDLE_COLUMN_WIDGET == "weather":
        # Weather placeholder — the fetch failed or hasn't populated yet
        # (e.g. fresh boot, or open-meteo unreachable). Same "icon plus
        # centred message" shape as Spotify's "Nothing Playing" fallback
        # below, so a dead API degrades to a placeholder instead of
        # leaving the column blank.
        draw_icon(draw, col2_x, 20, "icon_clouds", (40, 40))
        draw.text((col2_x + 50, 28), "WEATHER", font=fonts['28'], fill=0)
        draw.line((col2_x, 85, col2_x + col_w - 40, 85), fill=0, width=2)

        content_w = col_w - 40
        icon_size = 120
        icon_x = col2_x + max(0, (content_w - icon_size) / 2)
        icon_y = 150
        draw_icon(draw, int(icon_x), icon_y, "icon_clouds", (icon_size, icon_size))
        msg = "Weather Unavailable"
        mw = text_width(draw, msg, fonts['28'])
        draw.text((col2_x + max(0, (content_w - mw) / 2), icon_y + icon_size + 30), msg, font=fonts['28'], fill=0)

    else:
        # Spotify now-playing (default middle-column widget)
        draw_icon(draw, col2_x, 20, "icon_spotify", (40, 40))
        draw.text((col2_x + 50, 28), "SPOTIFY", font=fonts['28'], fill=0)
        draw.line((col2_x, 85, col2_x + col_w - 40, 85), fill=0, width=2)

        content_w = col_w - 40
        art_size = SPOTIFY_ART_SIZE
        art_x = col2_x + max(0, (content_w - art_size) / 2)
        art_y = 105

        if spotify['status'] == 'PLAYING':
            if spotify['cover']:
                Himage.paste(spotify['cover'], (int(art_x), art_y))
            else:
                draw_icon(draw, int(art_x), art_y, "icon_spotify", (art_size, art_size))

            draw_icon(draw, int(art_x + (art_size - 40) / 2), art_y + art_size + 15, "icon_play", (40, 40))

            # Artist/track names are external (Spotify) text of unpredictable
            # length and character width, like the affirmation line — a
            # naive character-count slice can still overflow the column, so
            # measure and ellipsis-truncate in pixels instead. They may also
            # be in Chinese/Japanese/Russian, which Aldrich can't render at
            # all (see needs_intl_font()) — pick the font per field first,
            # since wrapping/measuring both depend on which font is used.
            words = spotify['text'].split(' - ')
            artist_raw = words[0] if len(words) > 0 else "Unknown"
            track_raw = words[1] if len(words) > 1 else ""
            artist_font = fonts['intl_28'] if needs_intl_font(artist_raw) else fonts['28']
            track_font = fonts['intl_24'] if needs_intl_font(track_raw) else fonts['24']
            artist = wrap_lines_limited(draw, artist_raw, artist_font, content_w, max_lines=1)[0] if artist_raw else ""
            track = wrap_lines_limited(draw, track_raw, track_font, content_w, max_lines=1)[0] if track_raw else ""

            row_y = art_y + art_size + 70
            aw = text_width(draw, artist, artist_font)
            draw.text((col2_x + max(0, (content_w - aw) / 2), row_y), artist, font=artist_font, fill=0)
            tw = text_width(draw, track, track_font)
            draw.text((col2_x + max(0, (content_w - tw) / 2), row_y + 40), track, font=track_font, fill=0)
        else:
            draw_icon(draw, int(art_x), art_y, "icon_spotify", (art_size, art_size))
            msg = "Nothing Playing"
            mw = text_width(draw, msg, fonts['28'])
            draw.text((col2_x + max(0, (content_w - mw) / 2), art_y + art_size + 40), msg, font=fonts['28'], fill=0)

    draw.line((col_w * 2, 10, col_w * 2, 470), fill=0, width=2)

    # --- COLUMN 3 (Time, Claude/Spotify/Progress, Gmail) ---
    col3_x = col_w * 2 + 30
    dt = datetime.now()

    # 1. Time & Date
    draw.text((col3_x, 10), dt.strftime("%H:%M"), font=fonts['clock'], fill=0)

    date_str = dt.strftime("%d %B %Y")
    day_str = dt.strftime("%a").upper()

    draw.text((col3_x, 170), date_str, font=fonts['32'], fill=0)
    draw.text((col3_x + 340, 170), day_str, font=fonts['32'], fill=0)

    draw.line((col3_x, 220, epd.width - 20, 220), fill=0, width=2)

    # 2. Claude AI OR Spotify OR Time Progress
    sp_y = 240
    # Clear background for widget
    draw.rectangle((col3_x, sp_y, col3_x + 420, sp_y + 130), fill=255)

    if ENABLE_CLAUDE:
        draw.text((col3_x, sp_y), "CLAUDE AI USAGE", font=fonts['28'], fill=0)

        if claude.get('error'):
            draw.text((col3_x, sp_y + 50), "Claude Usage Error", font=fonts['24'], fill=0)
        else:
            # 5-Hour Limit
            pct_5h = claude.get('five_hour', {}).get('utilization', 0)
            resets_5h = claude.get('five_hour', {}).get('resets_at')
            rem_5h = time_until(resets_5h)

            draw.text((col3_x, sp_y + 40), f"5-Hour Limit: {pct_5h}% (Resets in {rem_5h})", font=fonts['20'], fill=0)
            bx, bw, bh = col3_x, 400, 15
            draw.rectangle((bx, sp_y + 65, bx + bw, sp_y + 65 + bh), outline=0, width=2)
            fill_w = int((bw - 4) * min(pct_5h / 100.0, 1.0))
            if fill_w > 0: draw.rectangle((bx + 2, sp_y + 67, bx + 2 + fill_w, sp_y + 65 + bh - 2), fill=0)

            # 7-Day Limit
            pct_7d = claude.get('seven_day', {}).get('utilization', 0)
            resets_7d = claude.get('seven_day', {}).get('resets_at')
            rem_7d = time_until(resets_7d)

            draw.text((col3_x, sp_y + 90), f"7-Day Limit: {pct_7d}% (Resets in {rem_7d})", font=fonts['20'], fill=0)
            draw.rectangle((bx, sp_y + 115, bx + bw, sp_y + 115 + bh), outline=0, width=2)
            fill_w = int((bw - 4) * min(pct_7d / 100.0, 1.0))
            if fill_w > 0: draw.rectangle((bx + 2, sp_y + 117, bx + 2 + fill_w, sp_y + 115 + bh - 2), fill=0)

    else:
        # Fallback: Greeting
        gr_y = sp_y
        widget_w = 400

        if dt.hour < 12:
            time_word = "Morning"
        elif dt.hour < 17:
            time_word = "Afternoon"
        else:
            time_word = "Evening"

        segments = [(f"{time_word} ", fonts['35'])]
        if random.random() < GREETING_INTL_CHANCE:
            if random.random() < GREETING_ZH_CHANCE:
                word, font_key = GREETING_ZH_HELLO, 'cjk_greeting'
            else:
                word, font_key = random.choice(GREETING_INTL_HELLOS)
            segments = [(word, fonts[font_key]), (" ", fonts['35'])]
        segments.append((GREETING_NAME, fonts['35']))

        gw = sum(text_width(draw, t, f) for t, f in segments)
        draw_mixed_text(draw, col3_x + max(0, (widget_w - gw) / 2), gr_y + 45, segments)

        # affirmation is API-sourced text of unpredictable length (unlike
        # every other string in this widget) — wrap_lines_limited() keeps it
        # from ever overflowing the widget regardless of what comes back.
        for i, line in enumerate(wrap_lines_limited(draw, affirmation, fonts['14'], widget_w, max_lines=2)):
            lw = text_width(draw, line, fonts['14'])
            draw.text((col3_x + max(0, (widget_w - lw) / 2), gr_y + 90 + i * 18), line, font=fonts['14'], fill=0)

    draw.line((col3_x, 380, epd.width - 20, 380), fill=0, width=2)

    # 3. Email (Gmail or Outlook, per EMAIL_PROVIDER)
    gm_y = 400
    gm_icon_size = 60
    draw_icon(draw, col3_x, gm_y, "icon_mail", (gm_icon_size, gm_icon_size))
    email_text = f"Unread Today: {email_unread_today}"
    bbox = draw.textbbox((0, 0), email_text, font=fonts['35'])
    text_y = gm_y + (gm_icon_size - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((col3_x + 80, text_y), email_text, font=fonts['35'], fill=0)

    return Himage


# --- FONTS ---
def load_fonts():
    def load_font(name, size):
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

    return {
        '14': load_font('Aldrich-Regular.ttc', 14),
        '20': load_font('Aldrich-Regular.ttc', 20),
        '24': load_font('Aldrich-Regular.ttc', 24),
        '28': load_font('Aldrich-Regular.ttc', 28),
        '32': load_font('Aldrich-Regular.ttc', 32),
        '35': load_font('Aldrich-Regular.ttc', 35),
        '40': load_font('Aldrich-Regular.ttc', 40),
        '60': load_font('Aldrich-Regular.ttc', 60),
        '80': load_font('Aldrich-Regular.ttc', 80),
        'clock': load_font('advanced_led_board-7.ttc', 180),
        'cjk_greeting': load_font('CJK-Greeting.ttf', 40),
        'intl_24': load_font('CJK-Cyrillic-Spotify.ttf', 24),
        'intl_28': load_font('CJK-Cyrillic-Spotify.ttf', 28),
    }


# --- MAIN LOOP ---
def main():
    auth_strava()
    auth_spotify()
    auth_gmail()
    auth_outlook()
    auth_claude()
    auth_antigravity()
    auth_codex()
    roborock_user_data = auth_roborock(ROBOROCK_CONF['EMAIL'])

    signal.signal(signal.SIGALRM, timeout_handler)
    epd = None

    try:
        epd = epd10in85.EPD()
        epd.init()
        epd.Clear()
        time.sleep(1)
        epd.init_Part()

        fonts = load_fonts()

        t_data = threading.Thread(target=update_data_thread)
        t_data.daemon = True
        t_data.start()

        if ENABLE_ROBOROCK:
            t_robo = threading.Thread(target=roborock_update_thread, args=(roborock_user_data, ROBOROCK_CONF['EMAIL']))
            t_robo.daemon = True
            t_robo.start()

        refresh_counter = 0

        while True:
            start_time = time.time()
            try:
                # CPU-bound rendering runs OUTSIDE the hardware watchdog: on a
                # Pi Zero 1 it can be slow, but it never hangs, so a slow render
                # must not trigger a reboot and throw away the frame.
                image = render_screen(epd, fonts)
                buf = epd.getbuffer(image)

                if refresh_counter >= 600:
                    logging.info("Full Refresh cycle")
                    signal.alarm(90)  # full refresh flashes the whole panel, it is slow
                    epd.init()
                    epd.display(buf)
                    time.sleep(2)
                    epd.init_Part()
                    signal.alarm(0)
                    refresh_counter = 0
                else:
                    logging.debug("Partial Refresh")
                    signal.alarm(30)  # watchdog guards only the SPI/BUSY transfer
                    # On the Pi Zero 1 the panel reliably completes only the
                    # FIRST partial after an init: afterwards the controller
                    # latches BUSY low forever. init_Part() does a hardware reset
                    # (RST pin) that pulls it out of that state, so re-arm before
                    # every frame. Skipped on faster boards (see the flag above).
                    if PANEL_REINIT_EACH_FRAME:
                        epd.init_Part()
                    epd.display_Partial(buf, 0, 0, epd.width, epd.height)
                    signal.alarm(0)
                    refresh_counter += 1

                del image
                del buf
                if refresh_counter % 10 == 0: gc.collect()

            except HardwareTimeoutError:
                logging.critical("HARDWARE HANG DETECTED!")
                signal.alarm(0)
                logging.shutdown()
                os.execv(sys.executable, ['python'] + sys.argv)
            except OSError as e:
                signal.alarm(0)
                if e.errno == 24:
                    os.execv(sys.executable, ['python'] + sys.argv)
            except Exception as e:
                signal.alarm(0)
                logging.error(f"Unexpected error in main: {e}")

            elapsed = time.time() - start_time
            sleep_time = max(5, 60 - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        try:
            signal.alarm(0)
            epd10in85.epdconfig.module_exit(cleanup=True)
        except:
            pass
        exit()


if __name__ == '__main__':
    main()
