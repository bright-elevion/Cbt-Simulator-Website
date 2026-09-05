"""
Premium utilities for authorization, logging, and helper functions
"""
import psycopg2
import psycopg2.extras
from functools import wraps
from flask import session, redirect, url_for, flash, request
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

def is_premium_user(user_id):
    """Check if a user has an active premium subscription"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''
            SELECT status FROM payments 
            WHERE user_id = %s AND status = %s
            ORDER BY created_at DESC LIMIT 1
        ''', (user_id, 'paid'))
        payment = cursor.fetchone()
        conn.close()
        return payment is not None
    except Exception as e:
        print(f"Error checking premium status: {e}")
        return False

def premium_required(f):
    """Decorator to check if user has premium subscription"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access premium features.')
            return redirect(url_for('login', next=request.url))
        
        if not is_premium_user(session['user_id']):
            flash('Please upgrade to Premium to access this feature.')
            return redirect(url_for('premium'))
        
        return f(*args, **kwargs)
    return decorated_function

def get_premium_stats():
    """Get statistics for premium dashboard"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        stats = {}
        
        # Total premium users
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) as count 
            FROM payments WHERE status = %s
        ''', ('paid',))
        stats['premium_users'] = cursor.fetchone()['count']
        
        # Total revenue
        cursor.execute('''
            SELECT SUM(amount) as total 
            FROM payments WHERE status = %s
        ''', ('paid',))
        result = cursor.fetchone()
        stats['total_revenue'] = result['total'] if result['total'] else 0
        
        # Active subscriptions this month
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) as count 
            FROM payments 
            WHERE status = %s 
            AND EXTRACT(MONTH FROM created_at) = EXTRACT(MONTH FROM NOW())
            AND EXTRACT(YEAR FROM created_at) = EXTRACT(YEAR FROM NOW())
        ''', ('paid',))
        stats['active_this_month'] = cursor.fetchone()['count']
        
        conn.close()
        return stats
    except Exception as e:
        print(f"Error getting premium stats: {e}")
        return {}

def get_user_premium_info(user_id):
    """Get premium subscription info for a user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''
            SELECT * FROM payments 
            WHERE user_id = %s 
            ORDER BY created_at DESC LIMIT 1
        ''', (user_id,))
        payment = cursor.fetchone()
        conn.close()
        return payment
    except Exception as e:
        print(f"Error getting user premium info: {e}")
        return None


def get_login_retention_reminder(user_id):
    """Return one throttled retention reminder for a premium learner, if due.

    This helper is deliberately fail-safe: missing history or an unavailable table
    never prevents a user from logging in.
    """
    from datetime import datetime

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            """
            SELECT 1
            FROM payments
            WHERE user_id = %s AND status = 'paid'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        if not cursor.fetchone():
            conn.close()
            return None

        cursor.execute(
            """
            SELECT
                course_code,
                COALESCE(NULLIF(topic, ''), course_code) AS topic,
                MAX(answered_at) AS last_studied
            FROM premium_question_attempts
            WHERE user_id = %s
            GROUP BY course_code, COALESCE(NULLIF(topic, ''), course_code)
            HAVING MAX(answered_at) <= NOW() - INTERVAL '7 days'
            ORDER BY MAX(answered_at) ASC
            LIMIT 1
            """,
            (user_id,),
        )
        study = cursor.fetchone()
        if not study:
            conn.close()
            return None

        cursor.execute(
            "SELECT last_shown_at FROM premium_reminder_state WHERE user_id = %s",
            (user_id,),
        )
        state = cursor.fetchone()
        if state and state['last_shown_at']:
            hours_since_shown = (datetime.utcnow() - state['last_shown_at']).total_seconds() / 3600
            if hours_since_shown < 24:
                conn.close()
                return None

        cursor.execute(
            """
            INSERT INTO premium_reminder_state (user_id, last_shown_at)
            VALUES (%s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id)
            DO UPDATE SET last_shown_at = EXCLUDED.last_shown_at
            """,
            (user_id,),
        )
        conn.commit()
        conn.close()

        days = max(1, (datetime.utcnow() - study['last_studied']).days)
        if days >= 30:
            retention = 35
        elif days >= 14:
            retention = 65
        elif days >= 7:
            retention = 80
        else:
            retention = 90

        return (
            f"Reminder: You studied {study['topic']} {days} days ago. "
            f"Your estimated retention is around {retention}% and may be dropping. "
            "Review it today from Premium → Weakness Analysis."
        )
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return None


def get_retention_percent(days):
    """Estimate retention using transparent anchor points for the UI."""
    anchors = ((0, 100), (1, 90), (7, 80), (14, 65), (30, 35), (60, 20))
    days = max(0, int(days))
    for (left_day, left_value), (right_day, right_value) in zip(anchors, anchors[1:]):
        if days <= right_day:
            ratio = (days - left_day) / (right_day - left_day)
            return round(left_value + ratio * (right_value - left_value))
    return anchors[-1][1]
