import sqlite3

conn = sqlite3.connect('knives.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# All knives that need images (excluding Gerber, Kershaw, and no-brand)
cursor.execute("""
    SELECT id, brand, model, image 
    FROM knives 
    WHERE brand NOT IN ('Gerber', 'Kershaw', 'No Brand', 'Unknown', '') 
    ORDER BY brand, model
""")

need_img = []
have_img = []

for row in cursor.fetchall():
    img = row['image']
    if img and img != 'placeholder.png' and img != '':
        have_img.append(row)
    else:
        need_img.append(row)

print("=== KNIVES THAT ALREADY HAVE IMAGES ===")
for r in have_img:
    print(f"  ID {r['id']:3d}: {r['brand']:15s} {r['model']:40s} -> {r['image']}")

print(f"\n=== KNIVES THAT NEED IMAGES ({len(need_img)}) ===")
for r in need_img:
    print(f"  ID {r['id']:3d}: {r['brand']:15s} {r['model']:40s} -> '{r['image']}'")

conn.close()
