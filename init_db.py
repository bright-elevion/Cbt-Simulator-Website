import os
import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
import werkzeug.security

# Load environment variables
if os.path.exists('key.env'):
    load_dotenv('key.env')
else:
    load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')

def get_db_connection():
    """Create a database connection and return it."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable not set")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create users table with email and password
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT,
            profile_picture TEXT,
            status TEXT DEFAULT 'Student',
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Create questions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id SERIAL PRIMARY KEY,
            course_code TEXT NOT NULL,
            topic TEXT NOT NULL,
            question_text TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_option TEXT NOT NULL,
            solution TEXT,
            content_json JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            UNIQUE(course_code, question_text),
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL

        )
        ''')

        cursor.execute('''
        ALTER TABLE questions
        ADD COLUMN IF NOT EXISTS content_json JSONB
        ''')

        # Track questions already shown to premium users so practice does not repeat them.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_question_views (
            user_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, question_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE
        )
        ''')

        # Track daily AI-generated-question usage for the ₦500 premium plan.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_ai_usage (
            user_id INTEGER NOT NULL,
            usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
            generated_questions INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, usage_date),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')
        
        # Persist question-level premium practice history for review and learning insights.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_question_attempts (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            attempt_id TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            course_code TEXT NOT NULL,
            topic TEXT,
            selected_option TEXT,
            correct_option TEXT,
            was_correct BOOLEAN NOT NULL,
            confidence TEXT,
            miss_reason TEXT,
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE,
            UNIQUE (user_id, attempt_id, question_id)
        )
        ''')
        cursor.execute('''
        ALTER TABLE premium_question_attempts
        ADD COLUMN IF NOT EXISTS time_spent_seconds NUMERIC(10, 2)
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_premium_attempts_user_course_time
        ON premium_question_attempts (user_id, course_code, answered_at DESC)
        ''')

        # Store one durable AI learning note and spaced-review state per user/question.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_mistake_notes (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            remember_text TEXT NOT NULL DEFAULT '',
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            provider TEXT,
            generated_at TIMESTAMP,
            next_review_at TIMESTAMP,
            last_reviewed_at TIMESTAMP,
            review_interval_days INTEGER NOT NULL DEFAULT 1,
            review_count INTEGER NOT NULL DEFAULT 0,
            last_review_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE,
            UNIQUE (user_id, question_id)
        )
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT ''
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS remember_text TEXT NOT NULL DEFAULT ''
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS provider TEXT
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS generated_at TIMESTAMP
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS next_review_at TIMESTAMP
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS last_reviewed_at TIMESTAMP
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS review_interval_days INTEGER NOT NULL DEFAULT 1
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS review_count INTEGER NOT NULL DEFAULT 0
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS last_review_result TEXT
        ''')
        cursor.execute('''
        ALTER TABLE premium_mistake_notes
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_premium_mistake_notes_due
        ON premium_mistake_notes (user_id, next_review_at)
        ''')
        # Create the notification table before applying compatibility ALTERs.
        # This ordering is safe for both fresh installations and existing databases.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS campusmate_notifications (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'CampusMate notification',
            message TEXT NOT NULL,
            action_url TEXT,
            source TEXT NOT NULL DEFAULT 'campusmate',
            severity TEXT NOT NULL DEFAULT 'info',
            entity_type TEXT,
            entity_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP,
            dismissed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE (user_id, dedupe_key)
        )
        ''')
        cursor.execute('''
        ALTER TABLE campusmate_notifications
        ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT 'CampusMate notification'
        ''')
        cursor.execute('''
        ALTER TABLE campusmate_notifications
        ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'campusmate'
        ''')
        cursor.execute('''
        ALTER TABLE campusmate_notifications
        ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT 'info'
        ''')
        cursor.execute('''
        ALTER TABLE campusmate_notifications
        ADD COLUMN IF NOT EXISTS entity_type TEXT
        ''')
        cursor.execute('''
        ALTER TABLE campusmate_notifications
        ADD COLUMN IF NOT EXISTS entity_id TEXT
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_campusmate_notifications_unread
        ON campusmate_notifications (user_id, read_at, dismissed_at, created_at DESC)
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_notification_log (
            id BIGSERIAL PRIMARY KEY,
            admin_user_id INTEGER NOT NULL,
            target_scope TEXT NOT NULL,
            target_user_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (target_user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_admin_notification_log_created
        ON admin_notification_log (created_at DESC)
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_reminder_state (
            user_id INTEGER PRIMARY KEY,
            last_shown_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')

        # Persist the verified learner context used by CampusMate personalisation.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS campusmate_profiles (
            user_id INTEGER PRIMARY KEY,
            university TEXT,
            department TEXT,
            level TEXT,
            semester TEXT,
            exam_date DATE,
            daily_minutes INTEGER NOT NULL DEFAULT 60,
            courses JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')

        # Store the latest adaptive plan so completed days survive recomputation.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS campusmate_exam_plans (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            exam_date DATE NOT NULL,
            available_minutes INTEGER NOT NULL,
            courses JSONB NOT NULL DEFAULT '[]'::jsonb,
            plan JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_campusmate_plans_user_status
        ON campusmate_exam_plans (user_id, status, updated_at DESC)
        ''')

        # Persist premium mock-exam configuration, question order, progress, and final scoring.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS premium_mock_exam_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT 'Premium Mock Exam',
            course_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            question_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            answers JSONB NOT NULL DEFAULT '{}'::jsonb,
            duration_seconds INTEGER NOT NULL DEFAULT 3600,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            submitted_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'active',
            score INTEGER,
            total INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_premium_mock_sessions_user_status
        ON premium_mock_exam_sessions (user_id, status, updated_at DESC)
        ''')

        # Persist deduplicated accountability messages for an in-app notification centre.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS campusmate_notifications (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            message TEXT NOT NULL,
            action_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP,
            dismissed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE (user_id, dedupe_key)
        )
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_campusmate_notifications_user_open
        ON campusmate_notifications (user_id, dismissed_at, created_at DESC)
        ''')

        # Keep a bounded history of CampusMate tutoring exchanges for continuity.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS campusmate_coach_history (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            question TEXT NOT NULL,
            response TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')
        cursor.execute('''
        ALTER TABLE campusmate_coach_history
        ADD COLUMN IF NOT EXISTS conversation_id TEXT
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_campusmate_history_user_conversation
        ON campusmate_coach_history (user_id, conversation_id, created_at ASC)
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_campusmate_history_user_time
        ON campusmate_coach_history (user_id, created_at DESC)
        ''')

        # Create scores table for leaderboard
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS scores (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            course_code TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')

        # Create feedback table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'unread',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')
        
        # Create payments table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            reference TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')

        # Create audit log table for admin actions
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES users (id) ON DELETE CASCADE
        )
        ''')
        
        # Check if default admin exists
        cursor.execute('SELECT * FROM users WHERE email = %s', (ADMIN_EMAIL,))
        admin = cursor.fetchone()
        
        if not admin:
            # Create default admin account
            hashed_password = generate_password_hash(ADMIN_PASSWORD)
            cursor.execute('''
            INSERT INTO users (username, email, password, role, status)
            VALUES (%s, %s, %s, %s, %s)
            ''', ('Admin', ADMIN_EMAIL, hashed_password, 'admin', 'Active'))
            print("✅ Default admin account created")
            
        
            # Questions Data
        questions_data = [
            # PHY101 - Motion and Forces
            ("PHY101", "Introduction to Physics & Measurement", "Which of the following quantities has the same dimensions as impulse?", "Force", "Momentum", "Energy", "Pressure", "B", "Impulse = force × time = change in momentum, so it has the same dimensions as momentum (MLT⁻¹)."),
            ("MTH102", "Applications of Integration", "Evaluate ∫₋₁¹(x⁷+x³+x)dx.", "-2", "-1", "0", "2", "C", "Explanation: The integrand is odd."),
            ("MTH102", "Applications of Integration", "The normal to y=x³ at x=1 is:", "y=x", "y=−x+2", "y=−x/3+4/3", "y=3x−2", "C", "Explanation: Tangent slope=3, so normal slope=−1/3."),
            ("MTH102", "Applications of Integration", "A cylindrical can with fixed volume has minimum surface area when:", "Height equals radius", "Height equals diameter", "Height equals half the diameter", "Height equals three times the radius", "B", "Explanation: The optimal condition is h=2r."),
            ("MTH102", "Applications of Integration", "Evaluate ∫₀¹ (1+x+x²+x³)dx.", "23/12", "25/12", "13/6", "11/6", "A", "Explanation: Sum the individual integrals."),
            ("MTH102", "Applications of Integration", "Which rule is needed to differentiate y=(3x²+1)/(x+2)?", "Chain Rule only", "Product Rule only", "Quotient Rule", "Power Rule only", "C", "Explanation: The function is a quotient."),
            ("MTH102", "Applications of Integration", "The volume generated by revolving y=√x, 0≤x≤9, about the x-axis is:", "81π/2", "81π/3", "81π/4", "27π", "A", "Explanation: V=π∫₀⁹x dx=81π/2."),
            ("MTH102", "Applications of Integration", "Evaluate ∫₀¹ (5x⁴−4x³+3x²−2x+1)dx.", "1", "2", "3", "4", "B", "Explanation: The integral equals 1−1+1−1+1=1? Carefully: 1−1+1−1+1=1. Therefore the correct answer should be Option A."),
            ("MTH102", "Applications of Integration", "Corrected version: Evaluate ∫₀¹ (10x⁴−4x³+3x²−2x+2)dx.", "2", "3", "4", "5", "B", "Explanation: 2−1+1−1+2=3."),
            ("MTH102", "Applications of Integration", "The derivative of e^(sinx) is:", "ecosx", "e^(sinx)cosx", "sine^(x)", "e^(cosx)", "B", "Explanation: Apply the chain rule."),
            ("MTH102", "Applications of Integration", "Find the maximum value of y=−2x²+8x−3.", "3", "5", "7", "9", "B", "Explanation: The vertex is at x=2, giving y=5."),
            ("MTH102", "Applications of Integration", "If ∫₂⁵f(x)dx=9, evaluate ∫₅²f(x)dx.", "-9", "0", "9", "18", "A", "Explanation: Reversing the limits changes the sign."),
            ("MTH102", "Applications of Integration", "The derivative of arctan(x) is:", "1/(1+x²)", "1/(1−x²)", "2x/(1+x²)", "tan²x", "A", "Explanation: Standard derivative formula."),
            ("MTH102", "Applications of Integration", "Evaluate ∫₀^(π/2) cos²x dx.", "π/2", "π/4", "1", "2", "B", "Explanation: Use the identity cos²x=(1+cos2x)/2."),
            ("MTH102", "Applications of Integration", "If a differentiable function has f'(a)=0 and f''(a)=0, then:", "a must be a maximum", "a must be a minimum", "No definite conclusion can be made", "a is not stationary", "C", "Explanation: Higher derivative tests or sign analysis are needed."),
            ("MTH102", "Applications of Integration", "Which statement is ALWAYS true?", "Every local minimum is an absolute minimum.", "Every absolute maximum is a stationary point.", "Every differentiable function is continuous.", "Every continuous function has a derivative.", "C", "Explanation: Differentiability always implies continuity, but the converse is false."),
            
        ]   
            
        inserted = 0

        for q in questions_data:
            cursor.execute("""
                INSERT INTO questions (
                    course_code,
                    topic,
                    question_text,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    correct_option,
                    solution
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (course_code, question_text)
                DO NOTHING
            """, q)

            if cursor.rowcount == 1:
                inserted += 1

        print(f"✅ Added {inserted} new questions.")
        
        conn.commit()
        conn.close()
        print("Database initialized successfully with PostgreSQL.")
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise

if __name__ == '__main__':
    init_db()
