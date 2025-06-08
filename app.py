import os
import shutil
import csv
import io
import json
import uuid
import math
from datetime import datetime
from functools import wraps

# Third-party imports
from flask import (Flask, render_template, request, jsonify, session,
                   redirect, url_for, send_from_directory, send_file)
from markupsafe import Markup
from openpyxl import Workbook

# --- Application Setup ---
app = Flask(__name__)

# --- Configuration ---
# Use a persistent disk mount path for all user data
# This is CRITICAL for production hosting
DATA_DIR = os.environ.get('RENDER_DATA_DIR', '.') # Use /data on Render, local dir otherwise

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-very-secret-key-for-dev')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
app.config['USERS_FILE'] = os.path.join(DATA_DIR, "users.txt")
app.config['NOTES_DIR'] = os.path.join(DATA_DIR, "user_notes")
app.config['UPLOADS_DIR'] = os.path.join(DATA_DIR, "user_uploads")

# --- Directory Initialization ---
# Ensure necessary directories exist on startup
for directory_key in ['NOTES_DIR', 'UPLOADS_DIR']:
    dir_path = app.config[directory_key]
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

# --- Jinja Filters for Frontend Rendering ---

@app.template_filter('format_bytes')
def format_bytes_filter(size, decimals=2):
    """Jinja filter to format file size into a human-readable string."""
    if not isinstance(size, (int, float)) or size == 0:
        return "0 Bytes"
    k = 1024
    dm = max(0, decimals)
    sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
    i = int(math.floor(math.log(size, k))) if size > 0 else 0
    return f"{round(size / (k ** i), dm)} {sizes[i]}"

@app.template_filter('file_icon')
def get_file_icon_svg_filter(filename):
    """Jinja filter to return an appropriate SVG icon for a file type."""
    # Using a dictionary for icon mapping is clean and scalable
    file_icons = {
    '_default': '<svg class="attachment-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>',
    'image': '<svg class="attachment-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>',
    'doc': '<svg class="attachment-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>',
    'pdf': '<svg class="attachment-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><line x1="10" y1="9" x2="8" y2="9"></line></svg>',
    }
 # Note: I've truncated the SVG strings for readability. Use your original SVGs here.

    extension = filename.split('.')[-1].lower() if '.' in filename else ''
    if extension in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg']:
        return Markup(file_icons['image'])
    if extension == 'pdf':
        return Markup(file_icons['pdf'])
    if extension in ['doc', 'docx', 'txt', 'rtf']:
        return Markup(file_icons['doc'])
    return Markup(file_icons['_default'])

# --- Internal Helper Functions ---

def _get_user_notes_path(username):
    """Returns the full path to a user's notes file."""
    return os.path.join(app.config['NOTES_DIR'], f"{username}.jsonl")

def _get_user_uploads_path(username):
    """Returns the full path to a user's upload directory."""
    return os.path.join(app.config['UPLOADS_DIR'], username)

def _load_all_users():
    """Reads all users from the users file into a dict."""
    # NOTE: Storing passwords in plain text is insecure.
    # For a real application, use a secure password hashing library like Werkzeug or Passlib.
    creds = {}
    users_file = app.config['USERS_FILE']
    if not os.path.exists(users_file):
        return {}
    with open(users_file, 'r') as f:
        for line in f:
            if ':' in line:
                user, pwd = line.strip().split(':', 1)
                creds[user] = pwd
    return creds

def _save_new_user(username, password):
    """Saves a new user to the users file."""
    with open(app.config['USERS_FILE'], 'a') as f:
        f.write(f"{username}:{password}\n")

def get_notes_for_user(username):
    """Reads and sorts all notes for a given user."""
    notes_file = _get_user_notes_path(username)
    if not os.path.exists(notes_file):
        return []
    notes = []
    with open(notes_file, 'r') as f:
        for line in f:
            try:
                notes.append(json.loads(line))
            except json.JSONDecodeError:
                # Silently skip corrupted lines to prevent app crashes
                continue
    notes.sort(key=lambda x: x['timestamp'])
    return notes

def write_all_notes_for_user(username, notes):
    """Rewrites the entire notes file for a user (for edit/delete operations)."""
    notes_file = _get_user_notes_path(username)
    with open(notes_file, 'w') as f:
        for note in notes:
            f.write(json.dumps(note) + '\n')

# --- Decorator for Authentication ---

def login_required(f):
    """Decorator to ensure a user is logged in before accessing a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Authentication Routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        users = _load_all_users()

        if username in users:
            # Existing user login
            if users[username] == password:
                session['username'] = username
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error='Invalid password.')
        else:
            # New user registration on first login
            _save_new_user(username, password)
            session['username'] = username
            return redirect(url_for('index'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# --- File Handling Routes ---

@app.route('/upload_file', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files or not request.files['file'].filename:
        return jsonify({"status": "error", "message": "No file selected"}), 400

    file = request.files['file']
    original_filename = secure_filename(file.filename)
    file_extension = os.path.splitext(original_filename)[1]
    unique_filename = f"{uuid.uuid4().hex}{file_extension}"

    user_upload_path = _get_user_uploads_path(session['username'])
    os.makedirs(user_upload_path, exist_ok=True) # Ensure directory exists

    filepath = os.path.join(user_upload_path, unique_filename)
    file.save(filepath)
    file_size = os.path.getsize(filepath)

    return jsonify({
        "status": "success",
        "original_name": original_filename,
        "stored_name": unique_filename,
        "size": file_size
    })

@app.route('/files/<username>/<filename>')
@login_required
def download_file(username, filename):
    # Security check: users can only download their own files
    if username != session['username']:
        return "Forbidden", 403

    user_upload_path = _get_user_uploads_path(username)
    return send_from_directory(user_upload_path, filename, as_attachment=True)

# --- Main Application Routes ---

@app.route('/')
@login_required
def index():
    notes = get_notes_for_user(session['username'])
    return render_template('index.html', username=session['username'], notes=notes)

@app.route('/get_notes')
@login_required
def get_notes_api():
    """API endpoint to fetch notes, useful for dynamic frontend updates."""
    notes = get_notes_for_user(session['username'])
    return jsonify(notes)

@app.route('/add_note', methods=['POST'])
@login_required
def add_note():
    data = request.json
    note_text = data.get('note', '')
    attachment_data = data.get('attachment')

    if not note_text and not attachment_data:
        return jsonify({"status": "error", "message": "Note cannot be empty"}), 400

    new_note = {
        "text": note_text,
        "timestamp": datetime.utcnow().isoformat() + "Z", # UTC 'Z' for standard
        "attachment": attachment_data
    }

    notes_file = _get_user_notes_path(session['username'])
    with open(notes_file, 'a') as f:
        f.write(json.dumps(new_note) + '\n')

    return jsonify({"status": "success", "note": new_note})

@app.route('/edit_note', methods=['POST'])
@login_required
def edit_note():
    data = request.json
    timestamp_to_edit = data.get('timestamp')
    new_text = data.get('new_text')
    username = session['username']

    notes = get_notes_for_user(username)
    note_found = False
    for note in notes:
        if note['timestamp'] == timestamp_to_edit:
            note['text'] = new_text
            note_found = True
            break

    if note_found:
        write_all_notes_for_user(username, notes)
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Note not found"}), 404

@app.route('/delete_note', methods=['POST'])
@login_required
def delete_note():
    timestamp_to_delete = request.json.get('timestamp')
    username = session['username']
    notes = get_notes_for_user(username)

    note_to_delete = next((n for n in notes if n['timestamp'] == timestamp_to_delete), None)

    if not note_to_delete:
        return jsonify({"status": "error", "message": "Note not found"}), 404

    # Delete the associated physical file if it exists
    if note_to_delete.get('attachment'):
        stored_name = note_to_delete['attachment'].get('stored_name')
        if stored_name:
            file_path = os.path.join(_get_user_uploads_path(username), stored_name)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    # The file couldn't be deleted, but we still proceed to delete
                    # the note record. Log this for debugging.
                    print(f"Error deleting file {file_path}: {e}")

    # Remove the note record and rewrite the file
    notes_after_deletion = [n for n in notes if n['timestamp'] != timestamp_to_delete]
    write_all_notes_for_user(username, notes_after_deletion)

    return jsonify({"status": "success"})

@app.route('/clear_notes', methods=['POST'])
@login_required
def clear_notes():
    password = request.json.get('password')
    users = _load_all_users()
    username = session['username']

    # Verify password before destructive action
    if users.get(username) == password:
        notes_file = _get_user_notes_path(username)
        if os.path.exists(notes_file):
            os.remove(notes_file)

        user_upload_dir = _get_user_uploads_path(username)
        if os.path.exists(user_upload_dir):
            shutil.rmtree(user_upload_dir)

        return jsonify({"status": "success"})
    else:
        return jsonify({"status": "error", "message": "Invalid password"}), 403

# --- Export Routes ---

def _format_notes_for_export(notes_data):
    """Helper to consistently format notes for various export types."""
    formatted_notes = []
    for note in notes_data:
        try:
            dt_object = datetime.fromisoformat(note['timestamp'].replace('Z', '+00:00'))
            timestamp_prefix = dt_object.strftime('%Y-%m-%d %H:%M:%S')
            note_text = f"{timestamp_prefix} - {note['text']}"
            if note.get('attachment'):
                note_text += f" [Attachment: {note['attachment']['original_name']}]"
            formatted_notes.append(note_text)
        except (ValueError, KeyError):
            # Skip malformed notes
            continue
    return formatted_notes

@app.route('/export/<filetype>')
@login_required
def export(filetype):
    """Dispatcher route for exporting notes to different file formats."""
    notes_data = get_notes_for_user(session['username'])
    formatted_notes = _format_notes_for_export(notes_data)
    username = session['username']
    
    if filetype == 'txt':
        output = '\n\n'.join(formatted_notes)
        return send_file(io.BytesIO(output.encode('utf-8')),
                         mimetype='text/plain', as_attachment=True,
                         download_name=f'{username}_notes.txt')

    elif filetype == 'csv':
        mem_file = io.StringIO()
        writer = csv.writer(mem_file)
        writer.writerow(['Note'])
        for note in formatted_notes:
            writer.writerow([note])
        mem_file.seek(0)
        return send_file(io.BytesIO(mem_file.read().encode('utf-8')),
                         mimetype='text/csv', as_attachment=True,
                         download_name=f'{username}_notes.csv')

    elif filetype == 'xlsx':
        wb = Workbook()
        ws = wb.active
        ws.title = "Notes"
        ws.append(['Note'])
        for note in formatted_notes:
            ws.append([note])
        mem_file = io.BytesIO()
        wb.save(mem_file)
        mem_file.seek(0)
        return send_file(mem_file,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name=f'{username}_notes.xlsx')

    return "Invalid file type", 400

# The if __name__ == '__main__': block is not needed for PythonAnywhere deployment,
# as it uses a different method to run the app. You can either remove it entirely
# or leave it as-is for local testing. It will not be executed on the server.

# if __name__ == '__main__':
#   app.run(host="0.0.0.0", port=3000, debug=True)
