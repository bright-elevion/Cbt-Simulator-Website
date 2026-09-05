"""
Admin Dashboard Routes
All admin routes are defined here to keep app.py clean
"""
from flask import render_template, request, redirect, url_for, jsonify, session, flash, current_app
import psycopg2
import psycopg2.extras
import os
import uuid
import re
import secrets
from dotenv import load_dotenv
from functools import wraps
from admin_utils import admin_required, log_admin_action, get_dashboard_stats, is_admin

if os.path.exists('key.env'):
    load_dotenv('key.env')
else:
    load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')

_ALLOWED_PAYMENT_STATUSES = {'pending', 'paid', 'failed', 'cancelled'}
_ALLOWED_USER_STATUSES = {'Active', 'Suspended', 'Inactive'}
_ALLOWED_SORTS = {'newest', 'score', 'attempts'}

def _form_text(name, limit):
    value = (request.form.get(name) or '').strip()
    return value[:limit]

def _validated_question_form():
    values = {
        'course_code': _form_text('course_code', 32).upper(),
        'topic': _form_text('topic', 160),
        'question_text': _form_text('question_text', 5000),
        'option_a': _form_text('option_a', 1000),
        'option_b': _form_text('option_b', 1000),
        'option_c': _form_text('option_c', 1000),
        'option_d': _form_text('option_d', 1000),
        'correct_option': _form_text('correct_option', 1).upper(),
        'solution': _form_text('solution', 10000),
    }
    required = ('course_code', 'question_text', 'option_a', 'option_b', 'option_c', 'option_d')
    if any(not values[field] for field in required):
        return None, 'Complete the course, question, and all four answer options.'
    if values['correct_option'] not in {'A', 'B', 'C', 'D'}:
        return None, 'Choose A, B, C, or D as the correct option.'
    return values, None

def get_db_connection():
    """Create a database connection and return it."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable not set")
    return psycopg2.connect(DATABASE_URL)

def register_admin_routes(app):
    """Register all admin routes with the Flask app"""
    
    # ==================== ADMIN DASHBOARD ====================
    
    @app.route('/admin/dashboard')
    @admin_required
    def admin_dashboard():
        """Main admin dashboard"""
        stats = get_dashboard_stats()
        return render_template('admin/dashboard.html', stats=stats)
    
    # ==================== USER MANAGEMENT ====================
    
    @app.route('/admin/users')
    @admin_required
    def admin_users():
        """View all users with search, filter and sort"""
        search = (request.args.get('search') or '').strip()[:80]
        status = (request.args.get('status') or '').strip()
        status = status if status in _ALLOWED_USER_STATUSES else ''
        sort = (request.args.get('sort') or 'newest').strip()
        sort = sort if sort in _ALLOWED_SORTS else 'newest'

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Base Query
        query = '''
            SELECT u.id, u.username, u.email, u.status, u.role, u.created_at,
                   COUNT(s.id) as total_attempts,
                   COALESCE(AVG(CAST(s.score AS FLOAT) / NULLIF(s.total, 0) * 100), 0) as avg_score
            FROM users u
            LEFT JOIN scores s ON u.id = s.user_id
            WHERE u.role = %s
        '''
        params = ['user']
        
        # Apply Filters
        if search:
            query += " AND (u.username ILIKE %s OR u.email ILIKE %s OR CAST(u.id AS TEXT) ILIKE %s)"
            search_param = f"%{search}%"
            params.extend([search_param, search_param, search_param])
            
        if status:
            query += " AND u.status = %s"
            params.append(status)
            
        query += " GROUP BY u.id"

        # Apply Sorting
        if sort == 'score':
            query += " ORDER BY avg_score DESC"
        elif sort == 'attempts':
            query += " ORDER BY total_attempts DESC"
        else: # default to newest
            query += " ORDER BY u.created_at DESC"
        query += " LIMIT 500"

        cursor.execute(query, tuple(params))
        users = cursor.fetchall()
        conn.close()
        
        return render_template('admin/users.html', 
                             users=users, 
                             search=search, 
                             status=status, 
                             sort=sort)
    
    @app.route('/admin/users/<int:user_id>/suspend', methods=['POST'])
    @admin_required
    def suspend_user(user_id):
        """Suspend a user"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET status = %s WHERE id = %s', ('Suspended', user_id))
            conn.commit()
            conn.close()
            
            log_admin_action(session['user_id'], 'SUSPEND_USER', 'user', user_id)
            flash(f'User {user_id} has been suspended.')
        except Exception:
            current_app.logger.exception('Admin user suspension failed')
            flash('We could not suspend this user right now.', 'error')
        
        return redirect(url_for('admin_users'))
    
    @app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
    @admin_required
    def delete_user(user_id):
        """Delete a user"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM users WHERE id = %s AND role = %s', (user_id, 'user'))
            conn.commit()
            conn.close()
            
            log_admin_action(session['user_id'], 'DELETE_USER', 'user', user_id)
            flash(f'User {user_id} has been deleted.')
        except Exception:
            current_app.logger.exception('Admin user deletion failed')
            flash('We could not delete this user right now.', 'error')
        
        return redirect(url_for('admin_users'))
    
    @app.route('/admin/users/<int:user_id>/details')
    @admin_required
    def user_details(user_id):
        """View detailed user information"""
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get user info
        cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            flash('User not found.')
            return redirect(url_for('admin_users'))
        
        # Get user scores
        cursor.execute('''
            SELECT * FROM scores WHERE user_id = %s ORDER BY created_at DESC
        ''', (user_id,))
        scores = cursor.fetchall()
        
        # Get payment info
        cursor.execute('''
            SELECT * FROM payments WHERE user_id = %s ORDER BY created_at DESC
        ''', (user_id,))
        payments = cursor.fetchall()
        
        conn.close()
        return render_template('admin/user_details.html', user=user, scores=scores, payments=payments)
    
    # ==================== QUESTION MANAGEMENT ====================
    
    @app.route('/admin/questions')
    @admin_required
    def admin_questions():
        """View all questions"""
        course = request.args.get('course', '')
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if course:
            cursor.execute('''
                SELECT * FROM questions WHERE course_code = %s ORDER BY created_at DESC
            ''', (course,))
        else:
            cursor.execute('SELECT * FROM questions ORDER BY created_at DESC')
        
        questions = cursor.fetchall()
        
        # Get unique courses
        cursor.execute('SELECT DISTINCT course_code FROM questions ORDER BY course_code')
        courses = [row['course_code'] for row in cursor.fetchall()]
        
        conn.close()
        return render_template('admin/questions.html', questions=questions, courses=courses, selected_course=course)
    
    @app.route('/admin/questions/add', methods=['GET', 'POST'])
    @admin_required
    def add_question():
        """Add a new question"""
        if request.method == 'POST':
            try:
                data, validation_error = _validated_question_form()
                if validation_error:
                    flash(validation_error, 'error')
                    return render_template('admin/add_question.html')
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO questions 
                    (course_code, topic, question_text, option_a, option_b, option_c, option_d, correct_option, solution, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    data.get('course_code'),
                    data.get('topic'),
                    data.get('question_text'),
                    data.get('option_a'),
                    data.get('option_b'),
                    data.get('option_c'),
                    data.get('option_d'),
                    data.get('correct_option'),
                    data.get('solution'),
                    session['user_id']
                ))
                conn.commit()
                conn.close()
                
                log_admin_action(session['user_id'], 'ADD_QUESTION', 'question', None, f"Course: {data.get('course_code')}")
                flash('Question added successfully!')
                return redirect(url_for('admin_questions'))
#
            except Exception:
                if conn:
                    conn.rollback()
                current_app.logger.exception('Admin question creation failed')
                flash('We could not add this question right now.', 'error')
#
        return render_template('admin/add_question.html')
    
    @app.route('/admin/questions/<int:question_id>/edit', methods=['GET', 'POST'])
    @admin_required
    def edit_question(question_id):
        """Edit a question"""
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT * FROM questions WHERE id = %s', (question_id,))
        question = cursor.fetchone()
        if not question:
            conn.close()
            flash('Question not found.', 'error')
            return redirect(url_for('admin_questions'))
        
        if request.method == 'POST':
            try:
                data, validation_error = _validated_question_form()
                if validation_error:
                    flash(validation_error, 'error')
                    cursor.close()
                    conn.close()
                    return render_template('admin/edit_question.html', question=question)
                cursor.execute('''
                    UPDATE questions SET
                    course_code = %s, topic = %s, question_text = %s,
                    option_a = %s, option_b = %s, option_c = %s, option_d = %s,
                    correct_option = %s, solution = %s
                    WHERE id = %s
                ''', (
                    data.get('course_code'),
                    data.get('topic'),
                    data.get('question_text'),
                    data.get('option_a'),
                    data.get('option_b'),
                    data.get('option_c'),
                    data.get('option_d'),
                    data.get('correct_option'),
                    data.get('solution'),
                    question_id
                ))
                conn.commit()
                conn.close()
                
                log_admin_action(session['user_id'], 'EDIT_QUESTION', 'question', question_id)
                flash('Question updated successfully!')
                return redirect(url_for('admin_questions'))
            except Exception:
                conn.rollback()
                current_app.logger.exception('Admin question update failed')
                flash('We could not update this question right now.', 'error')
        
        conn.close()
        return render_template('admin/edit_question.html', question=question)
    
    @app.route('/admin/questions/<int:question_id>/delete', methods=['POST'])
    @admin_required
    def delete_question(question_id):
        """Delete a question"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM questions WHERE id = %s', (question_id,))
            conn.commit()
            conn.close()
            
            log_admin_action(session['user_id'], 'DELETE_QUESTION', 'question', question_id)
            flash('Question deleted successfully!')
        except Exception:
            current_app.logger.exception('Admin question deletion failed')
            flash('We could not delete this question right now.', 'error')
        
        return redirect(url_for('admin_questions'))
    
    # ==================== PAYMENT MONITORING ====================
    
    @app.route('/admin/payments')
    @admin_required
    def admin_payments():
        """View payments with bounded, allowlisted filters."""
        status_filter = (request.args.get('status') or '').strip().lower()
        status_filter = status_filter if status_filter in _ALLOWED_PAYMENT_STATUSES else ''
        search = (request.args.get('search') or '').strip()[:80]
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            query = '''
                SELECT p.*, u.username, u.email
                FROM payments p JOIN users u ON p.user_id = u.id
                WHERE 1 = 1
            '''
            params = []
            if status_filter:
                query += ' AND p.status = %s'
                params.append(status_filter)
            if search:
                query += ' AND (u.username ILIKE %s OR u.email ILIKE %s OR CAST(p.id AS TEXT) ILIKE %s)'
                search_param = f'%{search}%'
                params.extend([search_param, search_param, search_param])
            query += ' ORDER BY p.created_at DESC LIMIT 500'
            cursor.execute(query, tuple(params))
            payments = cursor.fetchall()
            return render_template('admin/payments.html', payments=payments,
                                   status_filter=status_filter, search=search)
        except Exception:
            current_app.logger.exception('Admin payment list failed')
            flash('Payments are temporarily unavailable.', 'error')
            return redirect(url_for('admin_dashboard'))
        finally:
            if conn:
                conn.close()

    @app.route('/admin/payments/<int:payment_id>/confirm', methods=['POST'])
    @admin_required
    def confirm_payment(payment_id):
        """Confirm an existing payment idempotently."""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, status FROM payments WHERE id = %s FOR UPDATE', (payment_id,))
            payment = cursor.fetchone()
            if not payment:
                flash('Payment not found.', 'error')
            else:
                cursor.execute('UPDATE payments SET status = %s WHERE id = %s', ('paid', payment_id))
                cursor.execute('UPDATE users SET status = %s WHERE id = %s', ('Paid', payment[0]))
                conn.commit()
                log_admin_action(session['user_id'], 'CONFIRM_PAYMENT', 'payment', payment_id)
                flash('Payment confirmed.', 'success')
        except Exception:
            if conn:
                conn.rollback()
            current_app.logger.exception('Admin payment confirmation failed')
            flash('We could not confirm this payment right now.', 'error')
        finally:
            if conn:
                conn.close()
        return redirect(url_for('admin_payments'))
    
    @app.route('/admin/payments/<int:payment_id>/reject', methods=['POST'])
    @admin_required
    def reject_payment(payment_id):
        """Reject an existing payment idempotently."""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM payments WHERE id = %s FOR UPDATE', (payment_id,))
            payment = cursor.fetchone()
            if not payment:
                flash('Payment not found.', 'error')
            else:
                cursor.execute('UPDATE payments SET status = %s WHERE id = %s', ('failed', payment_id))
                conn.commit()
                log_admin_action(session['user_id'], 'REJECT_PAYMENT', 'payment', payment_id)
                flash('Payment rejected.', 'success')
        except Exception:
            if conn:
                conn.rollback()
            current_app.logger.exception('Admin payment rejection failed')
            flash('We could not reject this payment right now.', 'error')
        finally:
            if conn:
                conn.close()
        return redirect(url_for('admin_payments'))
    
    # ==================== ANALYTICS ====================
    
    @app.route('/admin/analytics')
    @admin_required
    def admin_analytics():
        """View bounded administrative analytics."""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT course_code, COUNT(*) as attempts,
                       AVG(CAST(score AS FLOAT) / NULLIF(total, 0) * 100) as avg_score
                FROM scores GROUP BY course_code ORDER BY attempts DESC LIMIT 200
            ''')
            course_stats = cursor.fetchall()
            cursor.execute('''
                SELECT u.username, u.email, COUNT(s.id) as attempts,
                       AVG(CAST(s.score AS FLOAT) / NULLIF(s.total, 0) * 100) as avg_score
                FROM users u JOIN scores s ON u.id = s.user_id
                WHERE u.role <> 'admin'
                GROUP BY u.id ORDER BY avg_score DESC LIMIT 10
            ''')
            top_performers = cursor.fetchall()
            cursor.execute('''
                SELECT DATE(created_at) AS date, COUNT(*) AS count
                FROM users
                WHERE created_at >= NOW() - INTERVAL '30 days' AND role = %s
                GROUP BY DATE(created_at) ORDER BY date
            ''', ('user',))
            registration_trend = cursor.fetchall()
            return render_template('admin/analytics.html', course_stats=course_stats,
                                   top_performers=top_performers,
                                   registration_trend=registration_trend)
        except Exception:
            current_app.logger.exception('Admin analytics loading failed')
            flash('Analytics are temporarily unavailable.', 'error')
            return redirect(url_for('admin_dashboard'))
        finally:
            if conn:
                conn.close()

    # ==================== FEEDBACK MANAGEMENT ====================
    
    @app.route('/admin/feedback')
    @admin_required
    def admin_feedback():
        """View feedback with bounded search and status filters."""
        status_filter = (request.args.get('status') or '').strip().lower()
        status_filter = status_filter if status_filter in {'unread', 'read'} else ''
        search = (request.args.get('search') or '').strip()[:80]
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            query = '''
                SELECT f.*, u.username, u.email
                FROM feedback f JOIN users u ON f.user_id = u.id
                WHERE 1 = 1
            '''
            params = []
            if status_filter:
                query += ' AND f.status = %s'
                params.append(status_filter)
            if search:
                query += ' AND (u.username ILIKE %s OR u.email ILIKE %s OR f.message ILIKE %s)'
                search_param = f'%{search}%'
                params.extend([search_param, search_param, search_param])
            query += ' ORDER BY f.created_at DESC LIMIT 500'
            cursor.execute(query, tuple(params))
            feedback = cursor.fetchall()
            return render_template('admin/feedback.html', feedback=feedback,
                                   status_filter=status_filter, search=search)
        except Exception:
            current_app.logger.exception('Admin feedback list failed')
            flash('Feedback is temporarily unavailable.', 'error')
            return redirect(url_for('admin_dashboard'))
        finally:
            if conn:
                conn.close()

    @app.route('/admin/feedback/<int:feedback_id>/mark-read', methods=['POST'])
    @admin_required
    def mark_feedback_read(feedback_id):
        """Mark feedback as read"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE feedback SET status = %s WHERE id = %s', ('read', feedback_id))
            conn.commit()
            conn.close()
            
            log_admin_action(session['user_id'], 'READ_FEEDBACK', 'feedback', feedback_id)
            flash('Feedback marked as read!')
        except Exception:
            current_app.logger.exception('Admin feedback update failed')
            flash('We could not update this feedback right now.', 'error')
        
        return redirect(url_for('admin_feedback'))
    
    # ==================== ADMIN MANAGEMENT ====================
    
    @app.route('/admin/admins')
    @admin_required
    def admin_admins():
        """View all admins"""
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT id, username, email, created_at FROM users WHERE role = %s', ('admin',))
        admins = cursor.fetchall()
        conn.close()
        return render_template('admin/admins.html', admins=admins)
    
    @app.route('/admin/admins/add', methods=['GET', 'POST'])
    @admin_required
    def add_admin():
        """Promote an existing learner or create an administrator account."""
        if request.method == 'POST':
            conn = None
            try:
                email = (request.form.get('email') or '').strip().lower()[:254]
                username = (request.form.get('username') or '').strip()[:80]
                if not email or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
                    flash('Enter a valid administrator email address.', 'error')
                    return render_template('admin/add_admin.html')
                if not username:
                    flash('Enter a username for the administrator.', 'error')
                    return render_template('admin/add_admin.html')
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Check if user exists
                cursor.execute('SELECT id FROM users WHERE email = %s', (email,))
                user = cursor.fetchone()
                
                if user:
                    # Update existing user to admin
                    cursor.execute('UPDATE users SET role = %s WHERE email = %s', ('admin', email))
                    flash(f'User {email} has been promoted to admin!')
                else:
                    # Create new admin user
                    from werkzeug.security import generate_password_hash
                    temp_password = secrets.token_urlsafe(16)
                    hashed_password = generate_password_hash(temp_password)
                    cursor.execute('''
                        INSERT INTO users (username, email, password, role, status)
                        VALUES (%s, %s, %s, %s, %s)
                    ''', (username, email, hashed_password, 'admin', 'Active'))
                    flash(f'Admin account created for {email}. Temporary password: {temp_password}')
                
                conn.commit()
                conn.close()
                
                log_admin_action(session['user_id'], 'ADD_ADMIN', 'user', None, f"Email: {email}")
                return redirect(url_for('admin_admins'))
            except Exception:
                if conn:
                    conn.rollback()
                current_app.logger.exception('Admin creation failed')
                flash('We could not create or promote this administrator right now.', 'error')
            finally:
                if conn:
                    conn.close()
        
        return render_template('admin/add_admin.html')
    
    @app.route('/admin/admins/<int:admin_id>/remove', methods=['POST'])
    @admin_required
    def remove_admin(admin_id):
        """Remove admin privileges"""
        try:
            # Prevent removing yourself
            if admin_id == session['user_id']:
                flash('You cannot remove your own admin privileges!')
                return redirect(url_for('admin_admins'))
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET role = %s WHERE id = %s', ('user', admin_id))
            conn.commit()
            conn.close()
            
            log_admin_action(session['user_id'], 'REMOVE_ADMIN', 'user', admin_id)
            flash('Admin privileges removed!')
        except Exception:
            current_app.logger.exception('Admin privilege removal failed')
            flash('We could not remove administrator privileges right now.', 'error')
        
        return redirect(url_for('admin_admins'))

    # ==================== NOTIFICATION MANAGEMENT ====================

    @app.route('/admin/notifications', methods=['GET', 'POST'])
    @admin_required
    def admin_notifications():
        """Compose and deliver an in-app notification to a controlled audience."""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            if request.method == 'POST':
                title = (request.form.get('title') or '').strip()[:160]
                message = (request.form.get('message') or '').strip()[:2000]
                scope = (request.form.get('target_scope') or 'all').strip().lower()
                severity = (request.form.get('severity') or 'info').strip().lower()
                action_url = (request.form.get('action_url') or '').strip()[:500]
                target_user_id = request.form.get('target_user_id', type=int)
                valid = True
                if not title or not message:
                    flash('Add a notification title and message before sending.', 'error')
                    valid = False
                elif scope not in {'all', 'premium', 'individual'}:
                    flash('Choose a valid notification audience.', 'error')
                    valid = False
                elif severity not in {'info', 'success', 'warning', 'critical'}:
                    flash('Choose a valid notification severity.', 'error')
                    valid = False
                elif action_url and not action_url.startswith('/'):
                    flash('Action links must point to an internal PrepCampus path.', 'error')
                    valid = False
                elif scope == 'individual' and not target_user_id:
                    flash('Select a learner for an individual notification.', 'error')
                    valid = False

                if valid:
                    if scope == 'individual':
                        cursor.execute("SELECT id FROM users WHERE id = %s AND role <> 'admin'", (target_user_id,))
                    elif scope == 'premium':
                        cursor.execute("""
                            SELECT DISTINCT u.id
                            FROM users u
                            JOIN payments p ON p.user_id = u.id AND p.status = 'paid'
                            WHERE u.role <> 'admin'
                            ORDER BY u.id
                        """)
                    else:
                        cursor.execute("SELECT id FROM users WHERE role <> 'admin' ORDER BY id")
                    recipients = [row['id'] for row in cursor.fetchall()]
                    if not recipients:
                        flash('No learners matched that audience.', 'error')
                    else:
                        batch_id = uuid.uuid4().hex
                        for recipient_id in recipients:
                            cursor.execute('''
                                INSERT INTO campusmate_notifications
                                    (user_id, kind, dedupe_key, title, message, action_url, source, severity)
                                VALUES (%s, 'admin_message', %s, %s, %s, %s, 'admin', %s)
                            ''', (recipient_id, f'admin:{batch_id}:{recipient_id}', title, message, action_url or None, severity))
                        cursor.execute('''
                            INSERT INTO admin_notification_log
                                (admin_user_id, target_scope, target_user_id, title, message)
                            VALUES (%s, %s, %s, %s, %s)
                        ''', (session['user_id'], scope, target_user_id if scope == 'individual' else None, title, message))
                        conn.commit()
                        log_admin_action(session['user_id'], 'SEND_NOTIFICATION', 'notification', None, f'Audience: {scope}; Recipients: {len(recipients)}')
                        suffix = '' if len(recipients) == 1 else 's'
                        flash(f'Notification sent to {len(recipients)} learner{suffix}.', 'success')
                        return redirect(url_for('admin_notifications'))

            cursor.execute("SELECT id, username, email FROM users WHERE role <> 'admin' ORDER BY username LIMIT 500")
            users = cursor.fetchall()
            cursor.execute('''
                SELECT l.*, u.username AS admin_username
                FROM admin_notification_log l
                JOIN users u ON u.id = l.admin_user_id
                ORDER BY l.created_at DESC, l.id DESC
                LIMIT 20
            ''')
            logs = cursor.fetchall()
            return render_template('admin/notifications.html', users=users, logs=logs)
        except Exception:
            if conn:
                conn.rollback()
            current_app.logger.exception('Admin notification management failed')
            flash('We could not process the notification request right now.', 'error')
            return redirect(url_for('admin_dashboard'))
        finally:
            if conn:
                conn.close()
