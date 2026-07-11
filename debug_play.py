import os
import time
from playwright.sync_api import sync_playwright

os.environ['PLAYWRIGHT_BROWSERS_PATH'] = r'e:\soundcloud tool\R APP DEV\backend\browsers'

def debug():
    with sync_playwright() as pw:
        print("Launching browser...")
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        req_log = []
        def handle_request(req):
            req_log.append(f"{req.method} {req.url}")
            if "track-play" in req.url or "play-count" in req.url or "play" in req.url:
                print(f"!!! PLAY EVENT: {req.url}")

        page.on("request", handle_request)
        
        # Using a verified active track
        url = "https://w.soundcloud.com/player/?url=https%3A%2F%2Fapi.soundcloud.com%2Ftracks%2F1714416759&auto_play=true"
        print(f"Navigating to {url}...")
        page.goto(url, wait_until="load")
        
        time.sleep(10)
        page.screenshot(path="debug_state_1.png")
        print("Screenshot 1 saved.")
        
        # Look for play button
        print("Looking for play button...")
        try:
            # The play button in the widget is often .playButton or button.sc-button-play
            # We use multiple selectors
            selectors = [".playButton", "button.sc-button-play", ".sc-button-play", ".play-button"]
            btn = None
            for s in selectors:
                if page.locator(s).is_visible():
                    btn = page.locator(s)
                    print(f"Found button with selector: {s}")
                    break
            
            if btn:
                print("Clicking play button...")
                btn.click()
                time.sleep(15)
                page.screenshot(path="debug_state_2.png")
                print("Screenshot 2 saved after click.")
            else:
                print("No play button found via simple selectors.")
                # Maybe it's in an iframe? The widget itself IS usually the page here.
        except Exception as e:
            print(f"Interaction error: {e}")

        with open("full_request_log.txt", "w") as f:
            f.write("\n".join(req_log))
        print("Full request log saved.")

        browser.close()

if __name__ == "__main__":
    debug()
