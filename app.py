from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
import psycopg2
import psycopg2.extras
import os
import io
import re
import requests
from functools import wraps
from PIL import Image, UnidentifiedImageError
from itsdangerous import URLSafeTimedSerializer
from init_db import init_db
from admin_routes import register_admin_routes
from admin_utils import is_admin
from premium_routes import register_premium_routes
from premium_utils import get_login_retention_reminder
from ai.question_generator import _question_from_row

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import smtplib
import uuid
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Load environment variables
# Check for key.env first, then fallback to .env
if os.path.exists('key.env'):
    load_dotenv('key.env')
else:
    load_dotenv()

app = Flask(__name__)
_configured_secret = os.getenv('SECRET_KEY')
app.secret_key = _configured_secret or secrets.token_hex(32)
serializer = URLSafeTimedSerializer(app.secret_key)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', '0').strip().lower() in {'1', 'true', 'yes'},
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
if not _configured_secret:
    app.logger.warning('SECRET_KEY is not configured; sessions will reset when the process restarts.')

# Initialize database
init_db()

# Register admin routes
register_admin_routes(app)

# Register premium routes
register_premium_routes(app)

# Upload configuration
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)

def allowed_file(filename):
    return bool(filename and '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS)


def _clean_text(value, max_length):
    return str(value or '').strip()[:max_length]


def _valid_email(value):
    email = _clean_text(value, 254).lower()
    return email if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) else None


def _bounded_int(value, default, minimum, maximum):
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _safe_local_target(value, fallback='index'):
    target = str(value or '').strip()
    if target.startswith('/') and not target.startswith('//'):
        return target
    return url_for(fallback)


def _paid_user(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM payments WHERE user_id = %s AND status = 'paid' LIMIT 1", (user_id,))
        return cursor.fetchone() is not None
    finally:
        if conn:
            conn.close()


# Paystack Configuration

PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY')
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY')
PAYMENT_AMOUNT_KOBO = _bounded_int(os.getenv('PAYMENT_AMOUNT_KOBO'), 50000, 1, 100000000)

# Email Configuration
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USERNAME = os.getenv('SMTP_USERNAME')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')

# Database URL
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    """Create a database connection and return it."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable not set")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# ==================== Auth Decorators ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this feature.')
            return redirect(url_for('login', next=request.full_path))
        return f(*args, **kwargs)
    return decorated_function

def payment_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.full_path))
        try:
            has_paid = _paid_user(session['user_id'])
        except Exception:
            app.logger.exception('Paid-access check failed')
            flash('We could not verify your access right now. Please try again.', 'error')
            return redirect(url_for('index'))
        if not has_paid:
            flash('Please complete the ₦500 payment to access the Paid Simulator.', 'warning')
            return redirect(url_for('payment'))
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_notification_context():
    """Expose a small unread notification summary to the global navigation."""
    empty = {'notifications': [], 'notification_unread_count': 0}
    user_id = session.get('user_id')
    if not user_id:
        return empty
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''
            SELECT id, title, kind, message, action_url, source, severity, created_at
            FROM campusmate_notifications
            WHERE user_id = %s AND dismissed_at IS NULL
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 5
        ''', (user_id,))
        notifications = [dict(row) for row in cursor.fetchall()]
        cursor.execute('''
            SELECT COUNT(*) FROM campusmate_notifications
            WHERE user_id = %s AND dismissed_at IS NULL AND read_at IS NULL
        ''', (user_id,))
        unread = int(cursor.fetchone()[0] or 0)
        cursor.close()
        return {'notifications': notifications, 'notification_unread_count': unread}
    except Exception:
        return empty
    finally:
        if conn:
            conn.close()

# ==================== Routes ====================

@app.route('/notifications')
@login_required
def notification_center():
    """Render the shared notification centre for free and premium users."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''
            SELECT id, title, kind, message, action_url, source, severity, created_at, read_at
            FROM campusmate_notifications
            WHERE user_id = %s AND dismissed_at IS NULL
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 50
        ''', (session['user_id'],))
        notifications = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return render_template('notifications.html', notifications=notifications)
    except Exception:
        app.logger.exception('Notification centre loading failed')
        flash('We could not load your notifications right now. Please try again.', 'error')
        return redirect(url_for('index'))
    finally:
        if conn:
            conn.close()

@app.route('/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE campusmate_notifications
            SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
            WHERE id = %s AND user_id = %s AND dismissed_at IS NULL
        ''', (notification_id, session['user_id']))
        conn.commit()
        cursor.close()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': True})
        return redirect(_safe_local_target(request.referrer, 'notification_center'))
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('Notification read update failed')
        return jsonify({'ok': False, 'message': 'We could not update this notification.'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/notifications/read-all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE campusmate_notifications
            SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
            WHERE user_id = %s AND dismissed_at IS NULL AND read_at IS NULL
        ''', (session['user_id'],))
        conn.commit()
        cursor.close()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': True})
        return redirect(_safe_local_target(request.referrer, 'notification_center'))
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('All notification read update failed')
        return jsonify({'ok': False, 'message': 'We could not update your notifications.'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/notifications/<int:notification_id>/dismiss', methods=['POST'])
@login_required
def dismiss_notification(notification_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE campusmate_notifications
            SET dismissed_at = CURRENT_TIMESTAMP, read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
            WHERE id = %s AND user_id = %s
        ''', (notification_id, session['user_id']))
        conn.commit()
        cursor.close()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': True})
        return redirect(_safe_local_target(request.referrer, 'notification_center'))
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('Notification dismissal failed')
        return jsonify({'ok': False, 'message': 'We could not dismiss this notification.'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/')
def index():
    """Home page route."""
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = _clean_text(request.form.get('username'), 80)
        email = _valid_email(request.form.get('email'))
        password = request.form.get('password') or ''
        if len(username) < 2 or not re.fullmatch(r'[A-Za-z0-9_. -]+', username):
            flash('Enter a valid name using letters, numbers, spaces, dots, underscores, or hyphens.', 'error')
        elif not email:
            flash('Enter a valid email address.', 'error')
        elif len(password) < 8 or len(password) > 128:
            flash('Your password must contain between 8 and 128 characters.', 'error')
        else:
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('INSERT INTO users (username, email, password) VALUES (%s, %s, %s)', (username, email, generate_password_hash(password)))
                conn.commit()
                flash('Registration successful. Please log in.', 'success')
                return redirect(url_for('login'))
            except psycopg2.IntegrityError:
                if conn:
                    conn.rollback()
                flash('An account with that email or username already exists.', 'error')
            except Exception:
                if conn:
                    conn.rollback()
                app.logger.exception('Registration failed')
                flash('We could not create your account right now. Please try again.', 'error')
            finally:
                if conn:
                    conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = _valid_email(request.form.get('email'))
        password = request.form.get('password') or ''
        user = None
        conn = None
        try:
            if email and password:
                conn = get_db_connection()
                cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cursor.execute('SELECT * FROM users WHERE LOWER(email) = %s LIMIT 1', (email,))
                user = cursor.fetchone()
        except Exception:
            app.logger.exception('Login lookup failed')
        finally:
            if conn:
                conn.close()
        if user and user['password'] and check_password_hash(user['password'], password):
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['email'] = user['email']
            session['role'] = user['role'] or 'user'
            session.pop('retention_reminder', None)
            reminder = get_login_retention_reminder(user['id'])
            if reminder:
                session['retention_reminder'] = reminder
                flash(reminder, 'retention')
            flash(f'Welcome back, {user["username"]}!', 'success')
            default_target = 'admin_dashboard' if user['role'] == 'admin' else 'index'
            return redirect(_safe_local_target(request.form.get('next') or request.args.get('next'), default_target))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = _valid_email(request.form.get('email'))

        if not email:
            flash('Please enter a valid email address.', 'error')
            return redirect(url_for('forgot_password'))

        conn = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(
                cursor_factory=psycopg2.extras.DictCursor
            )

            cursor.execute(
                'SELECT id, username, email FROM users WHERE LOWER(email) = %s LIMIT 1',
                (email,)
            )

            user = cursor.fetchone()

            # Always show the same message whether the account exists or not.
            # This prevents revealing which emails are registered.
            if user:
                token = serializer.dumps(
                    user['email'],
                    salt='password-reset'
                )

                reset_link = url_for(
                    'reset_password',
                    token=token,
                    _external=True
                )

                msg = MIMEMultipart()
                msg['From'] = SMTP_USERNAME
                msg['To'] = user['email']
                msg['Subject'] = 'Password Reset - CampusMate'

                body = f"""
Hello {user['username']},

You requested to reset your PrepCampus password.

Click the link below to create a new password:

{reset_link}

This link will expire in 1 hour.

If you did not request a password reset, you can safely ignore this email.

Regards,
PrepCampus Team
"""

                msg.attach(MIMEText(body, 'plain'))

                if SMTP_USERNAME and SMTP_PASSWORD:
                    with smtplib.SMTP(
                        SMTP_SERVER,
                        SMTP_PORT,
                        timeout=10
                    ) as server:
                        server.starttls()
                        server.login(SMTP_USERNAME, SMTP_PASSWORD)
                        server.sendmail(
                            SMTP_USERNAME,
                            user['email'],
                            msg.as_string()
                        )

            flash(
                'If an account with that email exists, a password reset link has been sent.',
                'success'
            )

            return redirect(url_for('login'))

        except Exception:
            app.logger.exception('Password reset request failed')
            flash(
                'We could not process your request right now. Please try again.',
                'error'
            )
            return redirect(url_for('forgot_password'))

        finally:
            if conn:
                conn.close()

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):

    try:
        email = serializer.loads(
            token,
            salt='password-reset',
            max_age=3600
        )

    except Exception:
        flash(
            'This password reset link is invalid or has expired.',
            'error'
        )
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':

        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if len(password) < 8 or len(password) > 128:
            flash(
                'Your password must contain between 8 and 128 characters.',
                'error'
            )
            return redirect(request.url)

        if password != confirm_password:
            flash(
                'The passwords do not match.',
                'error'
            )
            return redirect(request.url)

        conn = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            hashed_password = generate_password_hash(password)

            cursor.execute(
                '''
                UPDATE users
                SET password = %s
                WHERE LOWER(email) = %s
                ''',
                (hashed_password, email.lower())
            )

            conn.commit()

            flash(
                'Your password has been reset successfully. You can now log in.',
                'success'
            )

            return redirect(url_for('login'))

        except Exception:
            if conn:
                conn.rollback()

            app.logger.exception('Password reset failed')

            flash(
                'We could not reset your password right now. Please try again.',
                'error'
            )

        finally:
            if conn:
                conn.close()

    return render_template(
        'reset_password.html',
        email=email
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT * FROM users WHERE id = %s', (session['user_id'],))
        user = cursor.fetchone()
        if not user:
            session.clear()
            flash('Your account could not be found. Please log in again.', 'error')
            return redirect(url_for('login'))
        return render_template('profile.html', user=user)
    except Exception:
        app.logger.exception('Profile loading failed')
        flash('We could not load your profile right now.', 'error')
        return redirect(url_for('index'))
    finally:
        if conn:
            conn.close()

@app.route('/upload-profile-picture', methods=['POST'])
@login_required
def upload_profile_picture():
    uploaded = request.files.get('profile_pic')
    if not uploaded or not uploaded.filename:
        flash('Choose an image before uploading.', 'warning')
        return redirect(url_for('profile'))
    if not allowed_file(uploaded.filename):
        flash('Upload a PNG, JPG, JPEG, or GIF image.', 'error')
        return redirect(url_for('profile'))
    extension = uploaded.filename.rsplit('.', 1)[1].lower()
    try:
        uploaded.stream.seek(0)
        image = Image.open(uploaded.stream)
        image.verify()
        uploaded.stream.seek(0)
        filename = f"user_{session['user_id']}_{uuid.uuid4().hex}.{extension}"
        target = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename)
        image = Image.open(uploaded.stream)
        image.thumbnail((800, 800))
        image.convert('RGB').save(target, format='JPEG', optimize=True, quality=88)
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET profile_picture = %s WHERE id = %s', (filename, session['user_id']))
            conn.commit()
        finally:
            if conn:
                conn.close()
        flash('Profile picture updated.', 'success')
    except (UnidentifiedImageError, OSError, ValueError):
        flash('That file is not a valid image. Please choose another file.', 'error')
    except Exception:
        app.logger.exception('Profile picture upload failed')
        flash('We could not update your profile picture right now.', 'error')
    return redirect(url_for('profile'))

@app.route('/send-feedback', methods=['POST'])
@login_required
def send_feedback():
    message = _clean_text(request.form.get('message'), 5000)
    if not message:
        flash('Write a message before sending feedback.', 'warning')
        return redirect(url_for('profile'))
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO feedback (user_id, message) VALUES (%s, %s)', (session['user_id'], message))
        conn.commit()
        send_email_notification(session.get('email', 'Unknown User'), message)
        flash('Thank you for your feedback.', 'success')
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('Feedback submission failed')
        flash('We could not send your feedback right now. Please try again.', 'error')
    finally:
        if conn:
            conn.close()
    return redirect(url_for('profile'))

def send_email_notification(user_email, user_message):
    """Sends an email notification when feedback is submitted."""
    if not all([SMTP_USERNAME, SMTP_PASSWORD, ADMIN_EMAIL]):
        print("Email configuration missing. Skipping email notification.")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = ADMIN_EMAIL
        msg['Subject'] = f"New Feedback from {user_email}"

        body = f"User Email: {user_email}\n\nFeedback Message:\n{user_message}"
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, ADMIN_EMAIL, msg.as_string())
    except Exception:
        app.logger.exception('Feedback email notification failed')


@app.route('/leaderboard')
def leaderboard():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''
            SELECT s.*, u.username
            FROM scores s
            JOIN users u ON s.user_id = u.id
            WHERE s.total > 0 AND s.score >= 0 AND s.score <= s.total
            ORDER BY (CAST(s.score AS FLOAT) / NULLIF(s.total, 0)) DESC, s.created_at DESC
            LIMIT 10
        ''')
        return render_template('leaderboard.html', top_scores=cursor.fetchall())
    except Exception:
        app.logger.exception('Leaderboard loading failed')
        flash('We could not load the leaderboard right now.', 'error')
        return render_template('leaderboard.html', top_scores=[])
    finally:
        if conn:
            conn.close()

@app.route('/payment', methods=['GET'])
@login_required
def payment():
    return render_template('payment.html', paystack_public_key=PAYSTACK_PUBLIC_KEY, email=session.get('email'))

@app.route('/verify-payment/<reference>')
@login_required
def verify_payment(reference):
    reference = _clean_text(reference, 120)
    if not re.fullmatch(r'[A-Za-z0-9._-]{4,120}', reference):
        return jsonify({'status': 'failed', 'message': 'Invalid payment reference.'}), 400
    if not PAYSTACK_SECRET_KEY or PAYSTACK_SECRET_KEY == 'your_paystack_secret_key':
        return jsonify({'status': 'failed', 'message': 'Payment verification is temporarily unavailable.'}), 503
    try:
        response = requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}'},
            timeout=(3, 12),
        )
        res_data = response.json()
        payment_data = res_data.get('data') or {}
        if not res_data.get('status') or payment_data.get('status') != 'success':
            return jsonify({'status': 'failed', 'message': res_data.get('message', 'Payment was not successful.')}), 400
        amount_kobo = int(payment_data.get('amount') or 0)
        if amount_kobo != PAYMENT_AMOUNT_KOBO:
            app.logger.warning('Payment amount mismatch for reference %s', reference)
            return jsonify({'status': 'failed', 'message': 'The payment amount could not be verified.'}), 400
        payer_email = ((payment_data.get('customer') or {}).get('email') or '').strip().lower()
        if payer_email and payer_email != (session.get('email') or '').strip().lower():
            return jsonify({'status': 'failed', 'message': 'This payment does not match the logged-in account.'}), 403
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, status FROM payments WHERE reference = %s FOR UPDATE', (reference,))
            existing = cursor.fetchone()
            if existing and existing[0] != session['user_id']:
                conn.rollback()
                return jsonify({'status': 'failed', 'message': 'This payment reference is already linked to another account.'}), 409
            if existing and existing[1] == 'paid':
                already_paid = True
            else:
                cursor.execute('''
                    INSERT INTO payments (user_id, amount, status, reference)
                    VALUES (%s, %s, 'paid', %s)
                    ON CONFLICT (reference) DO UPDATE SET status = 'paid', user_id = EXCLUDED.user_id
                ''', (session['user_id'], amount_kobo // 100, reference))
                cursor.execute('UPDATE users SET status = %s WHERE id = %s', ('Paid', session['user_id']))
                conn.commit()
                already_paid = False
        finally:
            if conn:
                conn.close()
        flash('Payment verified. Paid Simulator access is now active.', 'success')
        return jsonify({'status': 'success', 'already_verified': already_paid})
    except (requests.RequestException, ValueError, KeyError):
        app.logger.exception('Paystack verification failed')
        return jsonify({'status': 'failed', 'message': 'We could not verify the payment right now. Please try again.'}), 502
    except Exception:
        app.logger.exception('Payment persistence failed')
        return jsonify({'status': 'failed', 'message': 'Payment verification could not be completed.'}), 500

@app.route('/free-courses')
def free_courses():
    """Course selection page for free questions."""
    return render_template('courses.html')

@app.route('/paid-courses')
@payment_required
def paid_courses():
    """Course selection page for paid simulator."""
    return render_template('paid_courses.html')

@app.route('/study-courses')
def study_courses():
    """Course selection page for study questions."""
    return render_template('study_courses.html')

@app.route('/configure-test')
def configure_test():
    """Render a bounded test configuration page for one simulator mode."""
    simulator = _clean_text(request.args.get('simulator'), 12).lower() or 'free'
    if simulator not in {'free', 'paid', 'study'}:
        simulator = 'free'
    course = _clean_text(request.args.get('course'), 32).upper()
    if simulator == 'paid':
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.full_path))
        try:
            if not _paid_user(session['user_id']):
                flash('Please complete payment before configuring a paid test.', 'warning')
                return redirect(url_for('payment'))
        except Exception:
            app.logger.exception('Paid test access check failed')
            flash('We could not verify paid access right now.', 'error')
            return redirect(url_for('paid_courses'))
    if not course:
        return redirect(url_for('paid_courses' if simulator == 'paid' else 'free_courses'))
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = %s', (course,))
        total_questions = int(cursor.fetchone()[0] or 0)
    except Exception:
        app.logger.exception('Test configuration loading failed')
        flash('We could not load this course right now. Please try again.', 'error')
        return redirect(url_for('paid_courses' if simulator == 'paid' else 'free_courses'))
    finally:
        if conn:
            conn.close()
    if total_questions <= 0:
        flash('This course does not have questions available yet.', 'warning')
        return redirect(url_for('paid_courses' if simulator == 'paid' else 'free_courses'))
    course_names = {
        'BIO101': 'General Biology', 'MTH101': 'General Mathematics', 'MTH102': 'General Mathematics',
        'CHM101': 'General Chemistry', 'PHY101': 'General Physics', 'PHY111': 'Physics',
        'PHY121': 'Physics', 'STA111': 'Statistics', 'GST101': 'Communication in English',
        'COS101': 'Computer Science', 'COS103': 'Computer Science II',
    }
    session['simulator_type'] = simulator
    return render_template('configure_test.html', course=course, course_full_name=course_names.get(course, course), simulator=simulator, total_questions=total_questions)

@app.route('/quiz')
def quiz():
    simulator = _clean_text(request.args.get('simulator'), 12).lower() or 'free'
    if simulator not in {'free', 'paid', 'study'}:
        simulator = 'free'
    course = _clean_text(request.args.get('course'), 32).upper()
    topic = _clean_text(request.args.get('topic'), 160) or 'All Topics'
    num_questions = _bounded_int(request.args.get('num_questions'), 10, 1, 100)
    duration_hours = _bounded_int(request.args.get('hours'), 0, 0, 12)
    duration_minutes = _bounded_int(request.args.get('minutes'), 10, 0, 59)
    duration_seconds = duration_hours * 3600 + duration_minutes * 60
    if duration_seconds <= 0:
        duration_seconds = 600
    if simulator == 'paid':
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.full_path))
        try:
            if not _paid_user(session['user_id']):
                flash('Please complete payment before starting a paid test.', 'warning')
                return redirect(url_for('payment'))
        except Exception:
            app.logger.exception('Quiz paid-access check failed')
            flash('We could not verify paid access right now.', 'error')
            return redirect(url_for('paid_courses'))
    if not course:
        return redirect(url_for('paid_courses' if simulator == 'paid' else ('study_courses' if simulator == 'study' else 'free_courses')))
    session['current_course'] = course
    session['current_topic'] = topic
    session['attempt_id'] = uuid.uuid4().hex
    session['num_questions'] = num_questions
    session['duration_seconds'] = duration_seconds
    session['simulator_type'] = simulator
    return render_template('study_questions.html' if simulator == 'study' else 'quiz.html', course=course, topic=topic)

@app.route('/api/review-data')
def get_review_data():
    user_answers = session.get('user_answers', [])
    course = session.get('current_course', 'Unknown')
    review_data = get_detailed_results(user_answers, course)
    return jsonify(review_data)

@app.route('/api/course-info')
def get_course_info():
    course = _clean_text(request.args.get('course'), 32).upper()
    topic = _clean_text(request.args.get('topic'), 160)
    simulator = _clean_text(request.args.get('simulator') or session.get('simulator_type') or 'free', 12).lower()
    if not course:
        return jsonify({'error': 'Course parameter required'}), 400
    if simulator not in {'free', 'paid', 'study'}:
        simulator = 'free'
    if simulator == 'free' and not course.startswith(('MTH', 'CHM', 'PHY')):
        return jsonify({'error': 'This course is not available in the free simulator'}), 403
    if simulator == 'paid':
        if 'user_id' not in session:
            return jsonify({'error': 'Login required'}), 401
        try:
            if not _paid_user(session['user_id']):
                return jsonify({'error': 'Paid access required'}), 403
        except Exception:
            app.logger.exception('Course info access check failed')
            return jsonify({'error': 'Course information is temporarily unavailable'}), 503
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if topic and topic != 'All Topics':
            cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = %s AND topic = %s', (course, topic))
        else:
            cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = %s', (course,))
        total_questions = int(cursor.fetchone()[0] or 0)
        if simulator == 'free':
            total_questions = min(total_questions, 10)
        return jsonify({'total_questions': total_questions})
    except Exception:
        app.logger.exception('Course information lookup failed')
        return jsonify({'error': 'Course information is temporarily unavailable'}), 503
    finally:
        if conn:
            conn.close()

@app.route('/api/topics')
def get_topics():
    course = _clean_text(request.args.get('course'), 32).upper()
    if not course:
        return jsonify({'error': 'Course parameter required'}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''SELECT DISTINCT topic FROM questions WHERE course_code = %s AND topic IS NOT NULL AND topic <> '' ORDER BY topic''', (course,))
        return jsonify({'topics': [row['topic'] for row in cursor.fetchall()]})
    except Exception:
        app.logger.exception('Topic lookup failed')
        return jsonify({'error': 'Topics are temporarily unavailable'}), 503
    finally:
        if conn:
            conn.close()

@app.route('/api/available-codes')
def get_available_codes():
    subject = _clean_text(request.args.get('subject'), 12).upper()
    if not subject or not re.fullmatch(r'[A-Z0-9]+', subject):
        return jsonify({'error': 'Valid subject code required'}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT DISTINCT course_code FROM questions WHERE course_code LIKE %s ORDER BY course_code LIMIT 100', (f'{subject}%',))
        return jsonify({'codes': [row['course_code'] for row in cursor.fetchall()]})
    except Exception:
        app.logger.exception('Course-code lookup failed')
        return jsonify({'error': 'Course codes are temporarily unavailable'}), 503
    finally:
        if conn:
            conn.close()

@app.route('/api/questions', methods=['GET'])
def get_questions():
    conn = None
    try:
        course = _clean_text(request.args.get('course'), 32).upper()
        topic = _clean_text(request.args.get('topic'), 160)
        simulator = _clean_text(session.get('simulator_type') or 'free', 12).lower()
        if not course:
            return jsonify({'error': 'Course parameter required'}), 400
        if simulator not in {'free', 'paid', 'study'}:
            simulator = 'free'
        if simulator == 'free' and not course.startswith(('MTH', 'CHM', 'PHY')):
            return jsonify({'error': 'This course is not available in the free simulator'}), 403
        if simulator == 'paid':
            if 'user_id' not in session:
                return jsonify({'error': 'Login required'}), 401
            if not _paid_user(session['user_id']):
                return jsonify({'error': 'Paid access required'}), 403
        limit = _bounded_int(request.args.get('limit'), 10, 1, 100)
        if simulator == 'free':
            limit = min(limit, 10)
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        query = '''
            SELECT id, course_code, question_text, option_a, option_b, option_c,
                   option_d, correct_option, solution, topic, content_json
            FROM questions
            WHERE course_code = %s
        '''
        params = [course]
        if simulator == 'paid':
            query += '''
                AND NOT EXISTS (
                    SELECT 1 FROM premium_question_views v
                    WHERE v.user_id = %s AND v.question_id = questions.id
                )
            '''
            params.append(session['user_id'])
        if topic and topic != 'All Topics':
            query += ' AND topic = %s'
            params.append(topic)
        query += ' ORDER BY RANDOM() LIMIT %s'
        params.append(limit)
        cursor.execute(query, tuple(params))
        questions = cursor.fetchall()
        if simulator == 'paid' and questions:
            for question in questions:
                cursor.execute(
                    '''INSERT INTO premium_question_views (user_id, question_id)
                       VALUES (%s, %s)
                       ON CONFLICT (user_id, question_id) DO NOTHING''',
                    (session['user_id'], question['id']),
                )
            conn.commit()
        if not questions:
            return jsonify({'error': f'No questions found for course {course}'}), 404
        questions_list = []
        for row in questions:
            normalized = _question_from_row(row, source='database')
            normalized['topic'] = row['topic'] or 'General practice'
            questions_list.append(normalized)
        return jsonify(questions_list)
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('Question retrieval failed')
        return jsonify({'error': 'Questions are temporarily unavailable'}), 503
    finally:
        if conn:
            conn.close()


def _normalize_answers(raw_answers):

    if not isinstance(raw_answers, list) or len(raw_answers) > 100:
        return []
    normalized, seen = [], set()
    for item in raw_answers:
        if not isinstance(item, dict):
            continue
        try:
            question_id = int(item.get('question_id'))
        except (TypeError, ValueError):
            continue
        if question_id <= 0 or question_id in seen:
            continue
        selected = _clean_text(item.get('answer'), 1).upper() or None
        if selected not in {'A', 'B', 'C', 'D'}:
            selected = None
        time_spent = item.get('time_spent_seconds')
        try:
            time_spent = max(0.0, min(float(time_spent), 3600.0)) if time_spent is not None else None
        except (TypeError, ValueError):
            time_spent = None
        normalized.append({'question_id': question_id, 'answer': selected, 'time_spent_seconds': time_spent})
        seen.add(question_id)
    return normalized

@app.route('/submit', methods=['POST'])
def submit():
    data = request.get_json(silent=True) or {}
    answers = _normalize_answers(data.get('answers'))
    course = _clean_text(session.get('current_course'), 32).upper()
    if not course:
        return jsonify({'error': 'No course selected'}), 400
    if not answers:
        return jsonify({'error': 'Submit at least one answer.'}), 400
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        question_ids = [item['question_id'] for item in answers]
        cursor.execute(
            '''SELECT id, topic, correct_option FROM questions
               WHERE id = ANY(%s) AND course_code = %s''',
            (question_ids, course),
        )
        question_rows = {int(row['id']): row for row in cursor.fetchall()}
        if len(question_rows) != len(question_ids):
            conn.rollback()
            return jsonify({'error': 'One or more submitted questions are invalid for this course.'}), 400
        score = sum(
            1 for item in answers
            if item['answer'] and item['answer'] == str(question_rows[item['question_id']]['correct_option']).upper()
        )
        total = len(answers)
        session['score'] = score
        session['total'] = total
        session['user_answers'] = answers
        if 'user_id' in session:
            attempt_id = session.get('attempt_id') or uuid.uuid4().hex
            cursor.execute(
                'INSERT INTO scores (user_id, course_code, score, total) VALUES (%s, %s, %s, %s)',
                (session['user_id'], course, score, total),
            )
            for item in answers:
                question = question_rows[item['question_id']]
                correct = str(question['correct_option']).upper()
                cursor.execute(
                    '''INSERT INTO premium_question_attempts
                       (user_id, attempt_id, question_id, course_code, topic,
                        selected_option, correct_option, was_correct, time_spent_seconds)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (user_id, attempt_id, question_id) DO NOTHING''',
                    (session['user_id'], attempt_id, item['question_id'], course,
                     question['topic'] or 'General practice', item['answer'], correct,
                     item['answer'] == correct, item['time_spent_seconds']),
                )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('Quiz submission failed')
        return jsonify({'error': 'We could not submit this quiz. Please try again.'}), 503
    finally:
        if conn:
            conn.close()
    if 'user_id' in session:
        try:
            from premium_routes import refresh_campusmate_plan_after_activity
            refresh_campusmate_plan_after_activity(session['user_id'])
        except Exception:
            app.logger.exception('CampusMate post-quiz refresh skipped')
    return jsonify({'score': score, 'total': total})

def get_detailed_results(answers, course):
    course = _clean_text(course, 32).upper()
    normalized_answers = _normalize_answers(answers)
    if not course or not normalized_answers:
        return []
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        question_ids = [item['question_id'] for item in normalized_answers]
        cursor.execute(
            '''SELECT id, course_code, question_text, option_a, option_b,
                      option_c, option_d, correct_option, solution, topic, content_json
               FROM questions WHERE id = ANY(%s) AND course_code = %s''',
            (question_ids, course),
        )
        rows = {int(row['id']): row for row in cursor.fetchall()}
        review_data = []
        for item in normalized_answers:
            row = rows.get(item['question_id'])
            if not row:
                continue
            normalized = _question_from_row(row, source='database')
            review_data.append({**normalized, 'id': item['question_id'],
                                'user_answer': item['answer'],
                                'correct_answer': row['correct_option']})
        return review_data
    except Exception:
        app.logger.exception('Detailed result loading failed')
        return []
    finally:
        if conn:
            conn.close()

def calculate_score(answers, course):
    review_data = get_detailed_results(answers, course)
    return sum(1 for item in review_data
               if item.get('user_answer') and
               str(item.get('user_answer')).upper() == str(item.get('correct_answer')).upper())

@app.route('/result')
def result():
    total = _bounded_int(session.get('total'), 0, 0, 100)
    if total <= 0 or 'score' not in session:
        flash('Complete a quiz to view its result.', 'info')
        return redirect(url_for('free_courses'))
    score = _bounded_int(session.get('score'), 0, 0, total)
    course = _clean_text(session.get('current_course'), 32) or 'Unknown course'
    return render_template('result.html', score=score, total=total, course=course)

@app.route('/review')
def review():
    user_answers = session.get('user_answers') or []
    course = _clean_text(session.get('current_course'), 32) or ''
    if not course or not user_answers:
        flash('Complete a quiz to review your answers.', 'info')
        return redirect(url_for('free_courses'))
    review_data = get_detailed_results(user_answers, course)
    return render_template('review.html', review_data=review_data, course=course)

@app.errorhandler(404)
def not_found(error): return render_template('error.html', message='Page not found'), 404

@app.errorhandler(500)
def server_error(error): return render_template('error.html', message='Server error occurred'), 500

if __name__ == '__main__':
    app.run()
