import sqlite3
import json
import os

DB_NAME = "knives.db"
SEED_FILE = "seed_data.json"

def seed_database():
    if not os.path.exists(SEED_FILE):
        print(f"Error: {SEED_FILE} not found.")
        return

    with open(SEED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Clear existing data so we don't have duplicates
    cursor.execute("DELETE FROM knives")
    # Reset auto-increment
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='knives'")

    inserted_count = 0

    for item in data:
        # Provide fallbacks if missing
        brand = item.get("brand", "Unknown")
        model = item.get("model", "Unknown")
        category = item.get("category", "Utility")
        status = item.get("status", "home")
        buy_price = float(item.get("buy_price", 0.0))
        estimated_value = float(item.get("estimated_value", 0.0))
        quantity = int(item.get("quantity", 1))
        notes = item.get("notes", "")
        description = item.get("description", "")
        image = item.get("image", "placeholder.png")

        image_url = item.get("image_url", "")
        image_source_url = item.get("image_source_url", "")
        price_source_url = item.get("price_source_url", "")
        data_confidence = item.get("data_confidence", "low")

        cursor.execute('''
            INSERT INTO knives (
                brand, model, category, status, 
                buy_price, estimated_value, quantity, 
                notes, image, description,
                image_url, image_source_url, price_source_url, data_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            brand, model, category, status, 
            buy_price, estimated_value, quantity, 
            notes, image, description,
            image_url, image_source_url, price_source_url, data_confidence
        ))
        inserted_count += 1

    conn.commit()
    conn.close()

    print(f"Successfully seeded {inserted_count} knives into the database.")

if __name__ == "__main__":
    seed_database()
