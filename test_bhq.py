import urllib.request
import re

url = 'https://www.bladehq.com/?search=benchmade+adamas+275'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        links = re.findall(r'https://[a-zA-Z0-9.\-/_]+\.(?:jpg|png|webp|avif)', html)
        links = [l for l in links if 'product' in l.lower() or 'item' in l.lower() or 'image' in l.lower()]
        print("Links found:", links[:5])
except Exception as e:
    print(f"Error: {e}")
