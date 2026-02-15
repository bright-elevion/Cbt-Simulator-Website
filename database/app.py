from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
import sqlite3
import os
import requests
from functools import wraps
from init_db import init_db
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Load environment variables
# Check for key.env first, then fallback to .env
if os.path.exists('key.env'):
    load_dotenv('key.env')
else:
    load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'supersecretkey')

# Initialize database
init_db()

# Upload configuration
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# OAuth Configuration
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    access_token_url='https://accounts.google.com/o/oauth2/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    client_kwargs={'scope': 'openid email profile'},
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration'
)

# Paystack Configuration
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY')
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY')

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'quiz.db')

def get_db_connection():
    """Create a database connection and return it."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== Auth Decorators ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this feature.')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def payment_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        
        # Check if user has paid
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM payments WHERE user_id = ? AND status = "paid"', (session['user_id'],))
        payment = cursor.fetchone()
        conn.close()
        
        if not payment:
            flash('Please pay ₦500 to access the Paid Simulator.')
            return redirect(url_for('payment'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== Routes ====================

@app.route('/')
def index():
    """Home page route."""
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not username or not email or not password:
            flash('All fields are required.')
            return render_template('register.html')
        
        hashed_password = generate_password_hash(password)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO users (username, email, password) VALUES (?, ?, ?)', 
                           (username, email, hashed_password))
            conn.commit()
            flash('Registration successful! Please log in.')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already exists.')
        finally:
            conn.close()
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and user['password'] and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['email'] = user['email']
            flash(f'Welcome back, {user["username"]}!')
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.')
            
    return render_template('login.html')

@app.route('/login/google')
def login_google():
    # Check if Google credentials are set
    if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):
        flash('Google Login is not configured yet. Please use email/password.')
        return redirect(url_for('login'))
    
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    try:
        token = google.authorize_access_token()
        resp = google.get('userinfo')
        user_info = resp.json()
        
        email = user_info['email']
        username = user_info.get('name', email.split('@')[0])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        
        if not user:
            # Create new user
            cursor.execute('INSERT INTO users (username, email) VALUES (?, ?)', (username, email))
            conn.commit()
            cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
            user = cursor.fetchone()
        
        conn.close()
        
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['email'] = user['email']
        
        flash(f'Welcome, {session["username"]}!')
        return redirect(url_for('index'))
    except Exception as e:
        flash('Google authentication failed.')
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    conn.close()
    return render_template('profile.html', user=user)

@app.route('/upload-profile-picture', methods=['POST'])
@login_required
def upload_profile_picture():
    if 'profile_pic' not in request.files:
        flash('No file part')
        return redirect(url_for('profile'))
    
    file = request.files['profile_pic']
    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('profile'))
    
    if file and allowed_file(file.filename):
        filename = secure_filename(f"user_{session['user_id']}_{file.filename}")
        file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], filename))
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET profile_picture = ? WHERE id = ?', (filename, session['user_id']))
        conn.commit()
        conn.close()
        
        flash('Profile picture updated!')
    else:
        flash('Invalid file type. Please upload an image.')
        
    return redirect(url_for('profile'))

@app.route('/send-feedback', methods=['POST'])
@login_required
def send_feedback():
    message = request.form.get('message')
    if message:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO feedback (user_id, message) VALUES (?, ?)', (session['user_id'], message))
        conn.commit()
        conn.close()
        flash('Thank you for your feedback!')
    return redirect(url_for('profile'))

@app.route('/leaderboard')
def leaderboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Get top 10 scores with usernames
    cursor.execute('''
        SELECT s.*, u.username 
        FROM scores s 
        JOIN users u ON s.user_id = u.id 
        ORDER BY (CAST(s.score AS FLOAT) / s.total) DESC, s.created_at DESC 
        LIMIT 10
    ''')
    top_scores = cursor.fetchall()
    conn.close()
    return render_template('leaderboard.html', top_scores=top_scores)

@app.route('/payment', methods=['GET'])
@login_required
def payment():
    return render_template('payment.html', paystack_public_key=PAYSTACK_PUBLIC_KEY, email=session.get('email'))

@app.route('/verify-payment/<reference>')
@login_required
def verify_payment(reference):
    if not PAYSTACK_SECRET_KEY or PAYSTACK_SECRET_KEY == 'your_paystack_secret_key':
        return jsonify({'status': 'failed', 'message': 'Paystack Secret Key is not configured.'}), 500

    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"
    }
    try:
        response = requests.get(url, headers=headers)
        res_data = response.json()
        
        if not res_data.get('status'):
            return jsonify({'status': 'failed', 'message': res_data.get('message', 'Verification failed')}), 400

        if res_data['data']['status'] == 'success':
            amount = res_data['data']['amount'] / 100  # Paystack returns in kobo
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('INSERT INTO payments (user_id, amount, status, reference) VALUES (?, ?, ?, ?)', 
                           (session['user_id'], amount, 'paid', reference))
            conn.commit()
            conn.close()
            
            flash('Payment successful! You now have access to the Paid Simulator.')
            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'failed', 'message': 'Payment was not successful.'}), 400
    except Exception as e:
        return jsonify({'status': 'failed', 'message': str(e)}), 500

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
    """Test configuration page."""
    course = request.args.get('course', None)
    simulator = request.args.get('simulator', 'free')
    
    if simulator == 'paid':
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM payments WHERE user_id = ? AND status = "paid"', (session['user_id'],))
        payment = cursor.fetchone()
        conn.close()
        if not payment:
            return redirect(url_for('payment'))

    if not course:
        if simulator == 'paid':
            return redirect(url_for('paid_courses'))
        else:
            return redirect(url_for('free_courses'))
    
    course_names = {
        'MTH': 'Mathematics', 'CHM': 'Chemistry', 'PHY': 'Physics',
        'STA': 'Statistics', 'BIO': 'Biology', 'COS': 'Computer Science',
        'MTH101': 'Mathematics 101', 'CHM101': 'Chemistry 101', 'PHY101': 'Physics 101',
        'PHY111': 'Physics 111', 'PHY121': 'Physics 121', 'STA101': 'Statistics 101',
        'BIO101': 'Biology 101', 'COS101': 'Computer Science 101', 'COS 103': 'Computer Science 103'
    }
    course_full_name = course_names.get(course, course)
    session['simulator_type'] = simulator
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = ?', (course,))
    total_questions = cursor.fetchone()[0]
    conn.close()
    
    return render_template('configure_test.html', course=course, course_full_name=course_full_name, simulator=simulator, total_questions=total_questions)

@app.route('/quiz')
def quiz():
    course = request.args.get('course', None)
    num_questions = request.args.get('num_questions', 10)
    duration_hours = request.args.get('hours', 0)
    duration_minutes = request.args.get('minutes', 10)
    simulator = request.args.get('simulator', 'free')
    
    if simulator == 'paid':
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM payments WHERE user_id = ? AND status = "paid"', (session['user_id'],))
        payment = cursor.fetchone()
        conn.close()
        if not payment:
            return redirect(url_for('payment'))

    if not course:
        return redirect(url_for('paid_courses' if simulator == 'paid' else 'free_courses'))
    
    session['current_course'] = course
    session['num_questions'] = int(num_questions)
    session['duration_seconds'] = (int(duration_hours) * 3600) + (int(duration_minutes) * 60)
    session['simulator_type'] = simulator
    
    return render_template('study_questions.html' if simulator == 'study' else 'quiz.html', course=course)

@app.route('/api/review-data')
def get_review_data():
    user_answers = session.get('user_answers', [])
    course = session.get('current_course', 'Unknown')
    review_data = get_detailed_results(user_answers, course)
    return jsonify(review_data)

@app.route('/api/course-info')
def get_course_info():
    course = request.args.get('course', None)
    simulator = request.args.get('simulator', session.get('simulator_type', 'free'))
    if not course: return jsonify({'error': 'Course parameter required'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = ?', (course,))
    total_questions = cursor.fetchone()[0]
    conn.close()
    if simulator == 'free':
        allowed_courses = ['MTH', 'CHM', 'PHY']
        if not any(course.startswith(prefix) for prefix in allowed_courses):
            return jsonify({'error': 'This course is not available in the free simulator'}), 403
        total_questions = min(total_questions, 10)
    return jsonify({'total_questions': total_questions})

@app.route('/api/available-codes')
def get_available_codes():
    subject = request.args.get('subject', None)
    if not subject: return jsonify({'error': 'Subject parameter required'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT course_code FROM questions WHERE course_code LIKE ?', (f'{subject}%',))
    codes = [row['course_code'] for row in cursor.fetchall()]
    conn.close()
    return jsonify({'codes': codes})

@app.route('/api/questions', methods=['GET'])
def get_questions():
    try:
        course = request.args.get('course', None)
        limit = request.args.get('limit', None)
        simulator = session.get('simulator_type', 'free')
        if not course: return jsonify({'error': 'Course parameter required'}), 400
        if simulator == 'free':
            limit = min(int(limit), 10) if limit else 10
        elif limit:
            limit = int(limit)
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Always fetch all fields to avoid missing data in any mode
        query = 'SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option, solution FROM questions WHERE course_code = ? ORDER BY RANDOM()'
        params = [course]
        
        if limit:
            query += ' LIMIT ?'
            params.append(limit)
            
        cursor.execute(query, tuple(params))
        questions = cursor.fetchall()
        conn.close()
        
        if not questions: return jsonify({'error': f'No questions found for course {course}'}), 404
        
        questions_list = []
        for q in questions:
            item = {
                'id': q['id'], 
                'question_text': q['question_text'], 
                'option_a': q['option_a'], 
                'option_b': q['option_b'], 
                'option_c': q['option_c'], 
                'option_d': q['option_d'],
                'correct_option': q['correct_option'],
                'solution': q['solution'] if q['solution'] else "No detailed solution available."
            }
            questions_list.append(item)
        return jsonify(questions_list)
    except Exception as e:
        return jsonify({'error': 'Failed to fetch questions'}), 500

@app.route('/submit', methods=['POST'])
def submit():
    try:
        data = request.get_json()
        answers = data.get('answers', [])
        course = session.get('current_course', None)
        if not course: return jsonify({'error': 'No course selected'}), 400
        score = calculate_score(answers, course)
        session['score'] = score
        session['total'] = len(answers)
        session['user_answers'] = answers
        
        # Save score to database if user is logged in
        if 'user_id' in session:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('INSERT INTO scores (user_id, course_code, score, total) VALUES (?, ?, ?, ?)',
                           (session['user_id'], course, score, len(answers)))
            conn.commit()
            conn.close()
            
        return jsonify({'score': score, 'total': len(answers)})
    except Exception as e:
        return jsonify({'error': 'Failed to submit quiz'}), 500

def get_detailed_results(answers, course):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        review_data = []
        for answer_data in answers:
            question_id = answer_data.get('question_id')
            user_answer = answer_data.get('answer')
            cursor.execute('SELECT question_text, option_a, option_b, option_c, option_d, correct_option, solution FROM questions WHERE id = ?', (question_id,))
            q = cursor.fetchone()
            if q:
                review_data.append({
                    'id': question_id, 'question_text': q['question_text'], 'option_a': q['option_a'], 'option_b': q['option_b'], 'option_c': q['option_c'], 'option_d': q['option_d'],
                    'user_answer': user_answer, 'correct_answer': q['correct_option'], 'solution': q['solution'] if q['solution'] else "No detailed solution available."
                })
        conn.close()
        return review_data
    except Exception as e:
        return []

def calculate_score(answers, course):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        score = 0
        for answer_data in answers:
            question_id = answer_data.get('question_id')
            user_answer = answer_data.get('answer')
            if user_answer is None: continue
            cursor.execute('SELECT correct_option FROM questions WHERE id = ?', (question_id,))
            result = cursor.fetchone()
            if result and user_answer == result['correct_option']:
                score += 1
        conn.close()
        return score
    except Exception as e:
        return 0

@app.route('/result')
def result():
    return render_template('result.html', score=session.get('score', 0), total=session.get('total', 10), course=session.get('current_course', 'Unknown'))

@app.route('/review')
def review():
    user_answers = session.get('user_answers', [])
    course = session.get('current_course', 'Unknown')
    review_data = get_detailed_results(user_answers, course)
    return render_template('review.html', review_data=review_data, course=course)

@app.errorhandler(404)
def not_found(error): return render_template('error.html', message='Page not found'), 404

@app.errorhandler(500)
def server_error(error): return render_template('error.html', message='Server error occurred'), 500

if __name__ == '__main__':
    app.run()
