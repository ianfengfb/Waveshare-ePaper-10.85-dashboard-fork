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
# The middle column shows Spotify now-playing instead of Weather while this
# is False. Kept as a toggle (not deleted) so weather can come back on this
# or another slot later — see CLAUDE.md roadmap.
ENABLE_WEATHER = False
# Only gates auth_gmail()'s interactive one-time prompt (so a fresh checkout
# never blocks on input()). The Gmail widget itself is unconditional and
# always visible — update_data_thread's fetch already self-gates on whether
# token.json exists, same as before this flag existed.
ENABLE_GMAIL = False

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
        self.gmail_unread = 0
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

        self.last_update = {
            'weather': 0, 'strava': 0, 'printer': 0, 'gmail': 0,
            'spotify': 0, 'crypto': 0, 'sysload': 0, 'ping': 0,
            'claude': 0, 'antigravity': 0, 'codex': 0, 'affirmation': 0
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
    if not ENABLE_GMAIL: return

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


def update_data_thread():
    global global_printer

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

        if now - data_store.last_update['gmail'] > 300:
            try:
                creds = None
                if os.path.exists(GMAIL_TOKEN_PATH):
                    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                        with open(GMAIL_TOKEN_PATH, 'w') as t: t.write(creds.to_json())
                if creds and creds.valid:
                    service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
                    label_info = service.users().labels().get(userId='me', id='INBOX').execute()
                    with data_store.lock: data_store.gmail_unread = label_info.get('messagesUnread', 0)
            except:
                pass
            data_store.last_update['gmail'] = now

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

        gc.collect()
        time.sleep(1)


# --- GRAPHICS FUNCTIONS ---
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
        gmail_unread = data_store.gmail_unread
        spotify = data_store.spotify.copy()
        claude = data_store.claude.copy()
        antigravity = data_store.antigravity.copy()
        codex = data_store.codex.copy()
        sysload = data_store.sysload.copy()
        crypto = data_store.crypto.copy()
        ping = data_store.ping.copy()
        affirmation = data_store.affirmation
    finally:
        data_store.lock.release()

    col_w = epd.width // 3

    # --- COLUMN 1 (Widgets) ---
    col1_x = 20

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

    if ENABLE_WEATHER and 'current' in weather:
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

    elif not ENABLE_WEATHER:
        # Fallback: Spotify now-playing (replaces Weather in this slot)
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

            # Artist/track names are external (Last.fm) text of unpredictable
            # length and character width, like the affirmation line — a
            # naive character-count slice can still overflow the column, so
            # measure and ellipsis-truncate in pixels instead.
            words = spotify['text'].split(' - ')
            artist_raw = words[0] if len(words) > 0 else "Unknown"
            track_raw = words[1] if len(words) > 1 else ""
            artist = wrap_lines_limited(draw, artist_raw, fonts['28'], content_w, max_lines=1)[0] if artist_raw else ""
            track = wrap_lines_limited(draw, track_raw, fonts['24'], content_w, max_lines=1)[0] if track_raw else ""

            row_y = art_y + art_size + 70
            aw = text_width(draw, artist, fonts['28'])
            draw.text((col2_x + max(0, (content_w - aw) / 2), row_y), artist, font=fonts['28'], fill=0)
            tw = text_width(draw, track, fonts['24'])
            draw.text((col2_x + max(0, (content_w - tw) / 2), row_y + 40), track, font=fonts['24'], fill=0)
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

    # 3. Gmail
    gm_y = 400
    gm_icon_size = 60
    draw_icon(draw, col3_x, gm_y, "icon_mail", (gm_icon_size, gm_icon_size))
    gmail_text = f"Unread Inbox: {gmail_unread}"
    bbox = draw.textbbox((0, 0), gmail_text, font=fonts['35'])
    text_y = gm_y + (gm_icon_size - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((col3_x + 80, text_y), gmail_text, font=fonts['35'], fill=0)

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
    }


# --- MAIN LOOP ---
def main():
    auth_strava()
    auth_spotify()
    auth_gmail()
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
