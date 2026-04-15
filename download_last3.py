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
                print(f"  Too small ({len(data)} bytes)")
                return False
            with open(filepath, 'wb') as f:
                f.write(data)
            print(f"  OK: {len(data)} bytes")
            return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False

sources = {
    38: ("microtech_delta.jpg", "https://cdn11.bigcommerce.com/s-cta3pou86s/images/stencil/1280x1280/products/26865/181051/123-1UT-DSH__77187.1691435511.jpg"),
    6:  ("kizer_supreme.jpg", "https://casiberia.com/img/prod/2x/kk0233_4.jpg"),
    5:  ("lionsteel_knife.webp", "https://assets.katogroup.eu/i/katogroup/LI-BM1-CF_03_lionsteel?fmt=webp"),
}

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

for kid, (fname, url) in sources.items():
    fpath = os.path.join(UPLOAD_FOLDER, fname)
    print(f"ID {kid}: {fname}")
    if download(url, fpath):
        cursor.execute("UPDATE knives SET image = ?, image_url = '' WHERE id = ?", (fname, kid))
    else:
        print(f"  FAILED {fname}")

conn.commit()
conn.close()
print("Done!")
