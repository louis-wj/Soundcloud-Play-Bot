"""
Test: Launch browser, navigate to SoundCloud signup, dump page structure.
Run from backend dir: python test_sc_signup.py
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from engine import _detect_chrome_version, _chrome_launch_lock

chrome_ver = _detect_chrome_version()
print(f"Chrome version: {chrome_ver}")

options = uc.ChromeOptions()
options.add_argument('--no-sandbox')
options.add_argument('--disable-gpu')
options.add_argument('--window-size=1280,900')

print("Launching browser...")
with _chrome_launch_lock:
    driver = uc.Chrome(options=options, headless=False, version_main=chrome_ver)
driver.set_page_load_timeout(120)

try:
    print("Navigating to soundcloud.com (may take 30-60s)...")
    try:
        driver.get("https://soundcloud.com/")
    except Exception as e:
        print(f"Page load timeout (normal for SC): {str(e)[:80]}")
        print("Continuing anyway — page may have partially loaded...")
    time.sleep(8)
    print(f"Title: {driver.title}")
    print(f"URL: {driver.current_url}")

    # Find signup/signin buttons
    print("\n=== Buttons with sign/create text ===")
    buttons = driver.find_elements(By.CSS_SELECTOR, 'button, a')
    for el in buttons:
        text = el.text.strip()
        if text and any(kw in text.lower() for kw in ['sign', 'create', 'register', 'log in']):
            href = el.get_attribute('href') or ''
            print(f"  <{el.tag_name}> '{text}' href='{href[:80]}'")

    # Click "Create account"
    print("\n=== Clicking 'Create account' ===")
    try:
        btn = driver.find_element(By.XPATH, '//button[contains(text(),"Create account")]')
        print(f"Found: '{btn.text}'")
        btn.click()
        time.sleep(5)
    except Exception as e:
        print(f"Could not click Create account: {e}")

    # Save screenshot of modal
    ss1 = os.path.join(os.path.dirname(__file__), "sc_after_create_click.png")
    driver.save_screenshot(ss1)
    print(f"Screenshot after click: {ss1}")

    # Find the auth iframe
    print("\n=== Looking for auth iframe ===")
    iframes = driver.find_elements(By.TAG_NAME, 'iframe')
    auth_iframe = None
    for iframe in iframes:
        src = iframe.get_attribute('src') or ''
        print(f"  iframe src='{src[:120]}'")
        if 'secure.soundcloud.com' in src or 'web-auth' in src:
            auth_iframe = iframe
            print(f"  >>> THIS IS THE AUTH IFRAME")

    if auth_iframe:
        print("\n=== Switching into auth iframe ===")
        driver.switch_to.frame(auth_iframe)
        time.sleep(3)

        # Dump EVERYTHING inside the iframe
        print("\n--- All elements in auth iframe ---")
        all_els = driver.find_elements(By.CSS_SELECTOR, '*')
        print(f"Total elements: {len(all_els)}")

        print("\n--- Buttons ---")
        for el in driver.find_elements(By.CSS_SELECTOR, 'button, a, [role=button]'):
            text = el.text.strip()
            if text:
                cls = (el.get_attribute('class') or '')[:60]
                href = el.get_attribute('href') or ''
                print(f"  <{el.tag_name}> '{text}' class='{cls}' href='{href[:60]}'")

        print("\n--- Inputs ---")
        for inp in driver.find_elements(By.CSS_SELECTOR, 'input, select, textarea'):
            print(f"  <{inp.tag_name}> type='{inp.get_attribute('type')}' name='{inp.get_attribute('name')}' placeholder='{inp.get_attribute('placeholder')}' id='{inp.get_attribute('id')}' aria-label='{inp.get_attribute('aria-label')}'")

        print("\n--- Labels ---")
        for label in driver.find_elements(By.TAG_NAME, 'label'):
            print(f"  <label> '{label.text[:60]}' for='{label.get_attribute('for')}'")

        print("\n--- Divs/spans with text (first 30) ---")
        count = 0
        for el in driver.find_elements(By.CSS_SELECTOR, 'div, span, p, h1, h2, h3'):
            text = el.text.strip()
            if text and len(text) < 100 and count < 30:
                print(f"  <{el.tag_name}> '{text}'")
                count += 1

        print("\n--- reCAPTCHA in iframe ---")
        for sel in ['[data-sitekey]', '.g-recaptcha', '#recaptcha', 'iframe']:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                src = el.get_attribute('src') or ''
                sitekey = el.get_attribute('data-sitekey') or ''
                if src or sitekey:
                    print(f"  {sel} sitekey='{sitekey}' src='{src[:100]}'")

        # Save iframe screenshot
        ss2 = os.path.join(os.path.dirname(__file__), "sc_iframe_screenshot.png")
        driver.save_screenshot(ss2)
        print(f"\nIframe screenshot: {ss2}")

        # Save iframe source
        isrc = os.path.join(os.path.dirname(__file__), "sc_iframe_source.html")
        with open(isrc, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"Iframe source: {isrc} ({len(driver.page_source)} bytes)")

        driver.switch_to.default_content()
    else:
        print("No auth iframe found!")

    input("\nPress Enter to close browser...")
finally:
    driver.quit()
