"""
Hybrid approach: UC browser visits SoundCloud to get valid DataDome cookie,
then use fast POST request for actual signup + smtp.dev email verification.
"""
import sys, os, time, json, random, string, hashlib, uuid, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from curl_cffi import requests as cffi_requests
from engine import _detect_chrome_version, _chrome_launch_lock

# ── Config ────────────────────────────────────────────────────────────────────
CLIENT_ID = 'tkIWLs4MIowq7bCXP80TOwx6DnDa7UPc'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'

config_path = os.path.join(os.path.dirname(__file__), 'sc_accounts_config.json')
with open(config_path) as f:
    cfg = json.load(f)

CAPTCHA_KEY = cfg['captcha_key']
SMTP_KEY = cfg['smtp_dev_key']
SMTP_DOMAIN = cfg['smtp_domain']
PROXY = cfg.get('proxy', '')

print(f"Proxy: {PROXY or 'none'}")
smtp_headers = {'X-API-KEY': SMTP_KEY, 'Accept': 'application/json', 'Content-Type': 'application/json'}

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Create smtp.dev inbox
# ══════════════════════════════════════════════════════════════════════════════
uname = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
email = f"{uname}@{SMTP_DOMAIN}"
sc_password = 'Sc' + ''.join(random.choices(string.ascii_letters + string.digits, k=10)) + '1!'
sc_username = uname.capitalize() + str(random.randint(10, 99))

print(f"\n{'='*60}")
print(f"STEP 1: Create inbox: {email}")

resp = cffi_requests.post('https://api.smtp.dev/accounts', headers=smtp_headers,
    json={'address': email, 'password': 'SmtpPass1!'}, timeout=15, impersonate='chrome124')
inbox = resp.json()
account_id = inbox.get('id', '')
print(f"  Inbox ID: {account_id}")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Launch UC browser to get valid DataDome cookie
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"STEP 2: Getting DataDome cookie via UC browser...")

chrome_ver = _detect_chrome_version()
options = uc.ChromeOptions()
options.add_argument('--no-sandbox')
options.add_argument('--disable-gpu')
options.add_argument('--window-size=1280,900')

# Use proxy in browser too so DataDome cookie is tied to proxy IP
if PROXY:
    from proxy_utils import parse_proxy
    from engine import _build_proxy_extension
    p = parse_proxy(PROXY.replace('http://', '').replace('https://', ''))
    if p.get('user') and p.get('password'):
        ext_dir = _build_proxy_extension(p['host'], p['port'], p['user'], p['password'])
        options.add_argument(f'--load-extension={ext_dir}')
        print(f"  Browser proxy: {p['host']}:{p['port']}")
    else:
        options.add_argument(f'--proxy-server=http://{p["host"]}:{p["port"]}')

with _chrome_launch_lock:
    driver = uc.Chrome(options=options, headless=False, version_main=chrome_ver)
driver.set_page_load_timeout(120)

try:
    try:
        driver.get("https://soundcloud.com/")
    except Exception:
        print("  Page load timeout — continuing")
    time.sleep(8)
    print(f"  Title: {driver.title}")

    # Extract ALL cookies from browser
    browser_cookies = driver.get_cookies()
    dd_cookie = ''
    all_sc_cookies = {}
    for c in browser_cookies:
        all_sc_cookies[c['name']] = c['value']
        if c['name'] == 'datadome':
            dd_cookie = c['value']

    print(f"  Browser cookies: {list(all_sc_cookies.keys())}")
    print(f"  datadome: {dd_cookie[:50]}..." if dd_cookie else "  NO datadome cookie!")

    if not dd_cookie:
        # Try navigating to the auth page to trigger DataDome
        print("  Trying secure.soundcloud.com to trigger DataDome...")
        try:
            driver.get(f"https://secure.soundcloud.com/web-auth?client_id={CLIENT_ID}")
        except Exception:
            pass
        time.sleep(5)
        browser_cookies = driver.get_cookies()
        for c in browser_cookies:
            all_sc_cookies[c['name']] = c['value']
            if c['name'] == 'datadome':
                dd_cookie = c['value']
        print(f"  datadome after auth page: {dd_cookie[:50]}..." if dd_cookie else "  Still no datadome!")

finally:
    driver.quit()

if not dd_cookie:
    print("\nFAILED: Could not obtain DataDome cookie from browser")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: POST signup with real DataDome cookie
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"STEP 3: POST signup with browser DataDome cookie")

anon_id = all_sc_cookies.get('sc_anonymous_id', '-'.join([str(random.randint(100000,999999)) for _ in range(4)]))
tracking_id = all_sc_cookies.get('sc_tracking_anonymous_id', str(uuid.uuid4())).strip('%22').strip('"')
csrf_token = hashlib.sha256(os.urandom(32)).hexdigest()

s = cffi_requests.Session(impersonate='chrome124')
if PROXY:
    s.proxies = {'http': PROXY, 'https': PROXY}

# Set all browser cookies on the session
s.cookies.set('datadome', dd_cookie, domain='.soundcloud.com')
s.cookies.set('sc_anonymous_id', anon_id, domain='.soundcloud.com')
s.cookies.set('sc_theme', 'dark', domain='.soundcloud.com')
s.cookies.set('sc_tracking_anonymous_id', f'%22{tracking_id}%22', domain='.soundcloud.com')

signup_payload = {
    'credentials': {'kind': 'password', 'body': {'identifier': email, 'password': sc_password}},
    'user_info': {
        'birthday': f'{random.randint(1990,2004)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}',
        'username': sc_username,
        'gender': random.choice(['male', 'female', 'custom']),
        'consent_emails': False,
        'consent_recommendations': True
    },
    'vk': {'id': email, 'kd': 'password', 'dd': anon_id, 'ag': UA, 'cd': CLIENT_ID}
}

signup_headers = {
    'user-agent': UA,
    'origin': 'https://secure.soundcloud.com',
    'referer': 'https://secure.soundcloud.com/',
    'x-anonymous-id': anon_id,
    'x-csrf-token': csrf_token,
    'x-tracking-anonymous-id': tracking_id,
    'accept': 'application/json',
    'content-type': 'application/json',
}

SIGNUP_URL = f'https://api-auth.soundcloud.com/sign-up?client_id={CLIENT_ID}'

resp = s.post(SIGNUP_URL, json=signup_payload, headers=signup_headers, timeout=30)
print(f"  Status: {resp.status_code}")
print(f"  Body: {resp.text[:400]}")

# If still 403, check t parameter
if resp.status_code == 403:
    try:
        import urllib.parse
        data = resp.json()
        cap_url = data.get('url', '')
        params = urllib.parse.parse_qs(urllib.parse.urlparse(cap_url).query)
        t_val = params.get('t', ['?'])[0]
        print(f"  t={t_val}")

        if t_val == 'fe':
            # Solvable! Use 2Captcha
            import requests as std_requests
            print(f"  t=fe — solvable! Sending to 2Captcha...")
            sub_data = {
                'key': CAPTCHA_KEY, 'method': 'datadome',
                'captcha_url': cap_url, 'pageurl': SIGNUP_URL,
                'userAgent': UA, 'json': 1,
            }
            sub_resp = std_requests.post('https://2captcha.com/in.php', data=sub_data, timeout=30)
            sub_result = sub_resp.json()
            print(f"  Submit: {sub_result}")

            if sub_result.get('status') == 1:
                task_id = sub_result['request']
                for poll in range(60):
                    time.sleep(5)
                    res = std_requests.get(f'https://2captcha.com/res.php?key={CAPTCHA_KEY}&action=get&json=1&id={task_id}', timeout=15).json()
                    if res.get('request') == 'CAPCHA_NOT_READY':
                        print(f"  Solving... ({(poll+1)*5}s)")
                        continue
                    elif 'ERROR' in str(res.get('request', '')):
                        print(f"  Error: {res['request']}")
                        break
                    else:
                        solved = res['request']
                        if 'datadome=' in solved:
                            solved = solved.split('datadome=')[1].split(';')[0]
                        print(f"  Solved cookie: {solved[:60]}...")
                        s.cookies.set('datadome', solved, domain='.soundcloud.com')
                        resp = s.post(SIGNUP_URL, json=signup_payload, headers=signup_headers, timeout=30)
                        print(f"  Retry status: {resp.status_code}")
                        print(f"  Retry body: {resp.text[:400]}")
                        break
    except Exception as e:
        print(f"  Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4+: Handle success
# ══════════════════════════════════════════════════════════════════════════════
if resp.status_code == 202:
    result_data = resp.json()
    intermediate_token = result_data.get('intermediate_token', '')
    print(f"\n{'='*60}")
    print(f"SIGNUP SUCCESSFUL!")
    print(f"  Required: {result_data.get('required')}")
    print(f"  Token: {intermediate_token[:50]}...")

    # Poll smtp.dev for verification email
    print(f"\nWaiting for verification email...")
    verify_url = ''
    for attempt in range(18):
        time.sleep(5)
        try:
            msg_resp = cffi_requests.get(f'https://api.smtp.dev/accounts/{account_id}/messages',
                headers=smtp_headers, timeout=15, impersonate='chrome124')
            if msg_resp.status_code == 200:
                data = msg_resp.json()
                messages = data if isinstance(data, list) else data.get('member', [])
                if messages:
                    msg_id = messages[0].get('id', '')
                    if msg_id:
                        body_resp = cffi_requests.get(f'https://api.smtp.dev/messages/{msg_id}',
                            headers=smtp_headers, timeout=15, impersonate='chrome124')
                        body = body_resp.json().get('html', body_resp.json().get('text', ''))
                        urls = re.findall(r'href="(https?://[^"]*soundcloud[^"]*)"', body)
                        for u in urls:
                            if any(kw in u.lower() for kw in ['confirm', 'verify', 'activate', 'email']):
                                verify_url = u
                                break
                        if not verify_url and urls:
                            verify_url = urls[0]
                        print(f"  Got email! Verify URL: {verify_url[:80]}...")
                    break
        except Exception as e:
            pass
        print(f"  Waiting... ({(attempt+1)*5}s)")

    if verify_url:
        print(f"\nClicking verification link...")
        cffi_requests.get(verify_url, timeout=20, impersonate='chrome124')
        time.sleep(3)

        print(f"POST email-verified...")
        ev_resp = s.post(f'https://api-auth.soundcloud.com/email-verified?client_id={CLIENT_ID}',
            json={'intermediate_token': intermediate_token}, headers=signup_headers, timeout=20)
        print(f"  Status: {ev_resp.status_code}")
        print(f"  Body: {ev_resp.text[:300]}")

    print(f"\n{'='*60}")
    print(f"ACCOUNT: {email} / {sc_password} / {sc_username}")
else:
    print(f"\nFAILED: {resp.status_code}")
