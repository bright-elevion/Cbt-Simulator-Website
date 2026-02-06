from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import sqlite3
import os

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'quiz.db')

def get_db_connection():
    """Create a database connection and return it."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== Routes ====================

@app.route('/')
def index():
    """Home page route."""
    return render_template('index.html')

@app.route('/free-courses')
def free_courses():
    """Course selection page for free questions."""
    return render_template('courses.html')

@app.route('/paid-courses')
def paid_courses():
    """Course selection page for paid simulator."""
    return render_template('paid_courses.html')

@app.route('/configure-test')
def configure_test():
    """Test configuration page."""
    course = request.args.get('course', None)
    simulator = request.args.get('simulator', 'free')  # Default to free
    if not course:
        if simulator == 'paid':
            return redirect(url_for('paid_courses'))
        else:
            return redirect(url_for('free_courses'))
    
    # Map course codes/prefixes to full names for display
    course_names = {
        'MTH': 'Mathematics',
        'CHM': 'Chemistry',
        'PHY': 'Physics',
        'STA': 'Statistics',
        'BIO': 'Biology',
        'COS': 'Computer Science',
        'MTH101/111': 'Mathematics',
        'CHM101': 'Chemistry',
        'PHY101': 'Physics 101',
        'PHY111': 'Physics 111',
        'PHY121': 'Physics 121',
        'STA101': 'Statistics',
        'BIO101': 'Biology',
        'COS101': 'Computer Science'
    }
    course_full_name = course_names.get(course, course)
    
    # Store simulator type in session
    session['simulator_type'] = simulator
    
    # Get total questions available for this course
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = ?', (course,))
    total_questions = cursor.fetchone()[0]
    conn.close()
    
    return render_template('configure_test.html', course=course, course_full_name=course_full_name, simulator=simulator, total_questions=total_questions)

@app.route('/quiz')
def quiz():
    """Quiz page route - can accept course parameter."""
    course = request.args.get('course', None)
    num_questions = request.args.get('num_questions', 10)
    duration_hours = request.args.get('hours', 0)
    duration_minutes = request.args.get('minutes', 10)
    simulator = request.args.get('simulator', 'free')
    
    if not course:
        if simulator == 'paid':
            return redirect(url_for('paid_courses'))
        else:
            return redirect(url_for('free_courses'))
    
    # Store the course and config in session
    session['current_course'] = course
    session['num_questions'] = int(num_questions)
    session['duration_seconds'] = (int(duration_hours) * 3600) + (int(duration_minutes) * 60)
    session['simulator_type'] = simulator
    
    return render_template('quiz.html', course=course)

@app.route('/api/review-data')
def get_review_data():
    """API endpoint to get review data for the last quiz."""
    user_answers = session.get('user_answers', [])
    course = session.get('current_course', 'Unknown')
    review_data = get_detailed_results(user_answers, course)
    return jsonify(review_data)

@app.route('/api/course-info')
def get_course_info():
    """API endpoint to get information about a course."""
    course = request.args.get('course', None)
    simulator = request.args.get('simulator', session.get('simulator_type', 'free'))
    
    if not course:
        return jsonify({'error': 'Course parameter required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM questions WHERE course_code = ?', (course,))
    total_questions = cursor.fetchone()[0]
    conn.close()
    
    # Enforce limit and course restriction for free simulator
    if simulator == 'free':
        allowed_courses = ['MTH', 'CHM', 'PHY']
        # Check if the course prefix is allowed
        if not any(course.startswith(prefix) for prefix in allowed_courses):
            return jsonify({'error': 'This course is not available in the free simulator'}), 403
        total_questions = min(total_questions, 10)
    
    return jsonify({'total_questions': total_questions})

@app.route('/api/available-codes')
def get_available_codes():
    """API endpoint to get available course codes for a given subject prefix."""
    subject = request.args.get('subject', None)
    if not subject:
        return jsonify({'error': 'Subject parameter required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    # Search for course codes starting with the subject prefix (e.g., 'PHY')
    cursor.execute('SELECT DISTINCT course_code FROM questions WHERE course_code LIKE ?', (f'{subject}%',))
    codes = [row['course_code'] for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({'codes': codes})

@app.route('/api/questions', methods=['GET'])
def get_questions():
    """
    API endpoint to fetch questions for a specific course.
    Query parameter: course (e.g., 'MTH101/111')
    Returns JSON array of questions without correct answers.
    """
    try:
        course = request.args.get('course', None)
        limit = request.args.get('limit', None)
        simulator = session.get('simulator_type', 'free')
        
        if not course:
            return jsonify({'error': 'Course parameter required'}), 400
            
        # Enforce hard limit for free simulator
        if simulator == 'free':
            if limit:
                limit = min(int(limit), 10)
            else:
                limit = 10
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Fetch questions for the specific course
        query = 'SELECT id, question_text, option_a, option_b, option_c, option_d FROM questions WHERE course_code = ?'
        params = [course]
        
        if limit:
            query += ' ORDER BY RANDOM() LIMIT ?'
            params.append(int(limit))
            
        cursor.execute(query, tuple(params))
        questions = cursor.fetchall()
        conn.close()
        
        if not questions:
            return jsonify({'error': f'No questions found for course {course}'}), 404
        
        # Convert to list of dictionaries
        questions_list = [
            {
                'id': q['id'],
                'question_text': q['question_text'],
                'option_a': q['option_a'],
                'option_b': q['option_b'],
                'option_c': q['option_c'],
                'option_d': q['option_d']
            }
            for q in questions
        ]
        
        return jsonify(questions_list)
    except Exception as e:
        print(f"Error fetching questions: {e}")
        return jsonify({'error': 'Failed to fetch questions'}), 500

@app.route('/submit', methods=['POST'])
def submit():
    """
    Submit quiz answers and calculate score.
    Expects JSON with answers array.
    """
    try:
        data = request.get_json()
        answers = data.get('answers', [])
        course = session.get('current_course', None)
        
        if not course:
            return jsonify({'error': 'No course selected'}), 400
        
        # Calculate score
        score = calculate_score(answers, course)
        
        # Store score in session for result page
        session['score'] = score
        session['total'] = len(answers)
        
        # Store answers in session for review page
        session['user_answers'] = answers
        
        return jsonify({'score': score, 'total': len(answers)})
    except Exception as e:
        print(f"Error submitting quiz: {e}")
        return jsonify({'error': 'Failed to submit quiz'}), 500

def get_detailed_results(answers, course):
    """Get detailed results for review including question text and correct answers."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        review_data = []
        
        for answer_data in answers:
            question_id = answer_data.get('question_id')
            user_answer = answer_data.get('answer')
            
            cursor.execute(
                'SELECT question_text, option_a, option_b, option_c, option_d, correct_option, solution FROM questions WHERE id = ? AND course_code = ?',
                (question_id, course)
            )
            q = cursor.fetchone()
            
            if q:
                review_data.append({
                    'id': question_id,
                    'question_text': q['question_text'],
                    'option_a': q['option_a'],
                    'option_b': q['option_b'],
                    'option_c': q['option_c'],
                    'option_d': q['option_d'],
                    'user_answer': user_answer,
                    'correct_answer': q['correct_option'],
                    'solution': q['solution'] if 'solution' in q.keys() else "No detailed solution available."
                })
        
        conn.close()
        return review_data
    except Exception as e:
        print(f"Error getting detailed results: {e}")
        return []

def calculate_score(answers, course):
    """
    Calculate the score based on submitted answers.
    
    Args:
        answers: List of dicts with 'question_id' and 'answer' keys
        course: The course code for context
    
    Returns:
        Integer score (number of correct answers)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        score = 0
        
        # For each answer, check if it's correct
        for answer_data in answers:
            question_id = answer_data.get('question_id')
            user_answer = answer_data.get('answer')
            
            if user_answer is None:
                continue
            
            # Fetch the correct answer from database
            cursor.execute(
                'SELECT correct_option FROM questions WHERE id = ? AND course_code = ?',
                (question_id, course)
            )
            result = cursor.fetchone()
            
            if result:
                correct_answer = result['correct_option']
                if user_answer == correct_answer:
                    score += 1
        
        conn.close()
        return score
    except Exception as e:
        print(f"Error calculating score: {e}")
        return 0

@app.route('/result')
def result():
    """
    Result page route.
    Displays the final score.
    """
    score = session.get('score', 0)
    total = session.get('total', 10)
    course = session.get('current_course', 'Unknown')
    
    return render_template('result.html', score=score, total=total, course=course)

@app.route('/review')
def review():
    """Review page route."""
    user_answers = session.get('user_answers', [])
    course = session.get('current_course', 'Unknown')
    
    # Fetch detailed results on demand to avoid session size limits
    review_data = get_detailed_results(user_answers, course)
    
    return render_template('review.html', review_data=review_data, course=course)

# ==================== Error Handlers ====================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return render_template('error.html', message='Page not found'), 404

@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    return render_template('error.html', message='Server error occurred'), 500

# ==================== Main ====================

if __name__ == '__main__':
    app.run(debug=True, port=5000)
