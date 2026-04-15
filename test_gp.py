import urllib.request
import re

url = 'https://www.gpknives.com/catalogsearch/result/?q=benchmade+adamas'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
try:
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        links = re.findall(r'https://[a-zA-Z0-9.\-/_]+\.(?:jpg|png|webp)', html)
        print("Links found:", set([l for l in links if 'image' in l.lower()][:10]))
except Exception as e:
    print(f"Error: {e}")
