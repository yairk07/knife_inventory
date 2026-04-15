import sqlite3
import urllib.request
import os

DB_NAME = 'knives.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def download_image(url, filename):
    req = urllib.request.Request(
        url, 
        data=None, 
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
            'Referer': 'https://www.google.com/'
        }
    )
    try:
        with urllib.request.urlopen(req) as response:
            with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as out_file:
                out_file.write(response.read())
        return True
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return False

def pull_images():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Select all that have a hotlink URL
    cursor.execute("SELECT id, image_url FROM knives WHERE image_url != ''")
    rows = cursor.fetchall()
    
    success_count = 0
    fail_count = 0
    
    for row in rows:
        knife_id = row['id']
        url = row['image_url']
        
        ext = '.jpg'
        if '.png' in url.lower(): ext = '.png'
        elif '.webp' in url.lower(): ext = '.webp'
        
        filename = f"knife_{knife_id}{ext}"
        print(f"Fetching {url}")
        
        if download_image(url, filename):
            cursor.execute("UPDATE knives SET image = ?, image_url = '' WHERE id = ?", (filename, knife_id))
            success_count += 1
        else:
            # Fall back to placeholder if download fails so we don't get a broken image icon
            cursor.execute("UPDATE knives SET image = 'placeholder.png', image_url = '' WHERE id = ?", (knife_id,))
            fail_count += 1
            
    conn.commit()
    conn.close()
    print(f"Successfully localized {success_count} images. Failed {fail_count} images, reverted to placeholder.")

if __name__ == '__main__':
    pull_images()
