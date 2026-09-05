"""
Admin utilities for authorization, logging, and helper functions
"""
import psycopg2
import psycopg2.extras
from functools import wraps
from flask import session, redirect, url_for, flash, request, current_app
import os
from dotenv import load_dotenv

if os.path.exists('key.env'):
    load_dotenv('key.env')
else:
    load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    """Create a database connection and return it."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable not set")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def admin_required(f):
    """Require a live database-backed administrator session."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this feature.', 'info')
            return redirect(url_for('login', next=request.full_path))
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('SELECT role, status FROM users WHERE id = %s', (session['user_id'],))
            user = cursor.fetchone()
        except Exception:
            current_app.logger.exception('Admin authorization lookup failed')
            flash('Administrator access is temporarily unavailable. Please try again.', 'error')
            return redirect(url_for('index'))
        finally:
            if conn:
                conn.close()
        if not user or user['role'] != 'admin' or str(user['status'] or '').lower() in {'suspended', 'inactive'}:
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def log_admin_action(admin_id, action, target_type=None, target_id=None, details=None):
    """Write an audit record without disrupting the primary admin action."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO audit_logs (admin_id, action, target_type, target_id, details)
        VALUES (%s, %s, %s, %s, %s)
        ''', (admin_id, action, target_type, target_id, details))
        conn.commit()
    except Exception:
        current_app.logger.exception('Admin audit log write failed')
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def is_admin(user_id):
    """Check whether a user is an active administrator."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT role, status FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()
        return bool(user and user['role'] == 'admin' and str(user['status'] or '').lower() not in {'suspended', 'inactive'})
    except Exception:
        current_app.logger.exception('Admin status lookup failed')
        return False
    finally:
        if conn:
            conn.close()

def get_dashboard_stats():
    """Get statistics for admin dashboard"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        stats = {}
        
        # Total users
        cursor.execute('SELECT COUNT(*) as count FROM users WHERE role = %s', ('user',))
        stats['total_users'] = cursor.fetchone()['count']
        
        # Total questions
        cursor.execute('SELECT COUNT(*) as count FROM questions')
        stats['total_questions'] = cursor.fetchone()['count']
        
        # Total quiz attempts
        cursor.execute('SELECT COUNT(*) as count FROM scores')
        stats['total_attempts'] = cursor.fetchone()['count']
        
        # Paid users
        cursor.execute('SELECT COUNT(DISTINCT user_id) as count FROM payments WHERE status = %s', ('paid',))
        stats['paid_users'] = cursor.fetchone()['count']
        
        # Unread feedback
        cursor.execute('SELECT COUNT(*) as count FROM feedback WHERE status = %s', ('unread',))
        stats['unread_feedback'] = cursor.fetchone()['count']
        
        # Total revenue (in naira)
        cursor.execute('SELECT SUM(amount) as total FROM payments WHERE status = %s', ('paid',))
        result = cursor.fetchone()
        stats['total_revenue'] = result['total'] if result['total'] else 0
        
        return stats
    except Exception:
        current_app.logger.exception('Admin dashboard statistics failed')
        return {}
    finally:
        if 'conn' in locals() and conn:
            conn.close()
