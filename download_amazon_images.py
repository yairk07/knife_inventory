import sqlite3
import urllib.request
import os
import ssl

DB_NAME = 'knives.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def download(url, filepath):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Referer': 'https://www.google.com/',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = resp.read()
            if len(data) < 1000:
                print(f"  Too small ({len(data)} bytes), skipping")
                return False
            with open(filepath, 'wb') as f:
                f.write(data)
            print(f"  Downloaded {len(data)} bytes")
            return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False

# URLs gathered from browser + web search
image_sources = {
    32: {  # SOG Seal XR
        "name": "sog_seal_xr.jpg",
        "urls": [
            "https://cdn11.bigcommerce.com/s-43l4b17cyb/images/stencil/original/products/720/13623/12-21-01-57_product_alt_5__45834-min__83492.1747153530.jpg",
            "https://m.media-amazon.com/images/I/61Kn28S8bRL._AC_SL1500_.jpg",
        ]
    },
    33: {  # SOG Pentagon FX Blackout
        "name": "sog_pentagon_fx.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/71Gd4vMlMaL._AC_SL1500_.jpg",
            "https://m.media-amazon.com/images/I/61hUOTXdRcL._AC_SL1500_.jpg",
        ]
    },
    34: {  # Cold Steel Code 4
        "name": "cs_code4.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/51z7TGVXETL._AC_SL1500_.jpg",
            "https://m.media-amazon.com/images/I/41DJnP0-cjL._AC_SL1500_.jpg",
        ]
    },
    35: {  # Cold Steel Espada
        "name": "cs_espada.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/61z0Y6V8gYL._AC_SL1500_.jpg",
            "https://m.media-amazon.com/images/I/71WfZKWzQwL._AC_SL1500_.jpg",
        ]
    },
    36: {  # Cold Steel Magnum Tanto
        "name": "cs_magnum_tanto.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/61uRLrFpxRL._AC_SL1500_.jpg",
            "https://m.media-amazon.com/images/I/71W7qQlTuQL._AC_SL1500_.jpg",
        ]
    },
    37: {  # Microtech UTX-85
        "name": "microtech_utx85.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/41hIPQiqYcL._AC_SL1500_.jpg",
            "https://m.media-amazon.com/images/I/51jK2HLHG1L._AC_SL1500_.jpg",
        ]
    },
    38: {  # Microtech Delta
        "name": "microtech_delta.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/31i7T5dHpEL._AC_SL1500_.jpg",
            "https://m.media-amazon.com/images/I/41zxhN6LqmL._AC_SL1500_.jpg",
        ]
    },
    6: {  # Kizer Supreme
        "name": "kizer_supreme.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/61NQ0aw5d0L._AC_SL1500_.jpg",
            "https://m.media-amazon.com/images/I/71YCLV0AXML._AC_SL1500_.jpg",
        ]
    },
    5: {  # LionSteel
        "name": "lionsteel_knife.jpg",
        "urls": [
            "https://m.media-amazon.com/images/I/51Ln5DP3AkL._AC_SL1000_.jpg",
            "https://m.media-amazon.com/images/I/616k+7VIAFL._AC_SL1500_.jpg",
        ]
    },
}

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

success = 0
failed_list = []

for kid, info in image_sources.items():
    fname = info["name"]
    fpath = os.path.join(UPLOAD_FOLDER, fname)
    print(f"\nID {kid}: {fname}")
    
    got_it = False
    for url in info["urls"]:
        print(f"  Trying: {url}")
        if download(url, fpath):
            cursor.execute("UPDATE knives SET image = ?, image_url = '' WHERE id = ?", (fname, kid))
            got_it = True
            success += 1
            break
    
    if not got_it:
        failed_list.append(kid)

conn.commit()
conn.close()
print(f"\n=== Result: {success} downloaded, {len(failed_list)} failed (IDs: {failed_list}) ===")
