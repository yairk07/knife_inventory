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
            print(f"  OK: {len(data)} bytes saved")
            return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False

# Real URLs found by the browser subagent from Google Images
image_sources = {
    34: {
        "name": "cs_code4.jpg",
        "url": "https://cdn11.bigcommerce.com/s-99kn4fj7jr/images/stencil/1280x1280/products/557/807/58PS_1__83146.1607300828.jpg?c=1"
    },
    35: {
        "name": "cs_espada.jpg",
        "url": "https://cdn11.bigcommerce.com/s-99kn4fj7jr/images/stencil/original/products/453/707/62MA_1__34759.1607300823.jpg"
    },
    36: {
        "name": "cs_magnum_tanto.jpg",
        "url": "https://files.knifecenter.com/knifecenter/cold-steel-knives/images/CS35AE_1.jpg"
    },
    37: {
        "name": "microtech_utx85.webp",
        "url": "https://sharg.pl/hpeciai/70791439988b6714dc1d8f302e3cfb29/eng_pl_Microtech-UTX-85-S-E-OTF-Knife-Natural-Clear-Aluminum-Apocalyptic-M390-231-10APCR-123904_1.webp"
    },
    38: {
        "name": "microtech_delta.jpg",
        "url": "https://files.knifecenter.com/knifecenter/microtech/images/MT1233UTDSH_1.jpg"
    },
    33: {
        "name": "sog_pentagon_fx.jpg",
        "url": "https://cdn11.bigcommerce.com/s-43l4b17cyb/images/stencil/original/products/986/10635/17-61-01-57_product_vertical__52471__60891.1747069397.jpg?c=2"
    },
    6: {
        "name": "kizer_supreme.jpg",
        "url": "https://files.knifecenter.com/knifecenter/kizlyar-supreme/images/VENDETTA_AUS8_S_G10B_1.jpg"
    },
    5: {
        "name": "lionsteel_knife.jpg",
        "url": "https://whitemountainknives.com/content/images/thumbs/0014022_lionsteel-bestman-folding-knife-ebony-wood-handle-m390-clip-plain-edge-bm1-eb.jpeg"
    },
}

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

success = 0
failed = []

for kid, info in image_sources.items():
    fname = info["name"]
    fpath = os.path.join(UPLOAD_FOLDER, fname)
    url = info["url"]
    print(f"ID {kid}: {fname}")
    print(f"  URL: {url}")
    
    if download(url, fpath):
        cursor.execute("UPDATE knives SET image = ?, image_url = '' WHERE id = ?", (fname, kid))
        success += 1
    else:
        failed.append((kid, fname))

conn.commit()
conn.close()
print(f"\n=== DONE: {success} success, {len(failed)} failed ===")
if failed:
    for kid, name in failed:
        print(f"  Failed: ID {kid} ({name})")
