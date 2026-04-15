import urllib.request
import os

urls = {
    'benchmade_logo.png': 'https://upload.wikimedia.org/wikipedia/en/thumb/5/52/Benchmade_Logo.svg/300px-Benchmade_Logo.svg.png',
    'coldsteel_logo.png': 'https://upload.wikimedia.org/wikipedia/commons/thumb/c/ca/Cold_Steel_Logo.svg/300px-Cold_Steel_Logo.svg.png',
    'sog_logo.png': 'https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/SOG_Specialty_Knives_Logo.svg/300px-SOG_Specialty_Knives_Logo.svg.png',
    'microtech_logo.png': 'https://upload.wikimedia.org/wikipedia/commons/3/36/Microtech_Knives_logo.jpg'
}

UPLOAD = os.path.join('static', 'uploads')
for name, url in urls.items():
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        data = urllib.request.urlopen(req).read()
        with open(os.path.join(UPLOAD, name), 'wb') as f:
            f.write(data)
        print(f"Downloaded {name}")
    except Exception as e:
        print(f"Failed {name}: {e}")
