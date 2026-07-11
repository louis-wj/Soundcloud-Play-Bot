"""End-to-end test: create 1 SC account with real keys."""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

from sc_accounts_engine import _signup_one_account, _load_config, _config
import threading

import random, string
_load_config()
# Use session-sticky proxy so IP stays same for entire signup
sess_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
proxy = f'http://e9uvn4-session-{sess_id}-time-10:ltf4wk0mkqrt@eu.nettify.xyz:8080'
print(f"Config: smtp_key={bool(_config['smtp_dev_key'])}, domain={_config['smtp_domain']}, captcha={bool(_config['captcha_key'])}")
print(f"Proxy: {proxy}")

stop = threading.Event()
try:
    result = _signup_one_account(proxy_str=proxy, stop_event=stop)
    print(f"\n{'='*50}")
    print(f"RESULT: {result}")
    print(f"{'='*50}")
except Exception as e:
    print(f"\nFAILED: {e}")
    import traceback
    traceback.print_exc()
