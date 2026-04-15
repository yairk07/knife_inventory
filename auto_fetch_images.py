import sqlite3
import os
import urllib.request
from duckduckgo_search import DDGS
import time

DB_NAME = 'knives.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def download_image(url, filename):
    req = urllib.request.Request(
        url, 
        data=None, 
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as out_file:
                out_file.write(response.read())
        return True
    except Exception as e:
        return False

def auto_fetch():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Grab all Benchmade, Cold Steel, SOG, Microtech that don't have images
    cursor.execute('''
        SELECT id, brand, model FROM knives 
        WHERE (brand='Benchmade' OR brand='Cold Steel' OR brand='SOG' OR brand='Microtech') 
        AND (image='' OR image='placeholder.png')
    ''')
    rows = cursor.fetchall()
    
    with DDGS() as ddgs:
        for row in rows:
            knife_id = row['id']
            brand = row['brand']
            model = row['model']
            
            query = f"{brand} {model} knife white background"
            print(f"Searching: {query}")
            
            try:
                # get top 5 images
                results = list(ddgs.images(query, max_results=5))
            except Exception as e:
                print(f"DDG Search failed for {model}: {e}")
                continue
                
            success = False
            for img in results:
                url = img.get('image')
                if not url: continue
                
                ext = '.jpg'
                if '.png' in url.lower(): ext = '.png'
                elif '.webp' in url.lower(): ext = '.webp'
                
                filename = f"ddg_{knife_id}{ext}"
                if download_image(url, filename):
                    cursor.execute("UPDATE knives SET image = ?, image_url = '' WHERE id = ?", (filename, knife_id))
                    print(f"-> SUCCESS downloaded {filename}")
                    success = True
                    break
                    
            if not success:
                print(f"-> ALL DOWNLOADS FAILED for {model}")
                
            time.sleep(1) # politely sleep
            
    conn.commit()
    conn.close()

if __name__ == '__main__':
    auto_fetch()
