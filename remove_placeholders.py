import sqlite3
conn = sqlite3.connect('knives.db')
cursor = conn.cursor()
cursor.execute("UPDATE knives SET image = '' WHERE image = 'placeholder.png'")
conn.commit()
conn.close()
