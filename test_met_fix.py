#!/usr/bin/env python3
"""Quick test to verify Met API fixes are working."""

import requests
import time

# Test with old user agent (should fail)
def test_old_headers():
    print("Testing with old bot-like headers...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "MET-ArtDownloadBot/1.0"
    })

    url = "https://collectionapi.metmuseum.org/public/collection/v1/objects/259652"
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        print(f"✅ Old headers: SUCCESS (status {response.status_code})")
    except Exception as e:
        print(f"❌ Old headers: FAILED - {e}")

    time.sleep(2)

# Test with new browser-like headers
def test_new_headers():
    print("\nTesting with new browser-like headers...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    url = "https://collectionapi.metmuseum.org/public/collection/v1/objects/259652"
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        print(f"✅ New headers: SUCCESS (status {response.status_code})")
        print(f"   Artwork: {data.get('title', 'Unknown')} by {data.get('artistDisplayName', 'Unknown')}")
    except Exception as e:
        print(f"❌ New headers: FAILED - {e}")

if __name__ == "__main__":
    test_old_headers()
    test_new_headers()
