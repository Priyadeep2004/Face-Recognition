from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
import os, sqlite3, base64, csv, io, secrets
from datetime import datetime
from io import BytesIO
from PIL import Image
import face_recognition
import numpy as np
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'faceid_pro_secret_key_change_in_prod')

DATABASE_NAME   = 'attendance.db'
ENROLLED_FACES_DIR = 'known_faces'

# ── Global face recognition state ────────────────────────────────────────────
known_face_encodings = []
known_face_names     = []
known_face_usernames = {}   # display_name -> username
known_face_meta      = {}   # display_name -> {username, roll_number}

# ── DB helper ─────────────────────────────────────────────────────────────────
def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ── Login decorator (admin only) ──────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ── Database init ─────────────────────────────────────────────────────────────
def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username     TEXT PRIMARY KEY,
                password     TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role         TEXT DEFAULT 'student',
                face_encoding BLOB,
                is_new       INTEGER DEFAULT 0,
                roll_number  TEXT DEFAULT ''
            )
        ''')
        for sql in [
            "ALTER TABLE users ADD COLUMN is_new INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN roll_number TEXT DEFAULT ''",
        ]:
            try: c.execute(sql); conn.commit()
            except Exception: pass

        c.execute('''
            CREATE TABLE IF NOT EXISTS attendance (
                display_name TEXT,
                username     TEXT,
                roll_number  TEXT DEFAULT '',
                timestamp    TEXT
            )
        ''')
        try: c.execute("ALTER TABLE attendance ADD COLUMN roll_number TEXT DEFAULT ''"); conn.commit()
        except Exception: pass

        # Default admin
        if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            c.execute("INSERT INTO users (username,password,display_name,role) VALUES (?,?,?,?)",
                      ('admin', generate_password_hash('admin123'), 'Admin User', 'admin'))
            conn.commit()
            print("Admin created: admin / admin123")

    load_known_faces_from_db()

# ── Load faces ────────────────────────────────────────────────────────────────
def load_known_faces_from_db():
    global known_face_encodings, known_face_names, known_face_usernames, known_face_meta
    known_face_encodings = []
    known_face_names     = []
    known_face_usernames = {}
    known_face_meta      = {}

    conn  = get_db_connection()
    users = conn.execute(
        "SELECT username, display_name, roll_number, face_encoding FROM users WHERE face_encoding IS NOT NULL"
    ).fetchall()
    conn.close()

    for user in users:
        try:
            enc = np.frombuffer(user['face_encoding'], dtype=np.float64)
            if enc.size == 128:
                known_face_encodings.append(enc)
                known_face_names.append(user['display_name'])
                known_face_usernames[user['display_name']] = user['username']
                known_face_meta[user['display_name']] = {
                    'username': user['username'],
                    'roll_number': user['roll_number'] or ''
                }
        except Exception as e:
            print(f"Error loading face for {user['display_name']}: {e}")
    print(f"Loaded {len(known_face_encodings)} known faces.")

if not os.path.exists(ENROLLED_FACES_DIR):
    os.makedirs(ENROLLED_FACES_DIR)

# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def home():
    """Public camera page — no login needed."""
    return render_template('index.html')

@app.route('/register')
def register():
    """Student self-registration disabled — redirect home."""
    return redirect(url_for('home'))

@app.route('/student')
def student_panel():
    """Public student camera panel — no login needed."""
    return render_template('student_panel.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Admin-only login."""
    if session.get('logged_in') and session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            if user['role'] != 'admin':
                flash('Only admins can log in here.', 'error')
                return redirect(url_for('home'))
            session['logged_in']    = True
            session['username']     = user['username']
            session['display_name'] = user['display_name']
            session['role']         = user['role']
            flash('Welcome back, Admin!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    """Legacy student dashboard — redirect to public camera."""
    return redirect(url_for('home'))

# ── Public face recognition (no login required) ───────────────────────────────
@app.route('/process_face_recognition', methods=['POST'])
def process_face_recognition():
    """Identifies any enrolled student from a camera frame."""
    image_data_url = request.form.get('image_data', '')
    if not image_data_url or 'base64,' not in image_data_url:
        return jsonify({'status': 'error', 'message': 'Invalid image data.'}), 400

    _, b64 = image_data_url.split(',', 1)
    image_bytes = base64.b64decode(b64)

    try:
        img    = Image.open(BytesIO(image_bytes)).convert('RGB')
        img_np = np.array(img)

        face_locations = face_recognition.face_locations(img_np)
        face_encodings = face_recognition.face_encodings(img_np, face_locations)

        if not face_encodings:
            return jsonify({'status': 'info', 'message': 'No face detected. Please face the camera directly.'})

        if not known_face_encodings:
            return jsonify({'status': 'error', 'message': 'No students enrolled yet. Ask admin to add students first.'}), 404

        matches = face_recognition.compare_faces(known_face_encodings, face_encodings[0], tolerance=0.5)
        if True not in matches:
            return jsonify({'status': 'error', 'message': 'Face not recognized. Ask admin to enroll your photo.'}), 404

        # Best match
        distances  = face_recognition.face_distance(known_face_encodings, face_encodings[0])
        best_idx   = int(distances.argmin())
        name       = known_face_names[best_idx]
        meta       = known_face_meta.get(name, {})
        roll_num   = meta.get('roll_number', '')
        username_f = meta.get('username', known_face_usernames.get(name))

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db_connection()

        # 5-minute duplicate cooldown
        last = conn.execute(
            "SELECT timestamp FROM attendance WHERE username=? ORDER BY timestamp DESC LIMIT 1",
            (username_f,)
        ).fetchone()
        if last:
            delta = (datetime.now() - datetime.strptime(last['timestamp'], "%Y-%m-%d %H:%M:%S")).total_seconds()
            if delta < 300:
                conn.close()
                return jsonify({
                    'status': 'info',
                    'message': f'Attendance already recorded for {name} recently.',
                    'name': name,
                    'roll_number': roll_num,
                    'timestamp': last['timestamp']
                })

        conn.execute(
            "INSERT INTO attendance (display_name, username, roll_number, timestamp) VALUES (?,?,?,?)",
            (name, username_f, roll_num, current_time)
        )
        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'message': f'Attendance marked for {name}!',
            'name': name,
            'roll_number': roll_num,
            'timestamp': current_time
        })

    except Exception as e:
        app.logger.error(f"Face recognition error: {e}")
        return jsonify({'status': 'error', 'message': f'Server error: {e}'}), 500

# ── Public attendance stats ───────────────────────────────────────────────────
@app.route('/get_attendance_data')
def get_attendance_data():
    """Returns today's attendance count and recent records (public)."""
    conn    = get_db_connection()
    records = conn.execute(
        "SELECT display_name, roll_number, timestamp FROM attendance ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()

    counts_by_date = {}
    today      = datetime.now().strftime("%Y-%m-%d")
    today_cnt  = 0
    last_ts    = None
    recent     = []

    for i, r in enumerate(records):
        ts = r['timestamp']
        if i == 0: last_ts = ts
        try:
            d = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
            counts_by_date[d] = counts_by_date.get(d, 0) + 1
            if d == today: today_cnt += 1
        except Exception: pass
        if i < 10:
            recent.append({'name': r['display_name'], 'roll': r['roll_number'] or '',
                           'time': ts.split(' ')[1] if ts and ' ' in ts else ts})

    dates  = sorted(counts_by_date.keys())
    counts = [counts_by_date[d] for d in dates]
    return jsonify({'status':'success', 'dates':dates, 'counts':counts,
                    'today_count':today_cnt, 'last_timestamp':last_ts, 'recent':recent})

# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ROUTES (login required)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    return render_template('admin_dashboard.html', new_users=[])

@app.route('/manage_students')
@login_required
def manage_students():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    return render_template('manage_students.html')

@app.route('/admin_register', methods=['GET', 'POST'])
@login_required
def admin_register():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        username     = request.form['username']
        display_name = request.form['display_name']
        password     = request.form['password']
        role         = request.form.get('role', 'admin')
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (username,password,display_name,role) VALUES (?,?,?,?)",
                         (username, generate_password_hash(password), display_name, role))
            conn.commit()
            flash(f'User "{username}" created successfully!', 'success')
            return redirect(url_for('admin_dashboard'))
        except sqlite3.IntegrityError:
            flash('Username already exists.', 'error')
        finally:
            conn.close()
    return render_template('admin_register.html')

# ── Admin: Add student (upload photo) ─────────────────────────────────────────
@app.route('/admin_add_student', methods=['POST'])
@login_required
def admin_add_student():
    if session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Access denied.'}), 403

    student_name   = request.form.get('student_name', '').strip()
    roll_number    = request.form.get('roll_number', '').strip()
    image_data_url = request.form.get('image_data', '')

    if not student_name or not roll_number:
        return jsonify({'status': 'error', 'message': 'Name and roll number required.'}), 400
    if not image_data_url or 'base64,' not in image_data_url:
        return jsonify({'status': 'error', 'message': 'Please provide a photo.'}), 400

    try:
        _, b64 = image_data_url.split(',', 1)
        img    = Image.open(BytesIO(base64.b64decode(b64))).convert('RGB')
        img_np = np.array(img)

        locs = face_recognition.face_locations(img_np)
        if len(locs) == 0:
            return jsonify({'status': 'error', 'message': 'No face found. Use a clear frontal photo.'}), 400
        if len(locs) > 1:
            return jsonify({'status': 'error', 'message': 'Multiple faces found. Use a single-person photo.'}), 400

        enc      = face_recognition.face_encodings(img_np, locs)[0].tobytes()
        ukey     = f"student_{roll_number.replace(' ','_').lower()}"
        conn     = get_db_connection()

        try:
            existing = conn.execute("SELECT username FROM users WHERE roll_number=?", (roll_number,)).fetchone()
            if existing:
                conn.execute("UPDATE users SET face_encoding=?, display_name=? WHERE roll_number=?",
                             (enc, student_name, roll_number))
                msg = f"Updated face for {student_name} (Roll: {roll_number})"
            else:
                dummy_pw = generate_password_hash(secrets.token_hex(16))
                conn.execute(
                    "INSERT INTO users (username,password,display_name,role,roll_number,face_encoding,is_new) VALUES (?,?,?,?,?,?,?)",
                    (ukey, dummy_pw, student_name, 'student', roll_number, enc, 0)
                )
                msg = f"Student {student_name} (Roll: {roll_number}) enrolled!"
            conn.commit()

            safe = ''.join(c for c in student_name if c.isalnum() or c == ' ').strip().replace(' ','_')
            img.save(os.path.join(ENROLLED_FACES_DIR, f"{safe}_{roll_number.replace('/','_')}.png"))
            load_known_faces_from_db()
            return jsonify({'status': 'success', 'message': msg})

        except sqlite3.IntegrityError:
            ukey2 = f"{ukey}_{secrets.token_hex(3)}"
            dummy_pw = generate_password_hash(secrets.token_hex(16))
            conn.execute(
                "INSERT INTO users (username,password,display_name,role,roll_number,face_encoding,is_new) VALUES (?,?,?,?,?,?,?)",
                (ukey2, dummy_pw, student_name, 'student', roll_number, enc, 0)
            )
            conn.commit()
            load_known_faces_from_db()
            return jsonify({'status': 'success', 'message': f'Enrolled {student_name} (Roll: {roll_number})'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'DB error: {e}'}), 500
        finally:
            conn.close()

    except Exception as e:
        app.logger.error(f"admin_add_student: {e}")
        return jsonify({'status': 'error', 'message': f'Server error: {e}'}), 500

@app.route('/get_enrolled_students')
@login_required
def get_enrolled_students():
    if session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Access denied.'}), 403
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT display_name, roll_number, username FROM users WHERE role='student' ORDER BY display_name"
    ).fetchall()
    conn.close()
    return jsonify({'status':'success', 'students':[
        {'name':r['display_name'], 'roll':r['roll_number'], 'username':r['username'], 'enrolled':True}
        for r in rows
    ]})

@app.route('/delete_student', methods=['POST'])
@login_required
def delete_student():
    if session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Access denied.'}), 403
    username = request.form.get('username', '')
    if not username or username == 'admin':
        return jsonify({'status': 'error', 'message': 'Invalid.'}), 400
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM users WHERE username=? AND role='student'", (username,))
        conn.commit()
        load_known_faces_from_db()
        return jsonify({'status': 'success', 'message': 'Student deleted.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

# ── Old enroll route (kept for compatibility) ─────────────────────────────────
@app.route('/enroll', methods=['GET', 'POST'])
@login_required
def enroll():
    if session.get('role') != 'admin':
        return redirect(url_for('home'))
    return redirect(url_for('manage_students'))

# ── Attendance records ────────────────────────────────────────────────────────
@app.route('/attendance')
@login_required
def attendance():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT display_name, username, roll_number, timestamp FROM attendance ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    records = [(r['display_name'], r['username'], r['roll_number'] or '', r['timestamp']) for r in rows]
    return render_template('attendance.html', records=records)

# ── CSV export ────────────────────────────────────────────────────────────────
@app.route('/download_attendance_csv')
@login_required
def download_attendance_csv():
    conn   = get_db_connection()
    rows   = conn.execute(
        "SELECT display_name, roll_number, username, timestamp FROM attendance ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()

    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(['Name', 'Roll Number', 'Username', 'Timestamp'])
    for r in rows:
        w.writerow([r['display_name'], r['roll_number'] or '', r['username'], r['timestamp']])
    out.seek(0)
    return Response(out, mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=attendance_log.csv"})

# ── Password reset ────────────────────────────────────────────────────────────
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form['username']
        conn = get_db_connection()
        user = conn.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user:
            flash('Proceed to reset your password.', 'info')
            return redirect(url_for('reset_password_username', username=username))
        flash('Username not found.', 'error')
    return render_template('forgot_password.html')

@app.route('/reset_password/<username>', methods=['GET', 'POST'])
def reset_password_username(username):
    conn = get_db_connection()
    user = conn.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not user:
        flash('Invalid link.', 'error')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        pw = generate_password_hash(request.form['new_password'])
        conn = get_db_connection()
        conn.execute("UPDATE users SET password=? WHERE username=?", (pw, username))
        conn.commit()
        conn.close()
        flash('Password reset successfully.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', username=username)

@app.route('/mark_user_reviewed', methods=['POST'])
@login_required
def mark_user_reviewed():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    username = request.form.get('username')
    conn = get_db_connection()
    conn.execute("UPDATE users SET is_new=0 WHERE username=?", (username,))
    conn.commit()
    conn.close()
    flash(f"User '{username}' marked as reviewed.", 'success')
    return redirect(url_for('admin_dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
