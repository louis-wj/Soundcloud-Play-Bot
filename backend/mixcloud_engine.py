"""
Mixcloud Bot Engine — FastAPI router wrapping all features from:
  H:\MIXCLOUD TOOL\RELEASE\main.py
  H:\MIXCLOUD TOOL\RELEASE\2cap\2capscrape.py
  H:\MIXCLOUD TOOL\RELEASE\2cap\checkkey.py
  H:\MIXCLOUD TOOL\RELEASE\2cap\capmonsterscrape.py

All business logic is ported IDENTICALLY from those files.
"""
import requests
from curl_cffi import requests as cffi_requests
import random
import string
import json
import time
import re
import threading
import logging
import sys
import os
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.common.by import By
from faker import Faker

logger = logging.getLogger("mixcloud")
router = APIRouter(prefix="/api/mx", tags=["mixcloud"])

fake = Faker()
BROWSER_IMPERSONATE = "chrome124"

# ── File paths ────────────────────────────────────────────────────────────────
# When frozen by PyInstaller, __file__ is inside _internal/; config files live next to server.exe
if getattr(sys, 'frozen', False):
    _DIR = os.path.dirname(sys.executable)
else:
    _DIR = os.path.dirname(os.path.abspath(__file__))
_ACCOUNTS_FILE = os.path.join(_DIR, "mx2.txt")
_CAPTCHA_KEY_FILE = os.path.join(_DIR, "captchakey.txt")
_CSRF_TOKENS_FILE = os.path.join(_DIR, "csrftokens.txt")
_PROXY_FILE = os.path.join(_DIR, "proxy.txt")
_USERDB_FILE = os.path.join(_DIR, "userdb.txt")
_file_lock = threading.Lock()

# Auto-create all config files if they don't exist
for _f in [_ACCOUNTS_FILE, _CAPTCHA_KEY_FILE, _CSRF_TOKENS_FILE, _PROXY_FILE, _USERDB_FILE]:
    if not os.path.exists(_f):
        try:
            open(_f, "w").close()
            logger.info(f"[mx] Created missing config file: {os.path.basename(_f)}")
        except Exception:
            pass

# ── Load CSRF tokens ─────────────────────────────────────────────────────────
_csrf_tokens = []
try:
    with open(_CSRF_TOKENS_FILE, "r") as f:
        _csrf_tokens = [line.strip() for line in f.readlines() if line.strip()]
except Exception:
    pass
if _csrf_tokens:
    logger.info(f"[mx] Loaded {len(_csrf_tokens)} CSRF token(s) from csrftokens.txt")
else:
    logger.warning("[mx] No CSRF tokens in csrftokens.txt — add them via Config page")

# ── Global config (mutable via API) ──────────────────────────────────────────
_captcha_key = ""
_proxy_str = ""

# Load saved proxy
try:
    _saved_proxy = open(_PROXY_FILE, "r").read().strip()
    if _saved_proxy:
        _proxy_str = _saved_proxy
        logger.info(f"[mx] Loaded proxy from proxy.txt: {_proxy_str[:40]}")
except Exception:
    pass

# ── Captcha balance cache (avoid hammering the API every 0.5s) ───────────────
_captcha_balance_cache = 0.0
_captcha_balance_ts = 0.0
_CAPTCHA_CACHE_TTL = 30  # seconds

# Load saved captcha key
try:
    _captcha_key = open(_CAPTCHA_KEY_FILE, "r").read().strip()
except Exception:
    pass

BASE_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "accept": "*/*",
    "accept-language": "en-GB,en;q=0.9",
    "origin": "https://www.mixcloud.com",
    "referer": "https://www.mixcloud.com/",
}


def _get_proxies():
    return {"http": _proxy_str, "https": _proxy_str}


def _count_accounts() -> int:
    try:
        lines = [l.strip() for l in open(_ACCOUNTS_FILE, "r", encoding="utf-8").readlines() if l.strip()]
        return len(lines)
    except Exception:
        return 0


# ── Core Mixcloud API functions (ported verbatim from main.py) ────────────────

def random_string(n=10):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def init_session():
    """Create a session using curl_cffi with Chrome TLS fingerprint impersonation."""
    s = cffi_requests.Session(impersonate=BROWSER_IMPERSONATE)
    s.headers.update(BASE_HEADERS)
    s.proxies.update(_get_proxies())
    return s


def bootstrap_mixcloud_session(session, retries: int = 3):
    """Injects a pre-obtained CSRF token and hits GraphQL to get mx_t. Returns (mx_t, csrftoken)."""
    for attempt in range(1, retries + 1):
        try:
            if not _csrf_tokens:
                raise RuntimeError("No CSRF tokens loaded — add tokens to csrftokens.txt")
            csrf = random.choice(_csrf_tokens)
            session.cookies.set("csrftoken", csrf, domain=".mixcloud.com")
            session.headers["x-csrftoken"] = csrf
            session.headers["referer"] = "https://www.mixcloud.com/"

            r = session.get(
                "https://app.mixcloud.com/graphql",
                params={"query": "query { viewer { id } }"},
                proxies=_get_proxies(),
                timeout=20
            )
            r.raise_for_status()

            mx_t = session.cookies.get("mx_t")
            if not mx_t:
                raise RuntimeError("mx_t cookie not set")

            return mx_t, csrf
        except Exception as e:
            logger.warning(f"[mx] bootstrap attempt {attempt}/{retries} failed: {e}")
            if attempt == retries:
                raise RuntimeError(f"Failed to obtain Mixcloud session after {retries} attempts: {e}")
            time.sleep(2)


def register_account(session, email: str, password: str):
    username = f"{email.split('@')[0]}{random.randint(1000, 9999)}"
    payload = {
        "email": email,
        "password": password,
        "username": username,
        "ch": "y"
    }
    try:
        r = session.post(
            "https://app.mixcloud.com/authentication/email-register/",
            data=payload,
            proxies=_get_proxies()
        )
        data = r.json()
        logger.info(f"[mx] Register: {data}")
        if r.status_code != 200:
            return None, False
        if data.get("error") or data.get("errors"):
            return None, False
        return username, True
    except Exception as e:
        logger.error(f"[mx] Registration exception: {e}")
        return None, False


def login_account(session, email: str, password: str) -> bool:
    payload = {"email": email, "password": password}
    try:
        r = session.post(
            "https://app.mixcloud.com/authentication/email-login/",
            data=payload,
            proxies=_get_proxies()
        )
        if r.status_code != 200:
            logger.warning(f"[mx] Login failed (HTTP {r.status_code}): {r.text[:80]}")
            return False
        data = r.json()
        if data.get("error") or data.get("errors"):
            logger.warning(f"[mx] Login error: {data}")
            return False
        return True
    except Exception as e:
        logger.error(f"[mx] Login exception: {e}")
        return False


def follow_user(session, user_id, username):
    ensure_csrf(session)
    payload = {
        "query": (
            "mutation useAddFollowMutation($input: ChangeFollowingMutationInput!) { "
            "changeFollowing(input: $input) { user { isFollowing id } } }"
        ),
        "variables": {
            "input": {
                "userId": user_id,
                "isFollowing": True,
                "tracking": json.dumps([
                    {"type": "source", "referrer": ""},
                    {"type": "view", "view_name": "user", "params": {"username": username}},
                    {"type": "platform", "platform": "www"},
                    {"type": "url", "url": f"/{username}/"},
                    {"type": "view", "view_name": "user", "params": {"username": username}},
                    {"type": "view", "view_name": "user", "params": {"username": username}}
                ])
            }
        }
    }
    data = session.post(
        "https://app.mixcloud.com/graphql",
        json=payload,
        proxies=_get_proxies()
    )
    return data.json()


def get_user_id_follow(username: str) -> str:
    payload = {
        "query": """
        query GetUserId($lookup: UserLookup!) {
          user: userLookup(lookup: $lookup) {
            id
            username
          }
        }
        """,
        "variables": {"lookup": {"username": username}}
    }
    session = init_session()
    if _csrf_tokens:
        csrf = random.choice(_csrf_tokens)
        session.cookies.set("csrftoken", csrf, domain=".mixcloud.com")
        session.headers["x-csrftoken"] = csrf
    r = session.post(
        "https://app.mixcloud.com/graphql",
        json=payload,
    )
    r.raise_for_status()
    data = r.json()
    logger.info(f"[mx] User ID for {username}: {data['data']['user']['id']}")
    return data["data"]["user"]["id"]


def solve_captcha(session):
    if not _captcha_key:
        raise RuntimeError("No captcha key configured — set it in the Mixcloud Overview tab")
    from twocaptcha import TwoCaptcha
    SITE_KEY = '6Ldm6gYUAAAAACfwSMTCIuHYSlfv2ptnauPKtl07'
    PAGE_URL = 'https://www.mixcloud.com/jailed/'
    solver = TwoCaptcha(_captcha_key)
    result = solver.recaptcha(sitekey=SITE_KEY, url=PAGE_URL)
    token = result['code']
    ensure_csrf(session)
    resp = session.post(
        "https://app.mixcloud.com/jail/release/",
        data={"g-recaptcha-response": token},
        proxies=_get_proxies()
    )
    logger.info(f"[mx] Jail release status: {resp.status_code}")
    return token


def ensure_csrf(session):
    """Refresh the CSRF header from the cookie jar. Re-fetch if missing."""
    csrf = session.cookies.get("csrftoken")
    if not csrf:
        session.get("https://app.mixcloud.com/graphql", params={"query": "{viewer{id}}"}, proxies=_get_proxies())
        csrf = session.cookies.get("csrftoken")
    if not csrf:
        raise RuntimeError("Missing csrftoken cookie")
    session.headers["x-csrftoken"] = csrf
    session.headers["referer"] = "https://www.mixcloud.com/"


def get_random_account() -> str:
    """Returns 'email:password' for a random account from mx2.txt."""
    with _file_lock:
        with open(_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            accounts = [l.strip() for l in f.readlines() if l.strip()]
    if not accounts:
        raise RuntimeError("No accounts in mx2.txt — generate some first")
    acc = random.choice(accounts)
    return f"{acc.split(':')[0]}:{acc.split(':')[1]}"


# ── MixcloudEngine class ──────────────────────────────────────────────────────

class MixcloudEngine:
    def __init__(self):
        self.running = False
        self.operation = ""
        self._stop_event = threading.Event()
        self._thread = None
        self.stats = {
            "sent": 0, "success": 0, "failed": 0,
            "target": 0, "start_time": 0,
        }
        self._lock = threading.Lock()

    def _update_stat(self, key, delta=1):
        with self._lock:
            self.stats[key] += delta

    def get_snapshot(self) -> dict:
        global _captcha_balance_cache, _captcha_balance_ts
        with self._lock:
            s = dict(self.stats)
        elapsed = time.time() - s["start_time"] if s["start_time"] else 0
        s["elapsed"] = round(elapsed, 1)
        s["running"] = self.running
        s["operation"] = self.operation
        s["accounts"] = _count_accounts()
        s["proxy"] = _proxy_str
        # Captcha balance — only refresh every 30s to avoid hammering the API
        now = time.time()
        if _captcha_key and (now - _captcha_balance_ts) > _CAPTCHA_CACHE_TTL:
            try:
                from twocaptcha import TwoCaptcha
                _captcha_balance_cache = round(float(TwoCaptcha(_captcha_key).balance()), 5)
                _captcha_balance_ts = now
            except Exception:
                pass
        s["captcha_balance"] = _captcha_balance_cache
        s["csrf_tokens"] = len(_csrf_tokens)
        s["captcha_key_set"] = bool(_captcha_key)
        return s

    def stop(self):
        self._stop_event.set()
        self.running = False
        self.operation = ""
        logger.info("[mx] Engine stopped by user")

    # ── [1] Generate Accounts ────────────────────────────────────────────────

    def start_generate(self, count: int, threads: int, follow_random: bool):
        if self.running:
            return
        self.running = True
        self.operation = "generate"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": count, "start_time": time.time()}

        def _worker_gen():
            if self._stop_event.is_set():
                return
            try:
                session = init_session()
                session.proxies.update(_get_proxies())
                # Verify proxy is working (identical to original worker() in main.py)
                try:
                    ip = session.get("https://httpbin.org/ip", timeout=15)
                    logger.info(f"[mx] Using IP: {ip.json().get('origin', '?')}")
                except Exception as e:
                    logger.warning(f"[mx] Proxy check failed: {e} — proxy may be dead")
                bootstrap_mixcloud_session(session)
                email = f"{random_string()}@example.com"
                password = "ExamplePass123!"
                ensure_csrf(session)
                username, ok = register_account(session, email, password)
                if ok:
                    ensure_csrf(session)
                    try:
                        solve_captcha(session)
                    except Exception as ce:
                        logger.warning(f"[mx] Captcha skip: {ce}")
                    ensure_csrf(session)
                    csref = session.cookies.get("csrftoken", "")
                    mx_t = session.cookies.get("mx_t", "")
                    authc = session.cookies.get("c", "")
                    with _file_lock:
                        with open(_ACCOUNTS_FILE, "a", encoding="utf-8") as f:
                            f.write(f"{email}:ExamplePass123!:{username}:{csref}:{mx_t}:{authc}\n")
                    logger.info(f"[mx] Account created: {email} ({username})")
                    self._update_stat("success")
                else:
                    logger.warning(f"[mx] Registration failed for {email}")
                    self._update_stat("failed")
                self._update_stat("sent")
            except Exception as e:
                logger.error(f"[mx] Generate worker error: {e}")
                self._update_stat("failed")
                self._update_stat("sent")

        def _run():
            with ThreadPoolExecutor(max_workers=threads) as pool:
                futs = [pool.submit(_worker_gen) for _ in range(count)]
                for ft in futs:
                    if self._stop_event.is_set():
                        break
                    ft.result()
            self.running = False
            self.operation = ""
            logger.info(f"[mx] Generate complete — {self.stats['success']}/{count} accounts created")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── [2] Follow User (set amount) ─────────────────────────────────────────

    def start_follow(self, username: str, count: int, follow_random: bool, use_captcha: bool):
        if self.running:
            return
        self.running = True
        self.operation = "follow"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": count, "start_time": time.time()}

        def _run():
            try:
                user_id = get_user_id_follow(username)
            except Exception as e:
                logger.error(f"[mx] Cannot get user ID for {username}: {e}")
                self.running = False
                self.operation = ""
                return

            try:
                accounts = [l.strip() for l in open(_ACCOUNTS_FILE, "r", encoding="utf-8") if l.strip()]
            except Exception:
                accounts = []

            limit = min(count, len(accounts))
            i = 0
            for account in accounts:
                if i >= limit or self._stop_event.is_set():
                    break
                if not account or account.startswith("#"):
                    continue
                try:
                    email = account.split(":")[0]
                    password = account.split(":")[1]
                except (IndexError, ValueError):
                    continue

                for attempt in range(3):
                    try:
                        session = init_session()
                        bootstrap_mixcloud_session(session)
                        ensure_csrf(session)
                        login_account(session, email, password)
                        ensure_csrf(session)
                        data = follow_user(session, user_id, username)

                        if data.get("errors"):
                            if any(err.get("message") == "jailed" for err in data["errors"]):
                                logger.info(f"[mx] Jailed — solving captcha for {email}")
                                if use_captcha:
                                    solve_captcha(session)
                                    time.sleep(2)
                                    data = follow_user(session, user_id, username)
                                else:
                                    logger.warning(f"[mx] Jailed, captcha solving disabled: {email}")

                        if data.get("errors"):
                            raise Exception(f"Follow errors: {data['errors']}")

                        # Follow random users for legitimacy
                        if follow_random and os.path.exists(_USERDB_FILE):
                            try:
                                user_lines = [l.strip() for l in open(_USERDB_FILE).readlines() if l.strip()]
                                num_random = random.randint(1, 25)
                                for _ in range(num_random):
                                    ru_line = random.choice(user_lines)
                                    ru = ru_line.split(":")[0]
                                    rid = ru_line.split(":")[1]
                                    try:
                                        follow_user(session, rid, ru)
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                        logger.info(f"[mx] Follow {i+1}/{limit} sent from {email}")
                        self._update_stat("success")
                        self._update_stat("sent")
                        i += 1
                        break
                    except Exception as ex:
                        logger.warning(f"[mx] Follow attempt {attempt+1}/3 failed for {email}: {ex}")
                        time.sleep(1)
                        if attempt == 2:
                            self._update_stat("failed")
                            self._update_stat("sent")

            self.running = False
            self.operation = ""
            logger.info(f"[mx] Follow complete — {self.stats['success']}/{limit} sent")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── [3] Mass Follow (fast, threaded infinite) ─────────────────────────────

    def start_mass_follow(self, username: str, threads: int, use_captcha: bool):
        if self.running:
            return
        self.running = True
        self.operation = "mass_follow"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": 0, "start_time": time.time()}

        def _worker_mass(u_name, u_id):
            while not self._stop_event.is_set():
                try:
                    accs = [l.strip() for l in open(_ACCOUNTS_FILE, "r", encoding="utf-8") if l.strip()]
                    if not accs:
                        time.sleep(2)
                        continue
                    acc = random.choice(accs)
                    email = acc.split(":")[0]
                    password = acc.split(":")[1]

                    session = init_session()
                    bootstrap_mixcloud_session(session)
                    ensure_csrf(session)
                    login_account(session, email, password)
                    ensure_csrf(session)
                    data = follow_user(session, u_id, u_name)

                    if data.get("errors"):
                        if any(err.get("message") == "jailed" for err in data["errors"]):
                            if use_captcha:
                                logger.info(f"[mx] Jailed — solving captcha: {email}")
                                token = solve_captcha(session)
                    # Captcha already submitted in solve_captcha()
                                time.sleep(2)
                                data = follow_user(session, u_id, u_name)
                                logger.info("[mx] Follow sent after captcha")
                            else:
                                logger.warning(f"[mx] Jailed: {email}")
                    else:
                        logger.info(f"[mx] Mass follow sent from {email}")
                        self._update_stat("success")
                    self._update_stat("sent")
                except Exception as e:
                    logger.warning(f"[mx] Mass follow error: {e}")
                    time.sleep(1)

        def _run():
            try:
                user_id = get_user_id_follow(username)
            except Exception as e:
                logger.error(f"[mx] Cannot get user ID: {e}")
                self.running = False
                self.operation = ""
                return
            ts = [threading.Thread(target=_worker_mass, args=(username, user_id), daemon=True)
                  for _ in range(threads)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.running = False
            self.operation = ""

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── [5] Live Viewers ─────────────────────────────────────────────────────

    def start_live_viewers(self, url: str, count: int):
        if self.running:
            return
        self.running = True
        self.operation = "live_viewers"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": count, "start_time": time.time()}

        def _viewer_worker():
            options = webdriver.ChromeOptions()
            options.add_argument("--incognito")
            options.add_argument("--mute-audio")
            options.add_argument("--headless")
            driver = webdriver.Chrome(options=options)
            try:
                session = init_session()
                bootstrap_mixcloud_session(session)
                ensure_csrf(session)
                acc = get_random_account()
                email = acc.split(":")[0]
                password = acc.split(":")[1]
                login_account(session, email, password)
                ensure_csrf(session)
                cookies = session.cookies.get_dict()
                driver.get(url)
                for name, value in cookies.items():
                    try:
                        driver.add_cookie({
                            "name": name, "value": value,
                            "path": "/", "domain": ".mixcloud.com"
                        })
                    except Exception:
                        pass
                driver.get(url)
                logger.info(f"[mx] Viewer active: {email}")
                self._update_stat("success")
                self._update_stat("sent")
                # Keep the viewer alive until stop
                while not self._stop_event.is_set():
                    time.sleep(5)
            except Exception as e:
                logger.error(f"[mx] Viewer error: {e}")
                self._update_stat("failed")
                self._update_stat("sent")
            finally:
                try:
                    driver.quit()
                except Exception:
                    pass

        def _run():
            ts = [threading.Thread(target=_viewer_worker, daemon=True) for _ in range(count)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.running = False
            self.operation = ""

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── [6] Live Chat Botter ─────────────────────────────────────────────────

    def start_live_chat(self, url: str, count: int):
        if self.running:
            return
        self.running = True
        self.operation = "live_chat"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": count, "start_time": time.time()}

        def _chat_worker():
            options = webdriver.ChromeOptions()
            options.add_argument("--incognito")
            options.add_argument("--mute-audio")
            driver = webdriver.Chrome(options=options)
            try:
                session = init_session()
                bootstrap_mixcloud_session(session)
                ensure_csrf(session)
                acc = get_random_account()
                email = acc.split(":")[0]
                password = acc.split(":")[1]
                login_account(session, email, password)
                ensure_csrf(session)
                cookies = session.cookies.get_dict()
                driver.get(url)
                for name, value in cookies.items():
                    try:
                        driver.add_cookie({
                            "name": name, "value": value,
                            "path": "/", "domain": ".mixcloud.com"
                        })
                    except Exception:
                        pass
                driver.get(url)
                driver.minimize_window()
                logger.info(f"[mx] Chat bot live: {email}")
                self._update_stat("success")
                while not self._stop_event.is_set():
                    try:
                        sentence = fake.sentence()
                        driver.find_element(
                            By.XPATH,
                            '//*[@id="react-root"]/div[1]/div/div/div[2]/span/div/div/div[2]/div[2]/div[2]/form/div/textarea'
                        ).send_keys(sentence)
                        driver.find_element(
                            By.XPATH,
                            '//*[@id="react-root"]/div[1]/div/div/div[2]/span/div/div/div[2]/div[2]/div[2]/form/div/div/div[2]/button[2]'
                        ).click()
                        logger.info(f"[mx] Chat sent: {sentence[:60]}")
                        self._update_stat("sent")
                        time.sleep(random.randint(8, 25))
                    except Exception:
                        time.sleep(3)
            except Exception as e:
                logger.error(f"[mx] Chat error: {e}")
                self._update_stat("failed")
            finally:
                try:
                    driver.quit()
                except Exception:
                    pass

        def _run():
            ts = [threading.Thread(target=_chat_worker, daemon=True) for _ in range(count)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.running = False
            self.operation = ""

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── [7] Play Botter ──────────────────────────────────────────────────────

    def start_play(self, url: str, threads: int, views_per_thread: int):
        if self.running:
            return
        self.running = True
        self.operation = "play"
        self._stop_event.clear()
        total = threads * views_per_thread
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": total, "start_time": time.time()}

        def _play_worker(vpw: int):
            for i in range(vpw):
                if self._stop_event.is_set():
                    break
                options = webdriver.ChromeOptions()
                options.add_argument("--incognito")
                options.add_argument("--mute-audio")
                driver = webdriver.Chrome(options=options)
                sleeptime = 2
                played = False
                try:
                    session = init_session()
                    bootstrap_mixcloud_session(session)
                    ensure_csrf(session)
                    acc = get_random_account()
                    email = acc.split(":")[0]
                    password = acc.split(":")[1]
                    login_account(session, email, password)
                    ensure_csrf(session)
                    cookies = session.cookies.get_dict()
                    driver.get(url)
                    for name, value in cookies.items():
                        try:
                            driver.add_cookie({
                                "name": name, "value": value,
                                "path": "/", "domain": ".mixcloud.com"
                            })
                        except Exception:
                            pass
                    driver.get(url)
                    # Try to click Play button
                    for _ in range(30):
                        try:
                            driver.find_element(
                                By.XPATH,
                                '//*[@id="react-root"]/div[1]/div[2]/div[1]/div/div/div[1]/div[2]/div[1]/div[3]/button'
                            ).click()
                            logger.info(f"[mx] Playing MIX #{i+1}")
                            played = True
                            break
                        except Exception:
                            time.sleep(1)
                    if played:
                        driver.minimize_window()
                        sleeptime = random.randint(100, 600)
                        logger.info(f"[mx] Play #{i+1} — sleeping {sleeptime}s")
                        self._update_stat("success")
                    else:
                        logger.warning(f"[mx] Play #{i+1} — could not find play button")
                        self._update_stat("failed")
                    self._update_stat("sent")
                    time.sleep(sleeptime)
                    driver.quit()
                except Exception as e:
                    logger.error(f"[mx] Play error: {e}")
                    self._update_stat("failed")
                    self._update_stat("sent")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                finally:
                    time.sleep(2)

        def _run():
            ts = [
                threading.Thread(target=_play_worker, args=(views_per_thread,), daemon=True)
                for _ in range(threads)
            ]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.running = False
            self.operation = ""
            logger.info("[mx] Play botter complete")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()


# ── Engine singleton ──────────────────────────────────────────────────────────
mx_engine = MixcloudEngine()


# ── Pydantic request models ───────────────────────────────────────────────────

class SetCaptchaKeyRequest(BaseModel):
    key: str

class SetProxyRequest(BaseModel):
    proxy: str

class SetCsrfTokensRequest(BaseModel):
    tokens: str  # newline-separated tokens

class GenerateRequest(BaseModel):
    count: int = 10
    threads: int = 3
    follow_random: bool = False

class FollowRequest(BaseModel):
    username: str
    count: int = 10
    follow_random: bool = False
    solve_captcha: bool = False

class MassFollowRequest(BaseModel):
    username: str
    threads: int = 3
    solve_captcha: bool = False

class LiveViewersRequest(BaseModel):
    url: str
    count: int = 5

class LiveChatRequest(BaseModel):
    url: str
    count: int = 3

class PlayRequest(BaseModel):
    url: str
    threads: int = 2
    views_per_thread: int = 5

class CheckBalanceRequest(BaseModel):
    key: str
    type: str = "2captcha"  # "2captcha" or "capmonster"

class ScrapeRequest(BaseModel):
    query: str
    pages: int = 3
    github_token: str


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/status")
def mx_status():
    return mx_engine.get_snapshot()


@router.post("/captcha/key")
def set_captcha_key(req: SetCaptchaKeyRequest):
    global _captcha_key
    _captcha_key = req.key.strip()
    try:
        with open(_CAPTCHA_KEY_FILE, "w") as f:
            f.write(_captcha_key)
    except Exception:
        pass
    try:
        from twocaptcha import TwoCaptcha
        balance = round(float(TwoCaptcha(_captcha_key).balance()), 5)
        return {"status": "ok", "balance": balance}
    except Exception as e:
        return {"status": "ok", "balance": 0.0, "error": str(e)}


@router.post("/csrf_tokens")
def set_csrf_tokens(req: SetCsrfTokensRequest):
    global _csrf_tokens
    tokens = [t.strip() for t in req.tokens.strip().splitlines() if t.strip()]
    _csrf_tokens = tokens
    try:
        with open(_CSRF_TOKENS_FILE, "w") as f:
            f.write("\n".join(tokens))
    except Exception:
        pass
    logger.info(f"[mx] Saved {len(tokens)} CSRF token(s)")
    return {"status": "ok", "count": len(tokens)}


@router.get("/csrf_tokens")
def get_csrf_tokens():
    return {"tokens": "\n".join(_csrf_tokens), "count": len(_csrf_tokens)}


@router.post("/proxy")
def set_proxy(req: SetProxyRequest):
    global _proxy_str
    _proxy_str = req.proxy.strip()
    try:
        with open(_PROXY_FILE, "w") as f:
            f.write(_proxy_str)
    except Exception:
        pass
    logger.info(f"[mx] Proxy saved and updated to: {_proxy_str[:40]}")
    return {"status": "ok"}


@router.get("/stats")
def get_mx_stats():
    return mx_engine.get_snapshot()


@router.post("/generate")
def start_generate(req: GenerateRequest):
    if mx_engine.running:
        return {"error": "Engine already running — stop it first"}
    mx_engine.start_generate(req.count, req.threads, req.follow_random)
    return {"status": "started"}


@router.post("/follow")
def start_follow(req: FollowRequest):
    if mx_engine.running:
        return {"error": "Engine already running — stop it first"}
    mx_engine.start_follow(req.username, req.count, req.follow_random, req.solve_captcha)
    return {"status": "started"}


@router.post("/mass_follow")
def start_mass_follow(req: MassFollowRequest):
    if mx_engine.running:
        return {"error": "Engine already running — stop it first"}
    mx_engine.start_mass_follow(req.username, req.threads, req.solve_captcha)
    return {"status": "started"}


@router.post("/live_viewers")
def start_live_viewers(req: LiveViewersRequest):
    if mx_engine.running:
        return {"error": "Engine already running — stop it first"}
    mx_engine.start_live_viewers(req.url, req.count)
    return {"status": "started"}


@router.post("/live_chat")
def start_live_chat(req: LiveChatRequest):
    if mx_engine.running:
        return {"error": "Engine already running — stop it first"}
    mx_engine.start_live_chat(req.url, req.count)
    return {"status": "started"}


@router.post("/play")
def start_play(req: PlayRequest):
    if mx_engine.running:
        return {"error": "Engine already running — stop it first"}
    mx_engine.start_play(req.url, req.threads, req.views_per_thread)
    return {"status": "started"}


@router.post("/stop")
def stop_mx():
    mx_engine.stop()
    return {"status": "stopping"}


# ── Captcha utilities (ported from 2cap/) ────────────────────────────────────

@router.post("/captcha/check_balance")
def check_captcha_balance(req: CheckBalanceRequest):
    """
    Ported from 2cap/checkkey.py (2captcha) and 2cap/capmonsterscrape.py (capmonster).
    """
    try:
        if req.type == "2captcha":
            # Identical to checkkey.py
            resp = requests.get(
                "https://2captcha.com/res.php",
                params={"key": req.key, "action": "getbalance", "json": 1},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != 1:
                return {"error": f"2Captcha error: {data.get('request')}"}
            return {"balance": round(float(data["request"]), 5), "key": req.key, "type": "2captcha"}

        elif req.type == "capmonster":
            # Identical to capmonsterscrape.py
            resp = requests.post(
                "https://api.capmonster.cloud/getBalance",
                json={"clientKey": req.key},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errorId") != 0:
                return {"error": f"CapMonster error {data.get('errorCode')}: {data.get('errorDescription')}"}
            return {"balance": data["balance"], "key": req.key, "type": "capmonster"}

        else:
            return {"error": "Unknown type. Use '2captcha' or 'capmonster'"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/captcha/scrape")
def scrape_captcha_keys(req: ScrapeRequest):
    """
    Ported from 2cap/2capscrape.py — searches GitHub for captcha API keys.
    """
    HEX_KEY_REGEX = re.compile(r"\b[a-f0-9]{24,32}\b", re.IGNORECASE)
    SEARCH_URL = "https://api.github.com/search/code"
    HEADERS = {
        "Authorization": f"Bearer {req.github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "requests"
    }
    found_keys = set()
    errors = []
    try:
        for page in range(1, req.pages + 1):
            logger.info(f"[mx] Scraping captcha keys — page {page}/{req.pages}")
            params = {"q": req.query, "per_page": 30, "page": page}
            try:
                r = requests.get(SEARCH_URL, headers=HEADERS, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                errors.append(f"Page {page}: {str(e)}")
                break

            for item in data.get("items", []):
                raw_url = (
                    item["html_url"]
                    .replace("https://github.com/", "https://raw.githubusercontent.com/")
                    .replace("/blob/", "/")
                )
                try:
                    fr = requests.get(raw_url, headers=HEADERS, timeout=15)
                    if fr.status_code == 200:
                        for m in HEX_KEY_REGEX.findall(fr.text):
                            found_keys.add(m)
                except Exception:
                    pass
                time.sleep(0.5)

    except Exception as e:
        errors.append(str(e))

    logger.info(f"[mx] Captcha scrape complete — {len(found_keys)} keys found")
    return {"keys": sorted(found_keys), "count": len(found_keys), "errors": errors}
