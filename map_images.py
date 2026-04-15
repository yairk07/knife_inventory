import sqlite3
import shutil
import os

DB_NAME = 'knives.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')
base_dir = r'C:\Users\yairk\.gemini\antigravity\brain\060a3123-22a7-4cfb-a718-8bb8ec014b8d'

# Map generated prefix patterns to matching DB model substrings
maps = {
    'cs_survivalist': 'Survivalist',
    'cs_recon1': 'Recon 1',
    'cs_srk': 'SRK',
    'bm_casbah': 'Casbah',
    'bm_adira': 'Adira',
    'bm_superfreek': 'Super Freek',
    'bm_claymore': 'Claymore',
    'bm_psk': 'PSK',
    'bm_shootout': 'Shootout',
    'bm_socp176bk': '176BK',
    'bm_socp179': '179',
    'bm_kitchen4010': '4010BK',
    'bm_cla4300': 'CLA',
    'bm_bushcrafter163': 'Bushcrafter'
}

# find exact generated files
generated_files = []
for f in os.listdir(base_dir):
    if f.endswith('.png') and any(f.startswith(prefix) for prefix in maps.keys()):
        generated_files.append(f)

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

count = 0
for f in generated_files:
    # get prefix
    prefix = next(p for p in maps.keys() if f.startswith(p))
    model_str = maps[prefix]
    
    # safe new name
    new_name = prefix + '.png'
    dest = os.path.join(UPLOAD_FOLDER, new_name)
    shutil.copy(os.path.join(base_dir, f), dest)
    
    # update DB
    cursor.execute(f"UPDATE knives SET image = ?, image_url = '' WHERE model LIKE '%{model_str}%'", (new_name,))
    count += cursor.rowcount

conn.commit()
conn.close()

print(f'Successfully mapped {count} specific knife models!')
