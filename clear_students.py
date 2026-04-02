import sqlite3, os, glob

# ── 1. Clear DB records ────────────────────────────────────────────────
conn = sqlite3.connect('attendance.db')
c = conn.cursor()

c.execute("DELETE FROM users WHERE role='student'")
students_del = c.rowcount

c.execute("DELETE FROM attendance")
att_del = c.rowcount

conn.commit()
conn.close()

print(f"Deleted {students_del} student(s) from users table.")
print(f"Deleted {att_del} attendance record(s).")

# ── 2. Clear known_faces folder ────────────────────────────────────────
known_faces_dir = 'known_faces'
if os.path.isdir(known_faces_dir):
    imgs = glob.glob(os.path.join(known_faces_dir, '*.png')) + \
           glob.glob(os.path.join(known_faces_dir, '*.jpg')) + \
           glob.glob(os.path.join(known_faces_dir, '*.jpeg')) + \
           glob.glob(os.path.join(known_faces_dir, '*.webp'))
    for f in imgs:
        os.remove(f)
    print(f"Removed {len(imgs)} image(s) from '{known_faces_dir}/'.")
else:
    print(f"Folder '{known_faces_dir}/' not found — skipping.")

print("All done! Restart the Flask server to reload face encodings.")
