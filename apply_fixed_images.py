import sqlite3
import shutil
import os

DB_NAME = 'knives.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')

bugout_src = r'C:\Users\yairk\.gemini\antigravity\brain\060a3123-22a7-4cfb-a718-8bb8ec014b8d\benchmade_bugout_1776108817174.png'
grip_src = r'C:\Users\yairk\.gemini\antigravity\brain\060a3123-22a7-4cfb-a718-8bb8ec014b8d\benchmade_griptilian_1776108831271.png'

bugout_dest = os.path.join(UPLOAD_FOLDER, 'benchmade_bugout.png')
grip_dest = os.path.join(UPLOAD_FOLDER, 'benchmade_griptilian.png')

shutil.copy(bugout_src, bugout_dest)
shutil.copy(grip_src, grip_dest)

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

# Assign beautiful images to these exact models
cursor.execute("UPDATE knives SET image = 'benchmade_bugout.png', image_url = '' WHERE model LIKE '%Bugout%'")
cursor.execute("UPDATE knives SET image = 'benchmade_griptilian.png', image_url = '' WHERE model LIKE '%Griptilian%'")

conn.commit()
conn.close()

print('Applied premium replacement images successfully.')
