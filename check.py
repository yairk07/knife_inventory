import json
with open('seed_data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
for idx, item in enumerate(data):
    if item['brand'] in ['Benchmade', 'Cold Steel', 'Microtech', 'SOG']:
        print(f\"{idx}: {item['brand']} | {item['model']} -> {item.get('image_url')}\")
