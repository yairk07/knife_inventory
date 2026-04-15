import sqlite3
conn = sqlite3.connect('knives.db')
cursor = conn.cursor()
cursor.execute("UPDATE knives SET image_url = '' WHERE image_url LIKE '%sogknives%' OR image_url LIKE '%coldsteel%' OR image_url LIKE '%microtechknives%'")
conn.commit()
conn.close()
print('Fixed URLs')
