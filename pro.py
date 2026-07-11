import requests

proxies_list = open("proxies.txt", "r").read().splitlines()

for proxy in proxies_list:
    try:
        # Ensure proper format
        if not proxy.startswith("http://") and not proxy.startswith("https://"):
            proxy = "http://" + proxy

        proxies = {
            "http": proxy,
            "https": proxy
        }

        response = requests.get(
            "https://api.ipify.org?format=json",
            proxies=proxies,
            timeout=5
        )

        print(f"{proxy} -> {response.text}")

    except requests.exceptions.RequestException as e:
        print(f"{proxy} -> FAILED ({e})")

print("Done")
input()
