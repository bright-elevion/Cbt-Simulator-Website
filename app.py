from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
import psycopg2
import psycopg2.extras
import os
import requests
from functools import wraps
from init_db import init_db
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import os
print("DATABASE_URL =", os.getenv("DATABASE_URL"))
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


# Paystack Configuration
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY')
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY')

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
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT status FROM payments WHERE user_id = %s AND status = %s', (session['user_id'], 'paid'))
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
            cursor.execute('INSERT INTO users (username, email, password) VALUES (%s, %s, %s)', 
                           (username, email, hashed_password))
            conn.commit()
            flash('Registration successful! Please log in.')
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            conn.rollback()
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
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
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

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('SELECT * FROM users WHERE id = %s', (session['user_id'],))
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
        cursor.execute('UPDATE users SET profile_picture = %s WHERE id = %s', (filename, session['user_id']))
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
        cursor.execute('INSERT INTO feedback (user_id, message) VALUES (%s, %s)', (session['user_id'], message))
        conn.commit()
        conn.close()

        send_email_notification(session.get('email', 'Unknown User'), message)
        
        flash('Thank you for your feedback!')
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

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(SMTP_USERNAME, ADMIN_EMAIL, text)
        server.quit()
    except Exception as e:
        print(f"Failed to send email: {e}")


@app.route('/leaderboard')
def leaderboard():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
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
            cursor.execute('INSERT INTO payments (user_id, amount, status, reference) VALUES (%s, %s, %s, %s)', 
                           (session['user_id'], amount, 'paid', reference))
            cursor.execute('UPDATE users SET status = %s WHERE id = %s', ('Paid', session['user_id']))
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
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT status FROM payments WHERE user_id = %s AND status = %s', (session['user_id'], 'paid'))
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
        'BIO101': 'General Biology',
        'MTH101': 'General Mathematics', 
        'MTH102': 'General Mathematics', 
        'CHM101': 'General Chemistry', 
        'PHY101': 'General Physics',
        'PHY111': 'Physics', 
        'PHY121': 'Physics', 
        'STA111': 'Statistics', 
        'GST101': 'Communicatio in English',
        'COS101': 'Computer Science', 
        'COS103': 'Computer Science II', 

    }
    course_full_name = course_names.get(course, course)
    session['simulator_type'] = simulator
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = %s', (course,))
    total_questions = cursor.fetchone()[0]
    conn.close()
    
    return render_template('configure_test.html', course=course, course_full_name=course_full_name, simulator=simulator, total_questions=total_questions)

@app.route('/quiz')
def quiz():
    course = request.args.get('course', None)
    topic = request.args.get('topic', 'All Topics')
    num_questions = request.args.get('num_questions', 10)
    duration_hours = request.args.get('hours', 0)
    duration_minutes = request.args.get('minutes', 10)
    simulator = request.args.get('simulator', 'free')
    
    if simulator == 'paid':
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT status FROM payments WHERE user_id = %s AND status = %s', (session['user_id'], 'paid'))
        payment = cursor.fetchone()
        conn.close()
        if not payment:
            return redirect(url_for('payment'))

    if not course:
        return redirect(url_for('paid_courses' if simulator == 'paid' else 'free_courses'))
    
    session['current_course'] = course
    session['current_topic'] = topic
    session['num_questions'] = int(num_questions)
    session['duration_seconds'] = (int(duration_hours) * 3600) + (int(duration_minutes) * 60)
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
    course = request.args.get('course', None)
    topic = request.args.get('topic', None)
    simulator = request.args.get('simulator', session.get('simulator_type', 'free'))
    if not course: return jsonify({'error': 'Course parameter required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if topic and topic != "All Topics":
        cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = %s AND topic = %s', (course, topic))
    else:
        cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = %s', (course,))
        
    total_questions = cursor.fetchone()[0]
    conn.close()
    
    if simulator == 'free':
        allowed_courses = ['MTH', 'CHM', 'PHY']
        if not any(course.startswith(prefix) for prefix in allowed_courses):
            return jsonify({'error': 'This course is not available in the free simulator'}), 403
        total_questions = min(total_questions, 10)
    return jsonify({'total_questions': total_questions})

@app.route('/api/topics')
def get_topics():
    course = request.args.get('course', None)
    if not course: return jsonify({'error': 'Course parameter required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('SELECT DISTINCT topic FROM questions WHERE course_code = %s', (course,))
    topics = [row['topic'] for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({'topics': topics})

@app.route('/api/available-codes')
def get_available_codes():
    subject = request.args.get('subject', None)
    if not subject: return jsonify({'error': 'Subject parameter required'}), 400
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('SELECT DISTINCT course_code FROM questions WHERE course_code LIKE %s', (f'{subject}%',))
    codes = [row['course_code'] for row in cursor.fetchall()]
    conn.close()
    return jsonify({'codes': codes})

@app.route('/api/questions', methods=['GET'])
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
        else:
            limit = None
            
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        topic = request.args.get('topic', None)
        
        query = 'SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option, solution FROM questions WHERE course_code = %s'
        params = [course]
        
        if topic and topic != "All Topics":
            query += ' AND topic = %s'
            params.append(topic)
            
        query += ' ORDER BY RANDOM()'
        
        if limit:
            query += ' LIMIT %s'
            params.append(limit)
            
        cursor.execute(query, tuple(params))
        questions = cursor.fetchall()
        conn.close()
        
        if not questions: return jsonify({'error': f'No questions found for course {course}'}), 404
        print("TOPIC RECIEVED: ", topic)
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
            cursor.execute('INSERT INTO scores (user_id, course_code, score, total) VALUES (%s, %s, %s, %s)',
                           (session['user_id'], course, score, len(answers)))
            conn.commit()
            conn.close()
            
        return jsonify({'score': score, 'total': len(answers)})
    except Exception as e:
        return jsonify({'error': 'Failed to submit quiz'}), 500

def get_detailed_results(answers, course):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        review_data = []
        for answer_data in answers:
            question_id = answer_data.get('question_id')
            user_answer = answer_data.get('answer')
            cursor.execute('SELECT question_text, option_a, option_b, option_c, option_d, correct_option, solution FROM questions WHERE id = %s', (question_id,))
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
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        score = 0
        for answer_data in answers:
            question_id = answer_data.get('question_id')
            user_answer = answer_data.get('answer')
            if user_answer is None: continue
            cursor.execute('SELECT correct_option FROM questions WHERE id = %s', (question_id,))
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
