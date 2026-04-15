import urllib.request
import re

url = 'https://html.duckduckgo.com/html/?q=benchmade+adamas+site:benchmade.com+filetype:jpg'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
try:
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        links = re.findall(r'href=\"([^"]+\.jpg(?:[^"]*))\"', html)
        print("Links found:", links[:5] if links else "None")
except Exception as e:
    print(f"Error: {e}")
