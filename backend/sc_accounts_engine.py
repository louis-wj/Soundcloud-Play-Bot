"""
SoundCloud Account Creator & Follower Engine
Browser-based (undetected-chromedriver) approach to bypass DataDome + reCAPTCHA Enterprise.
Uses smtp.dev for email generation/verification, 2Captcha for captcha solving.
"""
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

import os
import sys
import re
import json
import time
import random
import string
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from curl_cffi import requests as cffi_requests
from fastapi import APIRouter
from pydantic import BaseModel

from engine import _build_proxy_extension, _detect_chrome_version, _chrome_launch_lock

logger = logging.getLogger("sc_accounts")
router = APIRouter(prefix="/api/sc_accounts", tags=["sc_accounts"])

# When frozen by PyInstaller, __file__ is inside _internal/; config files live next to server.exe
if getattr(sys, 'frozen', False):
    _DIR = os.path.dirname(sys.executable)
else:
    _DIR = os.path.dirname(os.path.abspath(__file__))
_ACCOUNTS_FILE = os.path.join(_DIR, "sc_accounts.txt")
_SMTP_KEY_FILE = os.path.join(_DIR, "smtp_dev_key.txt")
_SC_CONFIG_FILE = os.path.join(_DIR, "sc_accounts_config.json")

_file_lock = threading.Lock()
_CHROME_VERSION = _detect_chrome_version()

# Auto-create all config files if they don't exist
for _f in [_ACCOUNTS_FILE, _SMTP_KEY_FILE]:
    if not os.path.exists(_f):
        try:
            open(_f, "w").close()
            logger.info(f"[sc] Created missing config file: {os.path.basename(_f)}")
        except Exception:
            pass
if not os.path.exists(_SC_CONFIG_FILE):
    try:
        with open(_SC_CONFIG_FILE, "w") as _cf:
            json.dump({"smtp_dev_key": "", "captcha_key": "", "proxy": "", "smtp_domain": ""}, _cf, indent=2)
        logger.info("[sc] Created missing config file: sc_accounts_config.json")
    except Exception:
        pass

# ── Persisted config ─────────────────────────────────────────────────────────
_config = {
    "smtp_dev_key": "",
    "captcha_key": "",  # 2captcha API key
    "proxy": "",
    "smtp_domain": "",  # e.g. "example.com" from smtp.dev
}

def _load_config():
    global _config
    if os.path.exists(_SC_CONFIG_FILE):
        try:
            with open(_SC_CONFIG_FILE, "r") as f:
                saved = json.load(f)
                _config.update(saved)
        except Exception:
            pass
    # Also try legacy file
    if not _config["smtp_dev_key"] and os.path.exists(_SMTP_KEY_FILE):
        try:
            _config["smtp_dev_key"] = open(_SMTP_KEY_FILE).read().strip()
        except Exception:
            pass

def _save_config():
    try:
        with open(_SC_CONFIG_FILE, "w") as f:
            json.dump(_config, f, indent=2)
    except Exception:
        pass

_load_config()


# ══════════════════════════════════════════════════════════════════════════════
#  smtp.dev helpers
# ══════════════════════════════════════════════════════════════════════════════

def _smtp_headers():
    return {
        "X-API-KEY": _config["smtp_dev_key"],
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def smtp_get_domain() -> str:
    """Get domain for email creation. Uses configured domain or fetches from smtp.dev."""
    # Use configured domain if set
    if _config.get("smtp_domain"):
        return _config["smtp_domain"]

    resp = cffi_requests.get(
        "https://api.smtp.dev/domains?isActive=true&page=1",
        headers=_smtp_headers(), timeout=15, impersonate="chrome124",
    )
    resp.raise_for_status()
    data = resp.json()
    # Handle both list and {member: [...]} response formats
    if isinstance(data, list):
        members = data
    elif isinstance(data, dict):
        members = data.get("member", data.get("results", []))
    else:
        members = []
    if not members:
        raise RuntimeError("No active domains on smtp.dev — add one in your dashboard or set domain in config")
    domain = members[0].get("domain", "") if isinstance(members[0], dict) else str(members[0])
    logger.info(f"[sc] Using smtp.dev domain: {domain}")
    return domain

def smtp_create_inbox(domain: str) -> dict:
    """Create a new email inbox on smtp.dev. Returns {id, address}."""
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    address = f"{username}@{domain}"
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    resp = cffi_requests.post(
        "https://api.smtp.dev/accounts",
        headers=_smtp_headers(),
        json={"address": address, "password": password},
        timeout=15, impersonate="chrome124",
    )
    resp.raise_for_status()
    data = resp.json()
    account_id = data.get("id") or data.get("@id", "").split("/")[-1]
    logger.info(f"[sc] Created inbox: {address} (id={account_id})")
    return {"id": account_id, "address": address, "password": password}

def smtp_poll_messages(account_id: str, timeout: int = 90, poll_interval: int = 5) -> list:
    """Poll smtp.dev for messages until at least one arrives or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = cffi_requests.get(
                f"https://api.smtp.dev/accounts/{account_id}/messages",
                headers=_smtp_headers(), timeout=15, impersonate="chrome124",
            )
            if resp.status_code == 200:
                data = resp.json()
                # Handle both list and {member: [...]} formats
                if isinstance(data, list):
                    messages = data
                elif isinstance(data, dict):
                    messages = data.get("member", data.get("results", []))
                else:
                    messages = []
                if messages:
                    return messages
        except Exception as e:
            logger.warning(f"[sc] smtp poll error: {e}")
        time.sleep(poll_interval)
    return []

def smtp_get_message_body(message_id: str) -> str:
    """Get the HTML body of a specific message."""
    resp = cffi_requests.get(
        f"https://api.smtp.dev/messages/{message_id}",
        headers=_smtp_headers(), timeout=15, impersonate="chrome124",
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("html", data.get("text", ""))

def smtp_extract_sc_verification_url(messages: list) -> str:
    """Extract the SoundCloud verification URL from email messages."""
    for msg in messages:
        msg_id = msg.get("id", "")
        if not msg_id:
            continue
        body = smtp_get_message_body(msg_id)
        # Look for SC verification links
        urls = re.findall(
            r'https?://(?:soundcloud\.com|snd\.sc|email\.soundcloud\.com)/[^\s"\'<>]+(?:confirm|verify|activate)[^\s"\'<>]*',
            body, re.IGNORECASE
        )
        if urls:
            return urls[0]
        # Broader: any soundcloud link in the email
        urls = re.findall(r'href="(https?://[^"]*soundcloud[^"]*)"', body, re.IGNORECASE)
        for url in urls:
            if any(kw in url.lower() for kw in ['confirm', 'verify', 'activate', 'token']):
                return url
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  2Captcha in-page solver
# ══════════════════════════════════════════════════════════════════════════════

def solve_recaptcha_for_page(driver, captcha_key: str, timeout: int = 180) -> str:
    """
    Solve reCAPTCHA visible on the current page using 2Captcha API,
    then inject the token into the page.
    """
    from twocaptcha import TwoCaptcha

    # Find the sitekey from the page
    sitekey = None
    try:
        # Method 1: data-sitekey attribute
        el = driver.find_element(By.CSS_SELECTOR, '[data-sitekey]')
        sitekey = el.get_attribute('data-sitekey')
    except Exception:
        pass

    if not sitekey:
        try:
            # Method 2: search page source
            source = driver.page_source
            match = re.search(r'["\']sitekey["\']\s*:\s*["\'](6L[\w-]+)["\']', source)
            if match:
                sitekey = match.group(1)
        except Exception:
            pass

    if not sitekey:
        try:
            # Method 3: grecaptcha render call
            source = driver.page_source
            match = re.search(r'grecaptcha(?:\.enterprise)?\.render\([^)]*["\']sitekey["\']\s*:\s*["\'](6L[\w-]+)', source)
            if match:
                sitekey = match.group(1)
        except Exception:
            pass

    if not sitekey:
        # Method 4: look in iframes
        try:
            iframes = driver.find_elements(By.TAG_NAME, 'iframe')
            for iframe in iframes:
                src = iframe.get_attribute('src') or ''
                match = re.search(r'[?&]k=(6L[\w-]+)', src)
                if match:
                    sitekey = match.group(1)
                    break
        except Exception:
            pass

    if not sitekey:
        raise RuntimeError("Could not find reCAPTCHA sitekey on page")

    logger.info(f"[sc] Found reCAPTCHA sitekey: {sitekey[:20]}...")
    page_url = driver.current_url

    # Detect if Enterprise
    is_enterprise = 'enterprise' in driver.page_source.lower()

    solver = TwoCaptcha(captcha_key)
    kwargs = {
        'sitekey': sitekey,
        'url': page_url,
    }
    if is_enterprise:
        kwargs['enterprise'] = 1
        logger.info("[sc] Detected reCAPTCHA Enterprise")

    logger.info("[sc] Sending reCAPTCHA to 2Captcha for solving...")
    result = solver.recaptcha(**kwargs)
    token = result['code']
    logger.info(f"[sc] Got reCAPTCHA token: {token[:40]}...")

    # Inject token into the page
    driver.execute_script(f"""
        // Set the response textarea
        var ta = document.getElementById('g-recaptcha-response');
        if (!ta) {{
            var tas = document.querySelectorAll('textarea[name="g-recaptcha-response"]');
            ta = tas[0];
        }}
        if (ta) {{
            ta.style.display = 'block';
            ta.innerHTML = '{token}';
            ta.value = '{token}';
        }}

        // Try enterprise callback
        if (typeof grecaptcha !== 'undefined') {{
            try {{
                if (grecaptcha.enterprise) {{
                    grecaptcha.enterprise.execute();
                }}
            }} catch(e) {{}}
        }}

        // Try to find and call the callback function
        try {{
            var frames = document.querySelectorAll('iframe[src*="recaptcha"]');
            for (var f of frames) {{
                var id = f.getAttribute('data-widget-id') || '0';
                if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {{
                    for (var clientId in ___grecaptcha_cfg.clients) {{
                        var client = ___grecaptcha_cfg.clients[clientId];
                        // Walk the client object to find callback
                        function findCallback(obj, depth) {{
                            if (depth > 5) return null;
                            if (typeof obj === 'function') return obj;
                            if (typeof obj !== 'object' || !obj) return null;
                            for (var key in obj) {{
                                if (key === 'callback' && typeof obj[key] === 'function') {{
                                    return obj[key];
                                }}
                                var found = findCallback(obj[key], depth + 1);
                                if (found) return found;
                            }}
                            return null;
                        }}
                        var cb = findCallback(client, 0);
                        if (cb) cb('{token}');
                    }}
                }}
            }}
        }} catch(e) {{ console.log('Callback search error:', e); }}
    """)
    time.sleep(2)
    return token


# ══════════════════════════════════════════════════════════════════════════════
#  Browser helpers
# ══════════════════════════════════════════════════════════════════════════════

def _make_uc_driver(proxy_str: str = None, headless: bool = False):
    """Create an undetected-chromedriver instance with optional proxy."""
    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1280,900')

    if headless:
        pass  # UC headless often fails with DataDome, keep visible

    if proxy_str and proxy_str.strip():
        from proxy_utils import parse_proxy
        p = parse_proxy(proxy_str)
        if p.get("user") and p.get("password"):
            ext_dir = _build_proxy_extension(p["host"], p["port"], p["user"], p["password"])
            options.add_argument(f'--load-extension={ext_dir}')
        else:
            options.add_argument(f'--proxy-server=http://{p["host"]}:{p["port"]}')

    with _chrome_launch_lock:
        driver = uc.Chrome(options=options, headless=False, version_main=_CHROME_VERSION)
    driver.set_page_load_timeout(60)
    return driver


def _extract_oauth_token(driver) -> str:
    """Extract OAuth token from SoundCloud browser session."""
    # Method 1: Check localStorage
    try:
        token = driver.execute_script("""
            try {
                // Check various localStorage keys
                var keys = ['oauth_token', 'V2::local::auth-token', 'V2::local::token'];
                for (var k of keys) {
                    var v = localStorage.getItem(k);
                    if (v) return v;
                }
                // Search all keys
                for (var i = 0; i < localStorage.length; i++) {
                    var key = localStorage.key(i);
                    var val = localStorage.getItem(key);
                    if (val && (key.toLowerCase().includes('token') || key.toLowerCase().includes('oauth'))) {
                        // Try parsing JSON
                        try {
                            var parsed = JSON.parse(val);
                            if (parsed.access_token) return parsed.access_token;
                            if (parsed.oauth_token) return parsed.oauth_token;
                        } catch(e) {}
                        if (val.length > 20 && val.length < 200 && !val.includes(' ')) return val;
                    }
                }
            } catch(e) {}
            return null;
        """)
        if token:
            return token
    except Exception:
        pass

    # Method 2: Check cookies
    try:
        cookies = driver.get_cookies()
        for c in cookies:
            if 'oauth' in c['name'].lower() or 'token' in c['name'].lower():
                if len(c['value']) > 20:
                    return c['value']
    except Exception:
        pass

    # Method 3: Intercept API calls (check performance logs)
    try:
        logs = driver.get_log('performance')
        for entry in logs:
            msg = json.loads(entry['message'])['message']
            if msg.get('method') == 'Network.requestWillBeSent':
                headers = msg.get('params', {}).get('request', {}).get('headers', {})
                auth = headers.get('Authorization', '')
                if auth.startswith('OAuth '):
                    return auth.replace('OAuth ', '')
        pass
    except Exception:
        pass

    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  Core worker: signup one account
# ══════════════════════════════════════════════════════════════════════════════

def _signup_one_account(proxy_str: str = None, stop_event: threading.Event = None) -> dict:
    """
    Full signup flow for one SoundCloud account.
    Returns {email, password, token, username} on success, or raises on failure.
    """
    if not _config["smtp_dev_key"]:
        raise RuntimeError("smtp.dev API key not configured")
    if not _config["captcha_key"]:
        raise RuntimeError("2Captcha API key not configured")

    driver = None
    try:
        # Step 1: Create email inbox
        logger.info("[sc] Creating email inbox via smtp.dev...")
        domain = smtp_get_domain()
        inbox = smtp_create_inbox(domain)
        email = inbox["address"]
        email_pass = inbox["password"]
        account_id = inbox["id"]
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + "A1!"
        logger.info(f"[sc] Email created: {email}")

        if stop_event and stop_event.is_set():
            return {}

        # Step 2: Launch browser and navigate to SoundCloud
        logger.info("[sc] Launching browser...")
        driver = _make_uc_driver(proxy_str)

        logger.info("[sc] Navigating to soundcloud.com...")
        try:
            driver.get("https://soundcloud.com/")
        except Exception:
            logger.warning("[sc] Page load timeout — continuing anyway")
        time.sleep(5 + random.uniform(0, 3))

        if stop_event and stop_event.is_set():
            return {}

        # Step 3: Click "Create account" to open the auth modal
        logger.info("[sc] Opening signup modal...")
        try:
            create_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH,
                    '//*[@id="content"]/div/div/div[1]/div/div[2]/div/button[2]'))
            )
            create_btn.click()
            logger.info("[sc] Clicked 'Create account'")
            time.sleep(4)
        except Exception:
            logger.info("[sc] Exact XPath failed — trying text match...")
            try:
                create_btn = driver.find_element(By.XPATH, '//button[contains(text(),"Create account")]')
                create_btn.click()
                time.sleep(4)
            except Exception:
                try:
                    signin_btn = driver.find_element(By.XPATH, '//button[contains(text(),"Sign in")]')
                    signin_btn.click()
                    time.sleep(4)
                except Exception:
                    logger.warning("[sc] No auth button found, auth iframe may already be present")

        if stop_event and stop_event.is_set():
            return {}

        # Step 4: Switch into the auth iframe (secure.soundcloud.com/web-auth)
        logger.info("[sc] Switching into auth iframe...")
        auth_iframe = None
        for attempt in range(10):
            iframes = driver.find_elements(By.TAG_NAME, 'iframe')
            for iframe in iframes:
                src = iframe.get_attribute('src') or ''
                if 'secure.soundcloud.com' in src or 'web-auth' in src:
                    auth_iframe = iframe
                    break
            if auth_iframe:
                break
            time.sleep(1)

        if not auth_iframe:
            raise RuntimeError("Could not find SoundCloud auth iframe")

        driver.switch_to.frame(auth_iframe)
        time.sleep(2)

        # Step 5: Enter email in the unified "Sign in or create account" form
        logger.info(f"[sc] Entering email: {email}")
        # Wait for form to fully render and become interactive
        time.sleep(4)

        for attempt in range(3):
            try:
                # Wait for the email field to be VISIBLE (not just present)
                email_field = WebDriverWait(driver, 15).until(
                    EC.visibility_of_element_located((By.ID, 'sign_in_up_email'))
                )
                # Click to focus
                try:
                    email_field.click()
                except Exception:
                    driver.execute_script("document.getElementById('sign_in_up_email').focus();")
                time.sleep(0.5)
                email_field.clear()
                time.sleep(0.3)

                # Try send_keys first
                try:
                    email_field.send_keys(email)
                    logger.info("[sc] Email entered via send_keys")
                except Exception:
                    # Fallback: use JavaScript to set the value
                    logger.info("[sc] send_keys failed, using JS to set email...")
                    driver.execute_script(f"""
                        var el = document.getElementById('sign_in_up_email');
                        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        nativeInputValueSetter.call(el, '{email}');
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    """)
                    logger.info("[sc] Email set via JS")
                time.sleep(1)
                break
            except Exception as e:
                err_str = str(e).lower()
                if ('stale' in err_str or 'not interactable' in err_str) and attempt < 2:
                    logger.warning(f"[sc] Email field issue, retrying ({attempt+1}): {str(e)[:60]}")
                    time.sleep(3)
                    # Re-find iframe
                    try:
                        driver.switch_to.default_content()
                        time.sleep(1)
                        for iframe in driver.find_elements(By.TAG_NAME, 'iframe'):
                            src = iframe.get_attribute('src') or ''
                            if 'secure.soundcloud.com' in src:
                                driver.switch_to.frame(iframe)
                                break
                    except Exception:
                        pass
                    time.sleep(2)
                else:
                    raise

        # Step 6: Click "Continue" — SC will detect this is a new email
        logger.info("[sc] Clicking Continue...")
        submit_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, 'sign_in_up_submit'))
        )
        submit_btn.click()
        time.sleep(5)

        if stop_event and stop_event.is_set():
            return {}

        # Step 7: SC should now show password field + DOB + possibly reCAPTCHA
        # The page changes — look for the new form fields
        logger.info("[sc] Waiting for signup form fields (password, DOB)...")

        # Screenshot to debug what we see
        driver.switch_to.default_content()
        driver.save_screenshot(os.path.join(_DIR, "sc_debug_after_continue.png"))
        driver.switch_to.frame(auth_iframe)

        # Wait for password field to become visible
        try:
            pass_field = WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR,
                    'input[type="password"]:not([tabindex="-1"]), '
                    'input[name="password"]:not([aria-hidden="true"])'
                ))
            )
            pass_field.clear()
            for char in password:
                pass_field.send_keys(char)
                time.sleep(random.uniform(0.02, 0.06))
            logger.info("[sc] Password entered")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"[sc] Password field not found: {e}")
            # Try the hidden field anyway
            try:
                pass_field = driver.find_element(By.ID, 'hidden_password_field')
                driver.execute_script("arguments[0].removeAttribute('tabindex'); arguments[0].removeAttribute('aria-hidden'); arguments[0].parentElement.classList.remove('form-element-wrapper--hidden');", pass_field)
                time.sleep(0.5)
                pass_field.send_keys(password)
                logger.info("[sc] Password entered (unhidden)")
            except Exception:
                logger.error("[sc] Could not enter password at all")

        # Fill date of birth fields (if visible)
        logger.info("[sc] Looking for DOB fields...")
        for sel_name, values in [
            ('select[name*="month" i]', [str(i) for i in range(1, 13)]),
            ('select[name*="day" i]', [str(i) for i in range(1, 29)]),
            ('select[name*="year" i]', [str(i) for i in range(1990, 2003)]),
        ]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel_name)
                Select(el).select_by_value(random.choice(values))
                time.sleep(0.3)
                logger.info(f"[sc] Set {sel_name}")
            except Exception:
                pass
        # Also try input fields for DOB
        for sel, val in [
            ('input[name*="month" i]', str(random.randint(1, 12))),
            ('input[name*="day" i]', str(random.randint(1, 28))),
            ('input[name*="year" i]', str(random.randint(1990, 2002))),
        ]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                el.clear()
                el.send_keys(val)
                time.sleep(0.3)
            except Exception:
                pass

        if stop_event and stop_event.is_set():
            return {}

        # Step 8: Solve reCAPTCHA if present
        logger.info("[sc] Checking for reCAPTCHA...")
        try:
            solve_recaptcha_for_page(driver, _config["captcha_key"])
        except Exception as e:
            logger.info(f"[sc] reCAPTCHA not found or solve failed: {e} — may not be required")

        if stop_event and stop_event.is_set():
            return {}

        # Step 9: Submit the signup form
        logger.info("[sc] Submitting signup...")
        try:
            # Look for any submit/continue button
            for sel in [
                '#sign_in_up_submit',
                'button[type="submit"]',
                'button.submit-button',
                '//button[contains(text(),"Continue")]',
                '//button[contains(text(),"Create")]',
                '//button[contains(text(),"Sign up")]',
            ]:
                try:
                    if sel.startswith('//'):
                        btn = driver.find_element(By.XPATH, sel)
                    else:
                        btn = driver.find_element(By.CSS_SELECTOR, sel)
                    if btn.is_enabled():
                        btn.click()
                        logger.info(f"[sc] Clicked submit: {sel}")
                        break
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(8)

        # Screenshot after submit
        driver.switch_to.default_content()
        driver.save_screenshot(os.path.join(_DIR, "sc_debug_after_submit.png"))

        # Step 10: Handle "Tell us more about you" profile page
        # This page has: Display name, DOB (Month/Day/Year selects), Gender select, Continue button
        logger.info("[sc] Handling profile completion page...")

        # Re-find and switch into iframe
        def _switch_to_auth_iframe():
            try:
                driver.switch_to.default_content()
                for iframe in driver.find_elements(By.TAG_NAME, 'iframe'):
                    src = iframe.get_attribute('src') or ''
                    if 'secure.soundcloud.com' in src:
                        driver.switch_to.frame(iframe)
                        return True
            except Exception:
                pass
            return False

        _switch_to_auth_iframe()
        time.sleep(2)

        # Fill Display Name if present
        try:
            name_field = driver.find_element(By.CSS_SELECTOR, 'input[name*="display" i], input[name*="name" i], input[placeholder*="display" i]')
            name_field.clear()
            display_name = ''.join(random.choices(string.ascii_letters, k=8)).capitalize()
            name_field.send_keys(display_name)
            logger.info(f"[sc] Set display name: {display_name}")
            time.sleep(0.5)
        except Exception:
            display_name = ""
            logger.debug("[sc] No display name field found")

        # Fill DOB selects (Month, Day, Year)
        # These are <select> elements on the "Tell us more" page
        dob_month = str(random.randint(1, 12))
        dob_day = str(random.randint(1, 28))
        dob_year = str(random.randint(1990, 2002))

        selects = driver.find_elements(By.TAG_NAME, 'select')
        for sel_el in selects:
            try:
                sel = Select(sel_el)
                options = [o.get_attribute('value') for o in sel.options if o.get_attribute('value')]
                label_text = (sel_el.get_attribute('aria-label') or sel_el.get_attribute('name') or '').lower()
                # Also check adjacent label
                try:
                    parent_text = sel_el.find_element(By.XPATH, '..').text.lower()
                except Exception:
                    parent_text = ''

                all_text = label_text + parent_text
                if 'month' in all_text or (options and options[0] in ['1', '01', 'January']):
                    sel.select_by_index(random.randint(1, min(12, len(sel.options) - 1)))
                    logger.info(f"[sc] Set month")
                elif 'day' in all_text or (len(options) > 20 and options[0] in ['1', '01']):
                    sel.select_by_index(random.randint(1, min(28, len(sel.options) - 1)))
                    logger.info(f"[sc] Set day")
                elif 'year' in all_text or (options and len(options[0]) == 4):
                    # Pick a year that makes the user 18+
                    valid_indices = [i for i, o in enumerate(sel.options) if o.get_attribute('value') and len(o.get_attribute('value')) == 4 and int(o.get_attribute('value')) <= 2006]
                    if valid_indices:
                        sel.select_by_index(random.choice(valid_indices))
                    else:
                        sel.select_by_index(random.randint(1, len(sel.options) - 1))
                    logger.info(f"[sc] Set year")
                elif 'gender' in all_text:
                    # Pick a random gender option (skip the placeholder)
                    sel.select_by_index(random.randint(1, len(sel.options) - 1))
                    logger.info(f"[sc] Set gender")
                time.sleep(0.3)
            except Exception as e:
                logger.debug(f"[sc] Select fill error: {e}")

        # Click Continue on the profile page
        time.sleep(1)
        for sel in ['button[type="submit"]', 'button.submit-button', '#sign_in_up_submit']:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_enabled():
                    btn.click()
                    logger.info(f"[sc] Clicked Continue on profile page")
                    break
            except Exception:
                continue
        time.sleep(5)

        # Handle any subsequent pages (genre selection, skip, etc.)
        logger.info("[sc] Handling any remaining steps...")
        for _ in range(8):
            _switch_to_auth_iframe()
            clicked = False
            for xpath in [
                '//button[contains(text(),"Continue")]',
                '//button[contains(text(),"Next")]',
                '//button[contains(text(),"Skip")]',
                '//button[contains(text(),"Get started")]',
                '//button[contains(text(),"Done")]',
            ]:
                try:
                    btn = driver.find_element(By.XPATH, xpath)
                    if btn.is_displayed() and btn.is_enabled():
                        btn.click()
                        logger.info(f"[sc] Clicked: {btn.text}")
                        clicked = True
                        time.sleep(3)
                        break
                except Exception:
                    continue

            if not clicked:
                try:
                    driver.switch_to.default_content()
                    for xpath in ['//button[contains(text(),"Continue")]', '//button[contains(text(),"Skip")]', '//button[contains(text(),"Get started")]']:
                        try:
                            btn = driver.find_element(By.XPATH, xpath)
                            if btn.is_displayed():
                                btn.click()
                                clicked = True
                                time.sleep(2)
                                break
                        except Exception:
                            continue
                except Exception:
                    pass
                if not clicked:
                    break

        if stop_event and stop_event.is_set():
            return {}

        # Step 6: Poll for verification email
        logger.info("[sc] Waiting for verification email...")
        messages = smtp_poll_messages(account_id, timeout=90)
        if not messages:
            logger.warning("[sc] No verification email received — account may still work without verification")
        else:
            # Step 7: Extract and visit confirmation URL
            verify_url = smtp_extract_sc_verification_url(messages)
            if verify_url:
                logger.info(f"[sc] Visiting verification URL: {verify_url[:80]}...")
                driver.get(verify_url)
                time.sleep(5)
                logger.info("[sc] Email verified!")
            else:
                logger.warning("[sc] Could not extract verification URL from email")

        if stop_event and stop_event.is_set():
            return {}

        # Step 8: Extract OAuth token
        logger.info("[sc] Extracting OAuth token...")
        # Navigate to SoundCloud to ensure we're logged in
        driver.get("https://soundcloud.com/")
        time.sleep(3)

        token = _extract_oauth_token(driver)
        if token:
            logger.info(f"[sc] Got OAuth token: {token[:20]}...")
        else:
            logger.warning("[sc] Could not extract OAuth token — will use browser fallback for follows")

        # Try to get username
        username = ""
        try:
            username = driver.execute_script("""
                try {
                    var el = document.querySelector('a[href*="/you"], a[class*="header"][href^="/"]');
                    if (el) return el.getAttribute('href').replace('/', '');
                } catch(e) {}
                return '';
            """) or ""
        except Exception:
            pass

        result = {
            "email": email,
            "password": password,
            "token": token,
            "username": username,
        }

        # Step 9: Save account
        with _file_lock:
            with open(_ACCOUNTS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{email}:{password}:{username}:{token}\n")

        logger.info(f"[sc] Account created: {email} ({username})")
        return result

    except Exception as e:
        logger.error(f"[sc] Signup failed: {e}")
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  Follow
# ══════════════════════════════════════════════════════════════════════════════

_FALLBACK_CLIENT_ID = 'GXG1PaJ1dcHGVX1lHIIbldZN7ZiUBJP7'

def _resolve_user_id(username_or_url: str) -> int:
    """Resolve a SoundCloud username or URL to a numeric user ID."""
    url = username_or_url.strip()
    if not url.startswith('http'):
        url = f"https://soundcloud.com/{url}"
    resp = cffi_requests.get(
        f"https://api-v2.soundcloud.com/resolve?url={url}&client_id={_FALLBACK_CLIENT_ID}",
        timeout=15, impersonate="chrome124",
    )
    if resp.status_code == 200:
        return resp.json().get("id")
    raise RuntimeError(f"Could not resolve user: {username_or_url} (HTTP {resp.status_code})")


def _follow_via_api(user_id: int, token: str) -> bool:
    """Follow a user via SoundCloud API using OAuth token."""
    resp = cffi_requests.put(
        f"https://api-v2.soundcloud.com/me/followings/{user_id}",
        headers={"Authorization": f"OAuth {token}"},
        timeout=15, impersonate="chrome124",
    )
    if resp.status_code in (200, 201):
        return True
    logger.warning(f"[sc] API follow failed: HTTP {resp.status_code} — {resp.text[:100]}")
    return False


def _follow_via_browser(target_url: str, email: str, password: str, proxy_str: str = None) -> bool:
    """Fallback: follow a user via browser automation."""
    driver = None
    try:
        driver = _make_uc_driver(proxy_str)
        driver.get("https://soundcloud.com/signin")
        time.sleep(3)

        # Login
        try:
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    'input[type="email"], input[name="email"], input[name="username"]'))
            )
            email_field.send_keys(email)
            time.sleep(0.5)
        except Exception:
            raise RuntimeError("Could not find login email field")

        try:
            pass_field = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            pass_field.send_keys(password)
            time.sleep(0.5)
            pass_field.send_keys(Keys.RETURN)
        except Exception:
            raise RuntimeError("Could not find login password field")

        time.sleep(5)

        # Navigate to target profile
        if not target_url.startswith('http'):
            target_url = f"https://soundcloud.com/{target_url}"
        driver.get(target_url)
        time.sleep(3)

        # Click Follow button
        try:
            follow_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH,
                    '//button[contains(@class,"follow") and not(contains(@class,"following"))] | '
                    '//button[contains(text(),"Follow") and not(contains(text(),"Following"))]'))
            )
            follow_btn.click()
            time.sleep(2)
            logger.info(f"[sc] Browser follow successful for {target_url}")
            return True
        except Exception as e:
            logger.warning(f"[sc] Could not click Follow button: {e}")
            return False
    except Exception as e:
        logger.error(f"[sc] Browser follow error: {e}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  Engine class
# ══════════════════════════════════════════════════════════════════════════════

class SoundCloudAccountsEngine:
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
        with self._lock:
            s = dict(self.stats)
        elapsed = time.time() - s["start_time"] if s["start_time"] else 0
        s["elapsed"] = round(elapsed, 1)
        s["running"] = self.running
        s["operation"] = self.operation
        s["accounts"] = _count_accounts()
        s["config"] = {
            "smtp_dev_key": bool(_config["smtp_dev_key"]),
            "captcha_key": bool(_config["captcha_key"]),
            "proxy": _config["proxy"][:40] if _config["proxy"] else "",
        }
        return s

    def stop(self):
        self._stop_event.set()
        self.running = False
        self.operation = ""
        logger.info("[sc] Engine stopped")

    # ── Generate Accounts ────────────────────────────────────────────────

    def start_generate(self, count: int, threads: int):
        if self.running:
            return
        self.running = True
        self.operation = "generate"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": count, "start_time": time.time()}

        def _worker():
            if self._stop_event.is_set():
                return
            try:
                _signup_one_account(proxy_str=_config["proxy"], stop_event=self._stop_event)
                self._update_stat("success")
            except Exception as e:
                logger.error(f"[sc] Generate worker error: {e}")
                self._update_stat("failed")
            self._update_stat("sent")

        def _run():
            # Limit concurrency — each worker is a Chrome instance
            max_workers = min(threads, 3)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futs = []
                for i in range(count):
                    if self._stop_event.is_set():
                        break
                    futs.append(pool.submit(_worker))
                    time.sleep(random.uniform(2, 5))  # Stagger launches
                for ft in futs:
                    if self._stop_event.is_set():
                        break
                    ft.result()
            self.running = False
            self.operation = ""
            logger.info(f"[sc] Generate complete — {self.stats['success']}/{count}")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── Follow ────────────────────────────────────────────────────────────

    def start_follow(self, target: str, count: int):
        if self.running:
            return
        self.running = True
        self.operation = "follow"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": count, "start_time": time.time()}

        def _run():
            try:
                user_id = _resolve_user_id(target)
                logger.info(f"[sc] Resolved {target} → user_id {user_id}")
            except Exception as e:
                logger.error(f"[sc] Cannot resolve user: {e}")
                self.running = False
                self.operation = ""
                return

            with _file_lock:
                accounts = [l.strip() for l in open(_ACCOUNTS_FILE, "r", encoding="utf-8") if l.strip()]

            limit = min(count, len(accounts))
            for i, line in enumerate(accounts[:limit]):
                if self._stop_event.is_set():
                    break
                parts = line.split(":")
                email = parts[0]
                password = parts[1] if len(parts) > 1 else ""
                token = parts[3] if len(parts) > 3 else ""

                # Try API first
                if token:
                    if _follow_via_api(user_id, token):
                        logger.info(f"[sc] Follow {i+1}/{limit} sent (API) from {email}")
                        self._update_stat("success")
                        self._update_stat("sent")
                        continue

                # Fallback to browser
                logger.info(f"[sc] API failed, trying browser follow for {email}...")
                if _follow_via_browser(target, email, password, _config["proxy"]):
                    self._update_stat("success")
                else:
                    self._update_stat("failed")
                self._update_stat("sent")
                time.sleep(random.uniform(1, 3))

            self.running = False
            self.operation = ""
            logger.info(f"[sc] Follow complete — {self.stats['success']}/{limit}")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    # ── Mass Follow ───────────────────────────────────────────────────────

    def start_mass_follow(self, target: str, threads: int):
        if self.running:
            return
        self.running = True
        self.operation = "mass_follow"
        self._stop_event.clear()
        with self._lock:
            self.stats = {"sent": 0, "success": 0, "failed": 0, "target": 0, "start_time": time.time()}

        def _worker(user_id):
            while not self._stop_event.is_set():
                try:
                    with _file_lock:
                        accounts = [l.strip() for l in open(_ACCOUNTS_FILE, "r", encoding="utf-8") if l.strip()]
                    if not accounts:
                        time.sleep(5)
                        continue
                    line = random.choice(accounts)
                    parts = line.split(":")
                    email = parts[0]
                    password = parts[1] if len(parts) > 1 else ""
                    token = parts[3] if len(parts) > 3 else ""

                    success = False
                    if token:
                        success = _follow_via_api(user_id, token)
                    if not success:
                        success = _follow_via_browser(target, email, password, _config["proxy"])

                    if success:
                        self._update_stat("success")
                    else:
                        self._update_stat("failed")
                    self._update_stat("sent")
                    time.sleep(random.uniform(2, 5))
                except Exception as e:
                    logger.warning(f"[sc] Mass follow error: {e}")
                    time.sleep(3)

        def _run():
            try:
                user_id = _resolve_user_id(target)
                logger.info(f"[sc] Resolved {target} → user_id {user_id}")
            except Exception as e:
                logger.error(f"[sc] Cannot resolve user: {e}")
                self.running = False
                self.operation = ""
                return

            max_workers = min(threads, 3)
            ts = [threading.Thread(target=_worker, args=(user_id,), daemon=True)
                  for _ in range(max_workers)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.running = False
            self.operation = ""

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()


def _count_accounts() -> int:
    try:
        with _file_lock:
            return len([l for l in open(_ACCOUNTS_FILE, "r", encoding="utf-8") if l.strip()])
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Engine singleton
# ══════════════════════════════════════════════════════════════════════════════

sc_accounts_engine = SoundCloudAccountsEngine()


# ══════════════════════════════════════════════════════════════════════════════
#  Pydantic models
# ══════════════════════════════════════════════════════════════════════════════

class SCConfigRequest(BaseModel):
    smtp_dev_key: str = ""
    captcha_key: str = ""
    proxy: str = ""
    smtp_domain: str = ""

class SCGenerateRequest(BaseModel):
    count: int = 5
    threads: int = 2

class SCFollowRequest(BaseModel):
    target: str
    count: int = 10

class SCMassFollowRequest(BaseModel):
    target: str
    threads: int = 2


# ══════════════════════════════════════════════════════════════════════════════
#  REST endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/status")
def sc_status():
    return sc_accounts_engine.get_snapshot()

@router.post("/config")
def sc_set_config(req: SCConfigRequest):
    if req.smtp_dev_key:
        _config["smtp_dev_key"] = req.smtp_dev_key.strip()
    if req.captcha_key:
        _config["captcha_key"] = req.captcha_key.strip()
    if req.proxy is not None:
        _config["proxy"] = req.proxy.strip()
    if req.smtp_domain:
        _config["smtp_domain"] = req.smtp_domain.strip()
    _save_config()
    return {"status": "ok", "config": {k: bool(v) if k != "proxy" else v[:40] for k, v in _config.items()}}

@router.post("/generate")
def sc_generate(req: SCGenerateRequest):
    if sc_accounts_engine.running:
        return {"error": "Engine already running — stop it first"}
    sc_accounts_engine.start_generate(req.count, req.threads)
    return {"status": "started"}

@router.post("/follow")
def sc_follow(req: SCFollowRequest):
    if sc_accounts_engine.running:
        return {"error": "Engine already running — stop it first"}
    sc_accounts_engine.start_follow(req.target, req.count)
    return {"status": "started"}

@router.post("/mass_follow")
def sc_mass_follow(req: SCMassFollowRequest):
    if sc_accounts_engine.running:
        return {"error": "Engine already running — stop it first"}
    sc_accounts_engine.start_mass_follow(req.target, req.threads)
    return {"status": "started"}

@router.post("/stop")
def sc_stop():
    sc_accounts_engine.stop()
    return {"status": "stopping"}
