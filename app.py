from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
import os, json, sqlite3, base64
from datetime import datetime
from io import BytesIO
from PIL import Image
import face_recognition
import numpy as np
import secrets # For generating secure tokens
from functools import wraps
import csv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
import io



app = Flask(__name__)
# IMPORTANT: Get secret key from an environment variable for security in production!
# For development, a fallback is provided.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_super_secret_and_long_key_for_sessions_12345_CHANGE_ME_IN_PROD')

DATABASE_NAME = 'attendance.db'
# Define the directory to store enrolled face images
ENROLLED_FACES_DIR = 'known_faces' # This will create 'known_faces' in the app's root directory

# Global variables for face recognition (initialized after DB init)
known_face_encodings = []
known_face_names = []
known_face_usernames = {} # Maps display_name to username

# Helper function to get database connection
def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row # This allows access to columns by name
    return conn

# Decorator to check if user is logged in
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session['logged_in']:
            flash('You need to be logged in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Initialize database (create tables and admin user if not exists)
def init_db():
    """Initializes the SQLite database, creating tables and a default admin user if they don't exist."""
    with get_db_connection() as conn:
        c = conn.cursor()
        # Create users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                face_encoding BLOB
            )
        ''')
        # Create attendance table
        c.execute('''
            CREATE TABLE IF NOT EXISTS attendance (
                display_name TEXT,
                username TEXT,
                timestamp TEXT
            )
        ''')
        # Check for admin user and create if not exists
        c.execute("SELECT * FROM users WHERE username = ?", ('admin',))
        admin_exists = c.fetchone()
        if not admin_exists:
            hashed_password = generate_password_hash('admin123') # Default admin password
            c.execute("INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, ?)",
                      ('admin', hashed_password, 'Admin User', 'admin'))
            conn.commit()
            print("Admin user created (username: admin, password: admin123)")
    
    # Load known faces from database after tables are ensured
    load_known_faces_from_db()

def load_known_faces_from_db():
    """Loads all known face encodings and their corresponding names/usernames from the database."""
    global known_face_encodings, known_face_names, known_face_usernames
    known_face_encodings = []
    known_face_names = []
    known_face_usernames = {}
    
    conn = get_db_connection()
    users = conn.execute("SELECT username, display_name, face_encoding FROM users WHERE face_encoding IS NOT NULL").fetchall()
    conn.close()

    if not users:
        print("No known faces loaded from database.")
        return

    for user in users:
        try:
            username = user['username']
            display_name = user['display_name']
            encoding_blob = user['face_encoding']

            # Convert blob back to numpy array
            face_encoding = np.frombuffer(encoding_blob, dtype=np.float64)
            if face_encoding.size == 128: # Ensure it's a 128-dimension encoding
                known_face_encodings.append(face_encoding)
                known_face_names.append(display_name)
                known_face_usernames[display_name] = username
                print(f"Loaded face for {display_name} (Username: {username})")
            else:
                print(f"Skipping {display_name}: Invalid face encoding size ({face_encoding.size})")
        except Exception as e:
            print(f"Error loading face encoding for {user.get('display_name', 'N/A')}: {e}")
    print(f"Loaded {len(known_face_encodings)} known faces from database.")

# Ensure the 'known_faces' directory exists at the app's root level
if not os.path.exists(ENROLLED_FACES_DIR):
    os.makedirs(ENROLLED_FACES_DIR)
    print(f"Created directory: {ENROLLED_FACES_DIR}")

# --- Routes ---

@app.route('/')
def home():
    """Renders the home page."""
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Handles user registration."""
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        display_name = request.form['display_name']
        password = request.form['password']
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (username, password, display_name, role, is_new) VALUES (?, ?, ?, ?, ?)",
             (username, hashed_password, display_name, 'student', 1))
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists. Please choose a different one.', 'error')
        finally:
            conn.close()
    return render_template('register.html')

@app.route('/admin_register', methods=['GET', 'POST'])
@login_required
def admin_register():
    """Allows an admin to register new users (students or admins)."""
    if session.get('role') != 'admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username']
        display_name = request.form['display_name']
        password = request.form['password']
        role = request.form.get('role', 'student') # Default to student if not provided
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, ?)",
                         (username, hashed_password, display_name, role))
            conn.commit()
            flash(f'User "{username}" registered successfully with role "{role}"!', 'success')
            return redirect(url_for('admin_dashboard'))
        except sqlite3.IntegrityError:
            flash('Username already exists. Please choose a different one.', 'error')
        finally:
            conn.close()
    return render_template('admin_register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    if session.get('logged_in'):
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['logged_in'] = True
            session['username'] = user['username']
            session['display_name'] = user['display_name']
            session['role'] = user['role']
            flash('Logged in successfully!', 'success')
            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Logs out the current user."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    """Renders the student dashboard."""
    if session.get('role') == 'admin':
        flash('Access denied. Please log in as a student.', 'error')
        return redirect(url_for('login'))
    return render_template('dashboard.html', username=session['username'])

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    if session.get('role') != 'admin':
        flash('Unauthorized access. Please log in as an admin.', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    new_users = conn.execute("SELECT username, display_name FROM users WHERE is_new = 1 AND role = 'student'").fetchall()
    conn.close()
    
    return render_template('admin_dashboard.html', new_users=new_users)

@app.route('/mark_user_reviewed', methods=['POST'])
@login_required
def mark_user_reviewed():
    if session.get('role') != 'admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    username = request.form.get('username')
    conn = get_db_connection()
    conn.execute("UPDATE users SET is_new = 0 WHERE username = ?", (username,))
    conn.commit()
    conn.close()

    flash(f"User '{username}' marked as reviewed.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/enroll', methods=['GET', 'POST'])
@login_required
def enroll():
    """Handles face enrollment for users by an admin. Stores face encoding in DB and image in 'known_faces' folder."""
    if session.get('role') != 'admin':
        # For non-admin POST requests, return JSON error
        if request.method == 'POST':
            return jsonify({'status': 'error', 'message': 'Access denied. Only admins can enroll faces.'}), 403
        # For non-admin GET requests, redirect with flash
        flash('Access denied. Only admins can enroll faces.', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        image_data_url = request.form.get('image_data') # Use .get() to prevent KeyError
        admin_password = request.form.get('admin_password')

        # Basic validation for required fields
        if not username or not image_data_url or not admin_password:
            return jsonify({'status': 'error', 'message': 'Missing form data. All fields are required.'}), 400

        conn = get_db_connection()
        try:
            # Verify admin's password before proceeding
            admin_user = conn.execute("SELECT password FROM users WHERE username = ? AND role = 'admin'", (session['username'],)).fetchone()
            if not admin_user or not check_password_hash(admin_user['password'], admin_password):
                return jsonify({'status': 'error', 'message': 'Incorrect admin password.'}), 401

            # Verify the target username exists
            user_to_enroll = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not user_to_enroll:
                return jsonify({'status': 'error', 'message': 'User does not exist. Please register the user first.'}), 404
                
            # Decode base64 image data (e.g., "data:image/jpeg;base64,...")
            if 'base64,' not in image_data_url:
                return jsonify({'status': 'error', 'message': 'Invalid image data format. Expected base64 URL.'}), 400
                
            _, base64_data = image_data_url.split(',', 1)
            image_bytes = base64.b64decode(base64_data)

            img = Image.open(BytesIO(image_bytes))
            img = img.convert('RGB') # Ensure image is in RGB format for face_recognition
            img_np = np.array(img)

            face_locations = face_recognition.face_locations(img_np)
            if len(face_locations) == 0:
                return jsonify({'status': 'error', 'message': 'No face found in the uploaded image. Please try again.'}), 400
            elif len(face_locations) > 1:
                return jsonify({'status': 'error', 'message': 'Multiple faces found. Please upload an image with only one face.'}), 400
            else:
                face_encoding = face_recognition.face_encodings(img_np, face_locations)[0]
                # Convert numpy array to bytes for storage in SQLite BLOB
                face_encoding_blob = face_encoding.tobytes()

                # Get display_name for the image filename and for the database update
                display_name = user_to_enroll['display_name'] # Use the display_name from the database

                conn.execute("UPDATE users SET face_encoding = ?, display_name = ? WHERE username = ?",
                             (face_encoding_blob, display_name, username))
                conn.commit()

                # --- NEW ADDITION: Save the image file to the file system ---
                # Create a safe filename using display_name and username to ensure uniqueness
                # Replace non-alphanumeric characters with underscores for a valid filename
                safe_display_name = ''.join(c for c in display_name if c.isalnum() or c.isspace()).strip().replace(' ', '_')
                image_filename = f"{safe_display_name}_{username}.png" # Using .png as it's common for data URLs
                image_path = os.path.join(ENROLLED_FACES_DIR, image_filename)

                # Save the image to the designated folder
                img.save(image_path)
                
                # Reload known faces into memory after a new face is enrolled
                load_known_faces_from_db()
                return jsonify({'status': 'success', 'message': f'Face for {display_name} (Username: {username}) enrolled and image stored successfully!'}), 200
        except Exception as e:
            app.logger.error(f"Error during enrollment: {e}")
            return jsonify({'status': 'error', 'message': f'Server error during enrollment: {e}'}), 500
        finally:
            conn.close() # Ensure connection is closed even if errors occur
    
    # For GET requests, render the enrollment HTML page
    return render_template('enroll.html')

@app.route('/process_face_recognition', methods=['POST'])
@login_required
def process_face_recognition():
    """Processes an image from the camera for face recognition and attendance marking."""
    if session.get('role') == 'admin':
        return jsonify({'status': 'error', 'message': 'Admins cannot mark attendance from dashboard. Use admin panel.'}), 403

    image_data_url = request.form.get('image_data')
    
    if not image_data_url or 'base64,' not in image_data_url:
        return jsonify({'status': 'error', 'message': 'Invalid image data format. Expected base64 URL.'}), 400

    # Extract base64 part
    _, base64_data = image_data_url.split(',', 1)
    image_bytes = base64.b64decode(base64_data)

    try:
        img = Image.open(BytesIO(image_bytes))
        img = img.convert('RGB')
        img_np = np.array(img)

        face_locations = face_recognition.face_locations(img_np)
        face_encodings = face_recognition.face_encodings(img_np, face_locations)

        if not face_encodings:
            return jsonify({'status': 'info', 'message': 'No face detected in the image.'})
        
        # Take the first detected face for a single user attendance marking
        user_face_encoding = face_encodings[0] 

        # Compare with known faces, adjust tolerance if needed
        # Lower tolerance means stricter match (e.g., 0.4), higher means more relaxed (e.g., 0.6)
        matches = face_recognition.compare_faces(known_face_encodings, user_face_encoding, tolerance=0.5) 
        
        name = "Unknown"
        username_found = None

        if True in matches:
            first_match_index = matches.index(True)
            name = known_face_names[first_match_index]
            username_found = known_face_usernames.get(name)

            if username_found == session['username']:
                # Record attendance
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = get_db_connection()
                # Check if attendance has already been marked recently for this user
                # This prevents multiple entries for a single recognition
                last_attendance = conn.execute("SELECT timestamp FROM attendance WHERE username = ? ORDER BY timestamp DESC LIMIT 1", (username_found,)).fetchone()
                
                # Consider adding a time window (e.g., 1 minute) to prevent duplicate entries within that period
                if last_attendance:
                    last_time = datetime.strptime(last_attendance['timestamp'], "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - last_time).total_seconds() < 60: # 60 seconds
                        conn.close()
                        return jsonify({
                            'status': 'info',
                            'message': f'Attendance already recorded for {name} recently.',
                            'name': name,
                            'timestamp': last_attendance['timestamp'] # Return the existing timestamp
                        })

                conn.execute("INSERT INTO attendance (display_name, username, timestamp) VALUES (?, ?, ?)",
                             (name, username_found, current_time))
                conn.commit()
                conn.close()
                return jsonify({
                    'status': 'success',
                    'message': f'Attendance recorded for {name}!',
                    'name': name,
                    'timestamp': current_time
                })
            else:
                return jsonify({'status': 'error', 'message': 'Recognized as a different user. Please ensure your face matches your logged-in account.'}), 401
        else:
            return jsonify({'status': 'error', 'message': 'Face not recognized. Please try again or enroll your face if you are a new user.'}), 404

    except Exception as e:
        app.logger.error(f"Error during face recognition: {e}")
        return jsonify({'status': 'error', 'message': f'Server error during recognition: {e}'}), 500

@app.route('/attendance')
@login_required
def attendance():
    """Displays attendance records for the current user or all users if admin."""
    conn = get_db_connection()
    if session.get('role') == 'admin':
        records = conn.execute("SELECT display_name, timestamp FROM attendance ORDER BY timestamp DESC").fetchall()
    else:
        records = conn.execute("SELECT display_name, timestamp FROM attendance WHERE username = ? ORDER BY timestamp DESC", (session['username'],)).fetchall()
    conn.close()

    # Format records for display
    formatted_records = [(row['display_name'], row['timestamp']) for row in records]
    return render_template('attendance.html', records=formatted_records)

@app.route('/get_attendance_data')
@login_required
def get_attendance_data():
    """Provides attendance data for Chart.js on the dashboard."""
    if session.get('role') == 'admin':
        # Admins don't get individual dashboard chart data
        return jsonify({'status': 'error', 'message': 'Access denied'}), 403

    username = session['username']
    conn = get_db_connection()
    records = conn.execute("SELECT timestamp FROM attendance WHERE username = ?", (username,)).fetchall()
    conn.close()

    # Process data for chart: count attendance per day
    attendance_counts = {}
    for record in records:
        date = datetime.strptime(record['timestamp'], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        attendance_counts[date] = attendance_counts.get(date, 0) + 1
    
    # Sort dates and prepare for chart.js
    dates = sorted(attendance_counts.keys())
    counts = [attendance_counts[date] for date in dates]

    return jsonify({
        'status': 'success',
        'dates': dates,
        'counts': counts
    })

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    """Handles forgotten password requests."""
    if request.method == 'POST':
        username = request.form['username']
        conn = get_db_connection()
        user = conn.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user:
            # In a real application, you would generate a unique token,
            # store it with an expiry in the database for the user,
            # and email a reset link like:
            # url_for('reset_password', token=generated_token, _external=True)
            # For this demo, we'll just redirect to a simplified reset page for demonstration.
            flash(f'If an account with {username} exists, you can proceed to reset_password.', 'info')
            return redirect(url_for('reset_password_username', username=username))
        else:
            flash('Username not found.', 'error')
    return render_template('forgot_password.html')

@app.route('/reset_password/<username>', methods=['GET', 'POST'])
def reset_password_username(username):
    """Allows a user to reset their password (simplified for demo)."""
    # In a real application, this route would first verify a token
    # passed in the URL (e.g., /reset_password?token=XYZ) before allowing reset.
    # For this demo, we're directly using the username (INSECURE!).
    conn = get_db_connection()
    user = conn.execute("SELECT username FROM users WHERE username = ? ", (username,)).fetchone()
    conn.close()

    if not user:
        flash('Invalid reset link or username.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_password = request.form['new_password']
        hashed_password = generate_password_hash(new_password)

        conn = get_db_connection()
        conn.execute("UPDATE users SET password = ? WHERE username = ?", (hashed_password, username))
        conn.commit()
        conn.close()
        flash('Your password has been reset successfully. Please log in with your new password.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', username=username)

@app.route('/download_attendance_csv')
@login_required
def download_attendance_csv():
    import csv
    import io
    from flask import Response

    conn = get_db_connection()
    cursor = conn.cursor()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Display Name', 'Username', 'Timestamp'])  # CSV Header

    if session.get('role') == 'admin':
        # Admin: get all records
        cursor.execute("SELECT display_name, username, timestamp FROM attendance ORDER BY timestamp DESC")
    else:
        # Student: get only their own records
        cursor.execute("SELECT display_name, username, timestamp FROM attendance WHERE username = ? ORDER BY timestamp DESC", (session['username'],))

    rows = cursor.fetchall()

    for row in rows:
        writer.writerow([row['display_name'], row['username'], row['timestamp']])

    conn.close()
    output.seek(0)

    return Response(
        output,
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment;filename=attendance_log.csv"}
    )

if __name__ == '__main__':
    # Initialize database when the application starts
    init_db()
    # Run the Flask application
    app.run(debug=True)
