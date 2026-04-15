import urllib.request
import re

url = 'https://www.bladehq.com/?search=benchmade+adamas+275'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
try:
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
        # BladeHQ uses class 'product-img' or meta properties. Let's find first jpg or png link inside an img tag containing benchmade adamas
        images = set(re.findall(r'src="([^"]+\.avif|[^"]+\.jpg|[^"]+\.png|[^"]+\.webp)"', html))
        images = [img for img in images if 'bhq-data' in img and 'product/' in img]
        # if 'bhq-data' doesn't work, let's just grab the first image matching
        if not images:
            matches = re.findall(r'https://[^"]+bladehq[^"]+\(?:jpg|png|webp|avif\)', html)
            print("Matches:", matches[:5])
        else:
            print("Found images:", images)
except Exception as e:
    print(f"Error: {e}")
