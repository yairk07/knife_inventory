"""
Migration: Add proper pricing columns.
Maps:  buy_price -> cost_price,  estimated_value -> msrp_new_price
Adds:  sale_price, price_confidence
"""
import sqlite3

DB = 'knives.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

# Check which columns already exist
c.execute("PRAGMA table_info(knives)")
existing = {row[1] for row in c.fetchall()}

migrations = [
    ("msrp_new_price", "REAL NOT NULL DEFAULT 0"),
    ("cost_price",     "REAL NOT NULL DEFAULT 0"),
    ("sale_price",     "REAL NOT NULL DEFAULT 0"),
    ("price_confidence", "TEXT DEFAULT 'low'"),
]

for col, typedef in migrations:
    if col not in existing:
        c.execute(f"ALTER TABLE knives ADD COLUMN {col} {typedef}")
        print(f"Added column: {col}")
    else:
        print(f"Column already exists: {col}")

# Migrate data: cost_price = buy_price, msrp_new_price = estimated_value
c.execute("UPDATE knives SET cost_price = buy_price WHERE cost_price = 0 AND buy_price > 0")
c.execute("UPDATE knives SET msrp_new_price = estimated_value WHERE msrp_new_price = 0 AND estimated_value > 0")
# Copy data_confidence to price_confidence where applicable
c.execute("UPDATE knives SET price_confidence = data_confidence WHERE price_confidence = 'low' AND data_confidence != 'low'")

conn.commit()
conn.close()
print("Migration complete.")
