"""
Bot engine — proven UC (undetected-chromedriver) + widget approach.
This is the ONLY method verified to actually register plays
(play count went 2→4 in testing on 2026-03-21).

Key principles:
  - undetected-chromedriver patches Chrome to bypass DataDome
  - headless=False (required — headless silently fails to register)
  - Widget URL with auto_play=true (NOT full page)
  - Do NOT click play button (it toggles/pauses auto_play)
  - Verify real audio stream via api-widget.soundcloud.com/media/
  - Proxy auth via Chrome extension (preserves TLS fingerprint)
  - Wait 33-45 seconds (SC needs ≥30s of streaming)
"""
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

import sys, os, time, random, json, zipfile, threading, logging, string
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as crequests
from proxy_utils import parse_proxy

logger = logging.getLogger("engine")

# When frozen by PyInstaller, __file__ is inside _internal/; use exe dir for writable paths
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
]

_FALLBACK_CLIENT_ID = 'jEFGoP9keUaPecfq0yoP321bUoawy2LC'
_client_id_cache = {'id': None, 'ts': 0}
_CLIENT_ID_TTL = 3600  # re-scrape every hour
_EXT_DIR = os.path.join(_BASE_DIR, '_proxy_extensions')
_chrome_launch_lock = threading.Lock()


def _scrape_client_id() -> str:
    """Scrape a fresh client_id from SoundCloud's JS bundles. Falls back to hardcoded."""
    now = time.time()
    if _client_id_cache['id'] and (now - _client_id_cache['ts']) < _CLIENT_ID_TTL:
        return _client_id_cache['id']
    try:
        import re as _re
        resp = crequests.get('https://soundcloud.com', impersonate='chrome110', timeout=15)
        scripts = _re.findall(r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', resp.text)
        for url in reversed(scripts[-6:]):
            js = crequests.get(url, impersonate='chrome110', timeout=10)
            ids = _re.findall(r'client_id[=:"]+(\w{32})', js.text)
            if ids:
                _client_id_cache['id'] = ids[0]
                _client_id_cache['ts'] = now
                logger.info(f'Scraped fresh SC client_id: {ids[0][:8]}...')
                return ids[0]
    except Exception as e:
        logger.warning(f'client_id scrape failed: {e}')
    return _client_id_cache['id'] or _FALLBACK_CLIENT_ID


def get_client_id() -> str:
    """Get the current best client_id (scraped or fallback)."""
    return _scrape_client_id()


def _detect_chrome_version():
    """Auto-detect installed Chrome major version on any Windows PC."""
    import subprocess, winreg
    # Method 1: Registry (most reliable on Windows)
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Google\Chrome\BLBeacon')
        ver, _ = winreg.QueryValueEx(key, 'version')
        winreg.CloseKey(key)
        return int(ver.split('.')[0])
    except Exception:
        pass
    # Method 2: Common install paths
    for p in [
        os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    ]:
        if os.path.exists(p):
            try:
                out = subprocess.check_output(f'"{p}" --version', shell=True, text=True, timeout=10)
                return int(out.strip().split()[-1].split('.')[0])
            except Exception:
                pass
    return None  # Let UC auto-detect

_CHROME_VERSION = _detect_chrome_version()
logger.info(f'Chrome version: {_CHROME_VERSION or "auto-detect"}')


# ── Proxy auth Chrome extension ──────────────────────────────────────────────

def _build_proxy_extension(host, port, user, password):
    os.makedirs(_EXT_DIR, exist_ok=True)
    ext_id = 'proxy_' + str(abs(hash(host + user)) % 100000)
    extract_dir = os.path.join(_EXT_DIR, ext_id)
    if os.path.exists(os.path.join(extract_dir, 'manifest.json')):
        return extract_dir

    manifest = json.dumps({
        "version": "1.0.0", "manifest_version": 2, "name": "Proxy Auth",
        "permissions": ["proxy", "tabs", "unlimitedStorage", "storage",
                        "<all_urls>", "webRequest", "webRequestBlocking"],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0"
    })
    background = (
        'var config={mode:"fixed_servers",rules:{singleProxy:{scheme:"http",'
        'host:"' + host + '",port:parseInt(' + port + ')},bypassList:["localhost"]}};\n'
        'chrome.proxy.settings.set({value:config,scope:"regular"},function(){});\n'
        'chrome.webRequest.onAuthRequired.addListener(function(d){'
        'return{authCredentials:{username:"' + user + '",password:"' + password + '"}}'
        '},{urls:["<all_urls>"]},["blocking"]);'
    )
    os.makedirs(extract_dir, exist_ok=True)
    with open(os.path.join(extract_dir, "manifest.json"), "w") as f:
        f.write(manifest)
    with open(os.path.join(extract_dir, "background.js"), "w") as f:
        f.write(background)
    return extract_dir


# ── Track metadata ────────────────────────────────────────────────────────────

def fetch_track_metadata(track_id: str, client_id: str = None):
    cid = client_id or get_client_id()
    resp = crequests.get(
        f'https://api-v2.soundcloud.com/tracks/{track_id}?client_id={cid}',
        timeout=15, impersonate='chrome110',
    )
    if resp.status_code == 200:
        data = resp.json()
        return {
            "id": str(data.get("id", track_id)),
            "title": data.get("title", "Unknown"),
            "artist": data.get("user", {}).get("username", "Unknown"),
            "artwork_url": (data.get("artwork_url") or "").replace("-large", "-t500x500"),
            "permalink_url": data.get("permalink_url", ""),
            "duration_ms": data.get("full_duration", 0),
            "playback_count": data.get("playback_count", 0),
            "genre": data.get("genre", ""),
        }
    raise RuntimeError(f'Track fetch failed: HTTP {resp.status_code}')


def fetch_play_count(track_id: str, client_id: str = None):
    try:
        cid = client_id or get_client_id()
        resp = crequests.get(
            f'https://api-v2.soundcloud.com/tracks/{track_id}?client_id={cid}',
            timeout=10, impersonate='chrome110',
        )
        if resp.status_code == 200:
            return resp.json().get('playback_count')
    except Exception:
        pass
    return None


# ── Single play ───────────────────────────────────────────────────────────────

def _make_fresh_proxy(proxy_str):
    """Generate a fresh session ID for session-based proxies so each play gets a unique IP."""
    if not proxy_str:
        return None
    p = parse_proxy(proxy_str)
    user = p.get('user', '')
    # Detect Nettify-style session proxies: user-session-XXXXX-time-N
    if '-session-' in user and '-time-' in user:
        base = user.split('-session-')[0]
        # Force time-30 (30 min sticky) so IP doesn't rotate mid-play
        new_sid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        new_user = f'{base}-session-{new_sid}-time-30'
        return f'{new_user}:{p["password"]}@{p["host"]}:{p["port"]}'
    return proxy_str


def _single_play(idx, proxy_str, track_id, process_cb=None, stop_event=None):
    """One UC Chrome -> widget -> verify audio stream -> wait 33-45s."""
    tag = f'p-{idx}'
    driver = None
    # Generate a FRESH proxy session for this specific play
    fresh_proxy = _make_fresh_proxy(proxy_str)
    p = parse_proxy(fresh_proxy) if fresh_proxy else None
    proxy_display = f'{p["host"]}:{p["port"]}' if p else "direct"
    status = {"id": idx, "proxy": proxy_display, "status": "starting", "elapsed": 0}
    if process_cb:
        process_cb(status)

    try:
        options = uc.ChromeOptions()
        options.add_argument('--mute-audio')
        options.add_argument('--autoplay-policy=no-user-gesture-required')
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-backgrounding-occluded-windows')
        options.add_argument('--disable-renderer-backgrounding')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=480,320')
        options.add_argument('--window-position=-10000,-10000')
        # Unique user-agent per play to avoid SC deduplication (IP + UA)
        options.add_argument(f'--user-agent={random.choice(_USER_AGENTS)}')

        # Proxy via Chrome extension (preserves TLS fingerprint)
        if p and p.get("user") and p.get("password"):
            ext_dir = _build_proxy_extension(p["host"], p["port"], p["user"], p["password"])
            options.add_argument(f'--load-extension={ext_dir}')
        elif p:
            options.add_argument(f'--proxy-server=http://{p["host"]}:{p["port"]}')

        # Unique profile per worker to avoid Chrome lock conflicts
        import tempfile
        user_data = os.path.join(tempfile.gettempdir(), f'sc_playbot_profile_{idx}')
        options.add_argument(f'--user-data-dir={user_data}')

        # Performance logging for audio verification
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        status["status"] = "launching"
        if process_cb:
            process_cb(status)

        # Serialize launches (UC patches chromedriver binary — can't be concurrent)
        # Retry up to 3 times on file collision
        for attempt in range(3):
            try:
                with _chrome_launch_lock:
                    driver = uc.Chrome(options=options, headless=False, version_main=_CHROME_VERSION)
                break
            except Exception as e:
                if attempt < 2 and ('183' in str(e) or 'session not created' in str(e)):
                    logger.warning(f'{tag} launch retry {attempt+1}: {str(e)[:60]}')
                    time.sleep(2 + random.uniform(0, 2))
                else:
                    raise
        driver.set_page_load_timeout(45)

        # Widget URL with auto_play
        widget_url = (
            'https://w.soundcloud.com/player/'
            f'?url=https%3A%2F%2Fapi.soundcloud.com%2Ftracks%2F{track_id}'
            '&auto_play=true&hide_related=true&show_comments=false'
            '&show_user=false&show_reposts=false&show_teaser=false&visual=false'
        )

        status["status"] = "loading"
        if process_cb:
            process_cb(status)
        start_time = time.time()

        logger.info(f'{tag} navigating to widget...')
        driver.get(widget_url)
        logger.info(f'{tag} loaded: {driver.title[:40]}')

        # Wait for auto_play to kick in — do NOT click play (it toggles pause!)
        time.sleep(3)

        # DataDome check
        page_len = len(driver.page_source)
        if page_len < 500:
            status["status"] = "blocked"
            if process_cb:
                process_cb(status)
            logger.warning(f'{tag} blocked (page {page_len}b)')
            return False

        # Check for REAL audio stream (api-widget.soundcloud.com/media/)
        def _has_real_audio():
            try:
                logs = driver.get_log('performance')
                for entry in logs:
                    msg = json.loads(entry['message'])['message']
                    if msg.get('method') == 'Network.requestWillBeSent':
                        url = msg.get('params', {}).get('request', {}).get('url', '')
                        if 'soundcloud.com/media' in url or 'cf-hls-media' in url:
                            logger.info(f'{tag} audio: {url[:80]}')
                            return True
            except Exception:
                pass
            return False

        audio_ok = _has_real_audio()

        # If auto_play didn't start audio, NOW click play as fallback
        if not audio_ok:
            logger.info(f'{tag} auto_play missed, clicking play...')
            try:
                driver.find_element(By.CSS_SELECTOR, 'button.playButton').click()
            except Exception:
                pass
            time.sleep(5)
            audio_ok = _has_real_audio()

        # Last chance
        if not audio_ok:
            time.sleep(5)
            audio_ok = _has_real_audio()

        if not audio_ok:
            status["status"] = "no_audio"
            if process_cb:
                process_cb(status)
            logger.warning(f'{tag} FAIL: no real audio stream')
            return False

        # Audio confirmed — wait for play to register
        status["status"] = "playing"
        if process_cb:
            process_cb(status)

        listen_time = random.uniform(31, 34)
        logger.info(f'{tag} streaming, waiting {listen_time:.0f}s...')
        time.sleep(listen_time)

        status["status"] = "done"
        status["elapsed"] = round(time.time() - start_time, 1)
        if process_cb:
            process_cb(status)
        logger.info(f'{tag} SUCCESS ({listen_time:.0f}s)')
        return True

    except Exception as e:
        status["status"] = "error"
        if process_cb:
            process_cb(status)
        logger.error(f'{tag} {e}')
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        # Clean up temp profile to prevent disk filling
        try:
            import shutil, tempfile
            profile_dir = os.path.join(tempfile.gettempdir(), f'sc_playbot_profile_{idx}')
            if os.path.exists(profile_dir):
                shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass


# ── Engine class ──────────────────────────────────────────────────────────────

class BotEngine:
    def __init__(self):
        self.running = False
        self._stop_event = threading.Event()
        self._thread = None
        self.stats = {
            "sent": 0, "success": 0, "failed": 0,
            "rate": 0.0, "play_count": 0, "play_count_start": 0,
            "target": 0, "start_time": 0,
        }
        self.processes = {}
        self._lock = threading.Lock()

    def _update_stat(self, key, delta=1):
        with self._lock:
            self.stats[key] += delta

    def _process_cb(self, status):
        with self._lock:
            self.processes[status["id"]] = dict(status)

    def get_snapshot(self):
        with self._lock:
            s = dict(self.stats)
            elapsed = time.time() - s["start_time"] if s["start_time"] else 0
            s["elapsed"] = round(elapsed, 1)
            s["rate"] = round(s["sent"] / elapsed * 60, 1) if elapsed > 1 else 0.0
            s["running"] = self.running
            s["processes"] = list(self.processes.values())
            return s

    def start(self, track_id, num_plays, concurrency, proxies):
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self.stats = {
            "sent": 0, "success": 0, "failed": 0,
            "rate": 0.0, "play_count": 0, "play_count_start": 0,
            "target": num_plays, "start_time": time.time(),
        }
        self.processes = {}

        pc = fetch_play_count(track_id)
        if pc is not None:
            self.stats["play_count"] = pc
            self.stats["play_count_start"] = pc

        self._thread = threading.Thread(
            target=self._run, args=(track_id, num_plays, concurrency, proxies),
            daemon=True,
        )
        self._thread.start()

    def _run(self, track_id, num_plays, concurrency, proxies):
        def do_play(idx):
            if self._stop_event.is_set():
                return False
            proxy = random.choice(proxies) if proxies else None
            ok = _single_play(idx, proxy, track_id,
                              process_cb=self._process_cb,
                              stop_event=self._stop_event)
            self._update_stat("sent")
            self._update_stat("success" if ok else "failed")

            # Refresh play count every 10 plays
            if self.stats["sent"] % 10 == 0:
                pc = fetch_play_count(track_id)
                if pc is not None:
                    with self._lock:
                        self.stats["play_count"] = pc
            return ok

        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = []
                for i in range(num_plays):
                    if self._stop_event.is_set():
                        break
                    futures.append(pool.submit(do_play, i + 1))
                    # Brief stagger to prevent chromedriver file collision
                    time.sleep(random.uniform(1.5, 3))
                for f in as_completed(futures):
                    if self._stop_event.is_set():
                        break
        finally:
            pc = fetch_play_count(track_id)
            if pc is not None:
                with self._lock:
                    self.stats["play_count"] = pc
            self.running = False

    def stop(self):
        self._stop_event.set()
        self.running = False
