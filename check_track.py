
import os, sys
# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))
import curl_cffi.requests as crequests
import json

_FALLBACK_CLIENT_ID = "WU4bVxk5Df0g5JC8ULzW77Ry7OM10Lyj"

def test():
    track_id = "2275084871"
    url = f'https://api-v2.soundcloud.com/tracks/{track_id}?client_id={_FALLBACK_CLIENT_ID}'
    print(f"Fetching: {url}")
    resp = crequests.get(url, impersonate='chrome110')
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"Title: {data.get('title')}")
        print(f"Sharing: {data.get('sharing')}")
        print(f"Streamable: {data.get('streamable')}")
        print(f"Permalink: {data.get('permalink_url')}")
    else:
        print(f"Error: {resp.text}")

if __name__ == "__main__":
    test()
