import sqlite3
import urllib.request
import json
import re
import os
import time
import ssl

DB_NAME = 'knives.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Disable SSL verification for problematic sites
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

KNIVES_NEEDING_IMAGES = [
    (34, "Cold Steel", "Code 4"),
    (35, "Cold Steel", "Espada"),
    (36, "Cold Steel", "Magnum Tanto"),
    (6,  "Kizer", "Supreme"),
    (5,  "LionSteel", "Unknown Model"),
    (38, "Microtech", "Delta"),
    (37, "Microtech", "UTX-85"),
    (33, "SOG", "Pentagon FX Blackout"),
    (32, "SOG", "Seal XR"),
]

# Direct known good URLs from retailers / wikimedia for each knife
# These are carefully selected from real retailer product pages
DIRECT_URLS = {
    34: [  # Cold Steel Code 4
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/coldsteel/images/CS58PS.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/coldsteel/images/CS58PAS.jpg",
    ],
    35: [  # Cold Steel Espada
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/coldsteel/images/CS62MGC.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/coldsteel/images/CS62MA.jpg",
    ],
    36: [  # Cold Steel Magnum Tanto
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/coldsteel/images/CS13QMBII.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/coldsteel/images/CS13BMBII.jpg",
    ],
    6: [  # Kizer Supreme
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/kizer/images/KIV4456A2n.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/kizer/images/KIV4456A1.jpg",
    ],
    5: [  # LionSteel
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/lionsteel/images/LNTT01CVG.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/lionsteel/images/LNSR22AABS.jpg",
    ],
    38: [  # Microtech Delta
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/microtech/images/MT1271T10.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/microtech/images/MT12210T.jpg",
    ],
    37: [  # Microtech UTX-85
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/microtech/images/MT23110.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/microtech/images/MT2311.jpg",
    ],
    33: [  # SOG Pentagon FX Blackout
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/sog-knives/images/SOG17610257.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/sog-knives/images/SOG17610157.jpg",
    ],
    32: [  # SOG Seal XR
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/sog-knives/images/SOG12210157.jpg",
        "https://images.knifecenter.com/thumb/1500x1500/knifecenter/sog-knives/images/SOG12210257.jpg",
    ],
}

def download(url, filepath):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Referer': 'https://www.google.com/',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = resp.read()
            if len(data) < 1000:  # too small, probably an error page
                return False
            with open(filepath, 'wb') as f:
                f.write(data)
            return True
    except Exception as e:
        print(f"  FAIL: {url} -> {e}")
        return False

def main():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    success = 0
    failed = 0
    
    for kid, brand, model in KNIVES_NEEDING_IMAGES:
        print(f"\n--- ID {kid}: {brand} {model} ---")
        urls = DIRECT_URLS.get(kid, [])
        
        got_it = False
        for url in urls:
            ext = '.jpg'
            if '.png' in url.lower(): ext = '.png'
            elif '.webp' in url.lower(): ext = '.webp'
            
            filename = f"real_{kid}{ext}"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            
            print(f"  Trying: {url}")
            if download(url, filepath):
                cursor.execute("UPDATE knives SET image = ?, image_url = '' WHERE id = ?", (filename, kid))
                print(f"  SUCCESS -> {filename} ({os.path.getsize(filepath)} bytes)")
                got_it = True
                success += 1
                break
        
        if not got_it:
            failed += 1
            print(f"  ALL URLS FAILED for {brand} {model}")
        
        time.sleep(0.5)
    
    conn.commit()
    conn.close()
    print(f"\n=== DONE: {success} success, {failed} failed ===")

if __name__ == '__main__':
    main()
