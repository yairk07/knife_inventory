import sqlite3
import json

conn = sqlite3.connect('knives.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT id, brand, model FROM knives WHERE (brand='Benchmade' OR brand='Cold Steel') AND (image='' OR image='placeholder.png')")
items = []
for row in cursor.fetchall():
    print(f"{row['id']}: {row['brand']} {row['model']}")
    items.append({"id": row['id'], "brand": row['brand'], "model": row['model']})

with open('missing.json', 'w') as f:
    json.dump(items, f)
