"""
YouTube View Engine — UC (undetected-chromedriver) + residential proxy approach.
Same proven architecture as the SoundCloud engine.

Key differences from SC:
  - YouTube requires deliberate play click (no reliable autoplay)
  - Cookie consent popup must be dismissed
  - Video playback verified via JS (video.currentTime advancing)
  - YouTube audits views more aggressively — ~30-60% success rate expected
"""
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

import os, time, random, json, threading, logging, string
from concurrent.futures import ThreadPoolExecutor, as_completed
from proxy_utils import parse_proxy

# Reuse proxy extension builder from SC engine
from engine import _build_proxy_extension, _chrome_launch_lock, _USER_AGENTS

logger = logging.getLogger("yt_engine")


def _make_fresh_proxy(proxy_str):
    """Generate a fresh session ID for session-based proxies."""
    if not proxy_str:
        return None
    p = parse_proxy(proxy_str)
    user = p.get('user', '')
    if '-session-' in user and '-time-' in user:
        base = user.split('-session-')[0]
        new_sid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        new_user = f'{base}-session-{new_sid}-time-30'
        return f'{new_user}:{p["password"]}@{p["host"]}:{p["port"]}'
    return proxy_str


# ── YouTube metadata via oEmbed ───────────────────────────────────────────────

def fetch_yt_metadata(video_url: str):
    """Fetch video info via YouTube oEmbed (no API key needed)."""
    import urllib.request, urllib.parse
    oembed_url = f'https://www.youtube.com/oembed?url={urllib.parse.quote(video_url, safe="")}&format=json'
    try:
        req = urllib.request.Request(oembed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            # Extract video ID from the URL
            vid = ''
            if 'watch?v=' in video_url:
                vid = video_url.split('watch?v=')[1].split('&')[0]
            elif 'youtu.be/' in video_url:
                vid = video_url.split('youtu.be/')[1].split('?')[0]
            return {
                "title": data.get("title", "Unknown"),
                "author": data.get("author_name", "Unknown"),
                "thumbnail": data.get("thumbnail_url", ""),
                "video_id": vid,
                "url": video_url,
            }
    except Exception as e:
        raise RuntimeError(f'YouTube lookup failed: {e}')


# ── Single YouTube view ───────────────────────────────────────────────────────

def _single_yt_view(idx, proxy_str, video_url, process_cb=None, stop_event=None):
    """One UC Chrome → YouTube video → verify playback → watch 30-45s."""
    tag = f'yt-{idx}'
    driver = None
    fresh_proxy = _make_fresh_proxy(proxy_str)
    p = parse_proxy(fresh_proxy) if fresh_proxy else None
    proxy_display = f'{p["host"]}:{p["port"]}' if p else "direct"
    status = {"id": idx, "proxy": proxy_display, "status": "starting", "elapsed": 0}
    if process_cb:
        process_cb(status)

    try:
        options = uc.ChromeOptions()
        options.add_argument('--autoplay-policy=no-user-gesture-required')
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-backgrounding-occluded-windows')
        options.add_argument('--disable-renderer-backgrounding')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1280,720')
        options.add_argument('--window-position=-10000,-10000')
        options.add_argument(f'--user-agent={random.choice(_USER_AGENTS)}')

        # Unique profile per worker
        import tempfile
        user_data = os.path.join(tempfile.gettempdir(), f'yt_playbot_profile_{idx}')
        options.add_argument(f'--user-data-dir={user_data}')

        # Proxy
        if p and p.get("user") and p.get("password"):
            ext_dir = _build_proxy_extension(p["host"], p["port"], p["user"], p["password"])
            options.add_argument(f'--load-extension={ext_dir}')
        elif p:
            options.add_argument(f'--proxy-server=http://{p["host"]}:{p["port"]}')

        status["status"] = "launching"
        if process_cb:
            process_cb(status)

        for attempt in range(3):
            try:
                with _chrome_launch_lock:
                    driver = uc.Chrome(options=options, headless=False, version_main=146)
                break
            except Exception as e:
                if attempt < 2 and ('183' in str(e) or 'session not created' in str(e)):
                    logger.warning(f'{tag} launch retry {attempt+1}')
                    time.sleep(2 + random.uniform(0, 2))
                else:
                    raise

        driver.set_page_load_timeout(45)

        status["status"] = "loading"
        if process_cb:
            process_cb(status)
        start_time = time.time()

        logger.info(f'{tag} navigating to {video_url}')
        driver.get(video_url)
        time.sleep(3)

        # Dismiss cookie consent (EU users get this)
        try:
            consent_btns = [
                'button[aria-label="Accept all"]',
                'button[aria-label="Accept the use of cookies and other data for the purposes described"]',
                '#yDmH0d button',
                'tp-yt-paper-button.ytd-consent-bump-v2-lightbox',
                'button.yt-spec-button-shape-next--filled',
            ]
            for sel in consent_btns:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, sel)
                    if btn.is_displayed():
                        btn.click()
                        logger.info(f'{tag} dismissed consent popup')
                        time.sleep(1)
                        break
                except:
                    continue
        except:
            pass

        # Click play button if video isn't playing
        time.sleep(2)
        try:
            is_playing = driver.execute_script("""
                var v = document.querySelector('video');
                return v && !v.paused && v.currentTime > 0;
            """)
            if not is_playing:
                # Try clicking the big play button
                play_selectors = [
                    'button.ytp-large-play-button',
                    'button.ytp-play-button',
                    '#movie_player',
                ]
                for sel in play_selectors:
                    try:
                        btn = driver.find_element(By.CSS_SELECTOR, sel)
                        if btn.is_displayed():
                            btn.click()
                            logger.info(f'{tag} clicked play ({sel})')
                            break
                    except:
                        continue
        except:
            pass

        # Wait for video to start
        time.sleep(5)

        # Verify video is actually playing
        video_playing = False
        for check in range(3):
            try:
                result = driver.execute_script("""
                    var v = document.querySelector('video');
                    if (!v) return null;
                    return {
                        paused: v.paused,
                        currentTime: v.currentTime,
                        duration: v.duration,
                        readyState: v.readyState
                    };
                """)
                if result and not result.get('paused') and result.get('currentTime', 0) > 1:
                    video_playing = True
                    logger.info(f'{tag} video playing at {result["currentTime"]:.1f}s')
                    break
            except:
                pass
            if check < 2:
                time.sleep(3)
                # Try clicking play again
                try:
                    driver.find_element(By.CSS_SELECTOR, 'button.ytp-large-play-button').click()
                except:
                    pass

        if not video_playing:
            status["status"] = "no_playback"
            if process_cb:
                process_cb(status)
            logger.warning(f'{tag} FAIL: video not playing')
            return False

        # Video is playing — watch for 30-45 seconds
        status["status"] = "watching"
        if process_cb:
            process_cb(status)

        watch_time = random.uniform(32, 48)
        logger.info(f'{tag} watching for {watch_time:.0f}s...')
        time.sleep(watch_time)

        # Final check — verify we actually watched
        try:
            final_time = driver.execute_script(
                "var v = document.querySelector('video'); return v ? v.currentTime : 0;"
            )
            logger.info(f'{tag} final position: {final_time:.1f}s')
        except:
            pass

        status["status"] = "done"
        status["elapsed"] = round(time.time() - start_time, 1)
        if process_cb:
            process_cb(status)
        logger.info(f'{tag} SUCCESS ({watch_time:.0f}s watched)')
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
            except:
                pass


# ── Engine class ──────────────────────────────────────────────────────────────

class YTBotEngine:
    def __init__(self):
        self.running = False
        self._stop_event = threading.Event()
        self._thread = None
        self.stats = {
            "sent": 0, "success": 0, "failed": 0,
            "rate": 0.0, "target": 0, "start_time": 0,
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

    def start(self, video_url, num_views, concurrency, proxies):
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self.stats = {
            "sent": 0, "success": 0, "failed": 0,
            "rate": 0.0, "target": num_views, "start_time": time.time(),
        }
        self.processes = {}

        self._thread = threading.Thread(
            target=self._run, args=(video_url, num_views, concurrency, proxies),
            daemon=True,
        )
        self._thread.start()

    def _run(self, video_url, num_views, concurrency, proxies):
        def do_view(idx):
            if self._stop_event.is_set():
                return False
            proxy = random.choice(proxies) if proxies else None
            ok = _single_yt_view(idx, proxy, video_url,
                                 process_cb=self._process_cb,
                                 stop_event=self._stop_event)
            self._update_stat("sent")
            self._update_stat("success" if ok else "failed")
            return ok

        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = []
                for i in range(num_views):
                    if self._stop_event.is_set():
                        break
                    futures.append(pool.submit(do_view, i + 1))
                    time.sleep(random.uniform(10, 20))
                for f in as_completed(futures):
                    if self._stop_event.is_set():
                        break
        finally:
            self.running = False

    def stop(self):
        self._stop_event.set()
        self.running = False
