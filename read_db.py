import sqlite3
conn = sqlite3.connect('knives.db')
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM knives WHERE image LIKE 'ddg_%'")
print(f'Filled DDG Images: {cursor.fetchone()[0]}')
