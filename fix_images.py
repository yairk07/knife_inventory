import sqlite3
import urllib.request
import os

DB_NAME = 'knives.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')

def download_image(url, filename):
    req = urllib.request.Request(
        url, 
        data=None, 
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
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

def fix_images():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, brand, model, image_url FROM knives WHERE image_url != ''")
    rows = cursor.fetchall()
    
    for row in rows:
        knife_id = row['id']
        image_url = row['image_url']
        
        # Determine extension from url
        ext = '.jpg'
        if '.png' in image_url.lower(): ext = '.png'
        elif '.webp' in image_url.lower(): ext = '.webp'
        elif '.jpeg' in image_url.lower(): ext = '.jpeg'
        
        filename = f"knife_{knife_id}{ext}"
        print(f"Downloading {filename} from {image_url}")
        
        if download_image(image_url, filename):
            # Update database to rely on local image and blank out the external URL
            cursor.execute(
                "UPDATE knives SET image = ?, image_url = '' WHERE id = ?",
                (filename, knife_id)
            )
            print(f"Successfully fixed ID {knife_id}")
            
    conn.commit()
    conn.close()

if __name__ == '__main__':
    fix_images()
