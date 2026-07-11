"""Full POST-based SoundCloud signup test with DataDome + smtp.dev email verification."""
import sys, os, time, json, random, string, hashlib, uuid, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from curl_cffi import requests as cffi_requests
from twocaptcha import TwoCaptcha

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

print(f"SMTP domain: {SMTP_DOMAIN}")
print(f"Captcha key: {CAPTCHA_KEY[:10]}...")
print(f"Proxy: {PROXY or 'none'}")

# ── Helpers ───────────────────────────────────────────────────────────────────
smtp_headers = {'X-API-KEY': SMTP_KEY, 'Accept': 'application/json', 'Content-Type': 'application/json'}

def gen_anon_id():
    return '-'.join([str(random.randint(100000, 999999)) for _ in range(4)])

def gen_tracking_id():
    return str(uuid.uuid4())

def gen_csrf():
    return hashlib.sha256(os.urandom(32)).hexdigest()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Create smtp.dev inbox
# ══════════════════════════════════════════════════════════════════════════════
uname = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
email = f"{uname}@{SMTP_DOMAIN}"
sc_password = 'Sc' + ''.join(random.choices(string.ascii_letters + string.digits, k=10)) + '1!'
sc_username = uname.capitalize() + str(random.randint(10, 99))

print(f"\n{'='*60}")
print(f"STEP 1: Create inbox")
print(f"  Email: {email}")
print(f"  Password: {sc_password}")
print(f"  Username: {sc_username}")

resp = cffi_requests.post('https://api.smtp.dev/accounts', headers=smtp_headers,
    json={'address': email, 'password': 'SmtpPass1!'}, timeout=15, impersonate='chrome124')
inbox = resp.json()
account_id = inbox.get('id', '')
print(f"  Inbox ID: {account_id}")
assert account_id, f"Failed to create inbox: {resp.text}"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: POST signup (expect 403 DataDome challenge)
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"STEP 2: POST signup")

anon_id = gen_anon_id()
tracking_id = gen_tracking_id()
csrf_token = gen_csrf()

s = cffi_requests.Session(impersonate='chrome124')
if PROXY:
    s.proxies = {'http': PROXY, 'https': PROXY}

# Set SC cookies
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
print(f"  Body: {resp.text[:300]}")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Solve DataDome if 403
# ══════════════════════════════════════════════════════════════════════════════
if resp.status_code == 403:
    data = resp.json()
    captcha_url = data.get('url', '')
    if not captcha_url:
        print("  ERROR: 403 but no captcha URL!")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"STEP 3: Solve DataDome CAPTCHA")
    print(f"  Captcha URL: {captcha_url[:100]}...")

    # Extract datadome cookie from set-cookie header
    dd_cookie = ''
    for val in resp.headers.get_list('set-cookie') if hasattr(resp.headers, 'get_list') else [resp.headers.get('set-cookie', '')]:
        if 'datadome' in val.lower():
            dd_cookie = val.split('datadome=')[1].split(';')[0]
            break
    # Also check cookies jar
    if not dd_cookie:
        dd_cookie = s.cookies.get('datadome', '')
    print(f"  DataDome cookie: {dd_cookie[:50]}...")

    # Check t= parameter — t=bv means IP banned, t=fe means solvable
    import urllib.parse
    parsed_url = urllib.parse.urlparse(captcha_url)
    params = urllib.parse.parse_qs(parsed_url.query)
    t_val = params.get('t', [''])[0]
    print(f"  t parameter: {t_val} ({'IP BANNED - need different proxy' if t_val == 'bv' else 'OK - solvable'})")

    # Proceed regardless of t value — try solving anyway

    # Parse proxy for 2Captcha (needs separate fields)
    proxy_clean = PROXY.replace('http://', '').replace('https://', '')
    proxy_login = proxy_password = proxy_host = proxy_port = ''
    if '@' in proxy_clean:
        auth, hostport = proxy_clean.rsplit('@', 1)
        proxy_login, proxy_password = auth.split(':', 1)
        proxy_host, proxy_port = hostport.rsplit(':', 1)
    elif ':' in proxy_clean:
        parts = proxy_clean.split(':')
        proxy_host = parts[0]
        proxy_port = parts[1]

    print(f"  Proxy for 2Captcha: {proxy_host}:{proxy_port} (login={proxy_login})")

    # Use raw 2Captcha API (more reliable for DataDome than SDK wrapper)
    import requests as std_requests
    print("  Sending to 2Captcha DataDome solver (raw API)...")

    try:
        # Submit task
        submit_data = {
            'key': CAPTCHA_KEY,
            'method': 'datadome',
            'captcha_url': captcha_url,
            'pageurl': SIGNUP_URL,
            'userAgent': UA,
            'proxy': f'{proxy_login}:{proxy_password}@{proxy_host}:{proxy_port}' if proxy_login else f'{proxy_host}:{proxy_port}',
            'proxytype': 'http',
            'json': 1,
        }
        sub_resp = std_requests.post('https://2captcha.com/in.php', data=submit_data, timeout=30)
        sub_data = sub_resp.json()
        print(f"  Submit response: {sub_data}")

        if sub_data.get('status') != 1:
            raise RuntimeError(f"Submit failed: {sub_data.get('request')}")

        task_id = sub_data['request']
        print(f"  Task ID: {task_id}")

        # Poll for result
        for poll in range(60):
            time.sleep(5)
            res_resp = std_requests.get(
                f'https://2captcha.com/res.php?key={CAPTCHA_KEY}&action=get&json=1&id={task_id}',
                timeout=15)
            res_data = res_resp.json()
            if res_data.get('request') == 'CAPCHA_NOT_READY':
                print(f"  Waiting... ({(poll+1)*5}s)")
                continue
            elif 'ERROR' in str(res_data.get('request', '')):
                raise RuntimeError(f"Solve error: {res_data['request']}")
            else:
                break

        solved_raw = res_data.get('request', '')
        print(f"  Solved raw: {solved_raw[:80]}...")

        # Extract datadome cookie value
        if 'datadome=' in solved_raw:
            solved_cookie = solved_raw.split('datadome=')[1].split(';')[0]
        else:
            solved_cookie = solved_raw

        print(f"  Solved cookie: {solved_cookie[:60]}...")

        if solved_cookie:
            s.cookies.set('datadome', solved_cookie, domain='.soundcloud.com')

            print(f"\n{'='*60}")
            print(f"STEP 4: Retry signup with solved DataDome cookie")

            resp = s.post(SIGNUP_URL, json=signup_payload, headers=signup_headers, timeout=30)
            print(f"  Status: {resp.status_code}")
            print(f"  Body: {resp.text[:300]}")

    except Exception as e:
        print(f"  2Captcha error: {e}")
        import traceback
        traceback.print_exc()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Handle success (202 = email verification needed)
# ══════════════════════════════════════════════════════════════════════════════
if resp.status_code == 202:
    result_data = resp.json()
    intermediate_token = result_data.get('intermediate_token', '')
    print(f"\n{'='*60}")
    print(f"STEP 5: SIGNUP SUCCESSFUL!")
    print(f"  Required: {result_data.get('required')}")
    print(f"  Token: {intermediate_token[:50]}...")

    # Poll smtp.dev for verification email
    print(f"\n{'='*60}")
    print(f"STEP 6: Waiting for verification email...")
    verify_url = ''
    for attempt in range(18):  # 90 seconds
        time.sleep(5)
        try:
            msg_resp = cffi_requests.get(
                f'https://api.smtp.dev/accounts/{account_id}/messages',
                headers=smtp_headers, timeout=15, impersonate='chrome124')
            if msg_resp.status_code == 200:
                data = msg_resp.json()
                messages = data if isinstance(data, list) else data.get('member', [])
                if messages:
                    print(f"  Got {len(messages)} message(s)!")
                    # Get the message body
                    msg_id = messages[0].get('id', '')
                    if msg_id:
                        body_resp = cffi_requests.get(
                            f'https://api.smtp.dev/messages/{msg_id}',
                            headers=smtp_headers, timeout=15, impersonate='chrome124')
                        body = body_resp.json().get('html', body_resp.json().get('text', ''))
                        # Find verification link
                        urls = re.findall(r'href="(https?://[^"]*soundcloud[^"]*)"', body)
                        for u in urls:
                            if any(kw in u.lower() for kw in ['confirm', 'verify', 'activate', 'email']):
                                verify_url = u
                                break
                        if not verify_url and urls:
                            verify_url = urls[0]
                        print(f"  Verification URL: {verify_url[:80]}...")
                    break
        except Exception as e:
            print(f"  Poll error: {e}")
        print(f"  Waiting... ({(attempt+1)*5}s)")

    if verify_url:
        print(f"\n{'='*60}")
        print(f"STEP 7: Clicking verification link")
        vresp = cffi_requests.get(verify_url, timeout=20, impersonate='chrome124')
        print(f"  Status: {vresp.status_code}")
        time.sleep(3)

        # POST email-verified
        print(f"\n{'='*60}")
        print(f"STEP 8: POST email-verified")
        ev_resp = s.post(
            f'https://api-auth.soundcloud.com/email-verified?client_id={CLIENT_ID}',
            json={'intermediate_token': intermediate_token},
            headers=signup_headers,
            timeout=20
        )
        print(f"  Status: {ev_resp.status_code}")
        print(f"  Body: {ev_resp.text[:300]}")
    else:
        print("  No verification email received within timeout")

    print(f"\n{'='*60}")
    print(f"ACCOUNT CREATED:")
    print(f"  Email: {email}")
    print(f"  Password: {sc_password}")
    print(f"  Username: {sc_username}")
    print(f"  Token: {intermediate_token[:50]}...")
else:
    print(f"\nFAILED: Status {resp.status_code}")
    print(f"Body: {resp.text[:500]}")
