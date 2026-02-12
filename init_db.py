import sqlite3
import os
import werkzeug.security

DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'quiz.db')

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create questions table
    cursor.execute('DROP TABLE IF EXISTS questions')
    cursor.execute('''
    CREATE TABLE questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_code TEXT NOT NULL,
        question_text TEXT NOT NULL,
        option_a TEXT NOT NULL,
        option_b TEXT NOT NULL,
        option_c TEXT NOT NULL,
        option_d TEXT NOT NULL,
        correct_option TEXT NOT NULL,
        solution TEXT
    )
    ''')
    
    # Create users table with email and password
    cursor.execute('DROP TABLE IF EXISTS users')
    cursor.execute('''
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Create payments table
    cursor.execute('DROP TABLE IF EXISTS payments')
    cursor.execute('''
    CREATE TABLE payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        reference TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')
    
    # Core questions
    questions = [
        ("PHY101", "The slope of a velocity–time graph gives:", "Speed", "Distance", "Acceleration", "Momentum", "C", "The slope of a velocity-time graph represents the rate of change of velocity, which is acceleration (a = Δv/Δt)."),
        ("PHY101", "The area under a velocity–time graph gives:", "Acceleration", "Displacement", "Force", "Energy", "B", "The area under a velocity-time graph represents the product of velocity and time, which equals displacement (s = v × t)."),
        # COS 103
        ("COS 103", "x = 5\nx = x + x\nprint(x)", "5", "10", "15", "25", "B", "x starts at 5. x + x is 5 + 5 = 10. So x becomes 10."),
        ("COS 103", "arr = [3, 6, 9]\nprint(arr[0] + arr[2])", "9", "12", "15", "18", "B", "arr[0] is 3 and arr[2] is 9. 3 + 9 = 12."),
        ("COS 103", "for i in range(3):\n    print(i)\nHow many numbers are printed?", "2", "3", "4", "Infinite", "B", "range(3) generates 0, 1, 2. That is 3 numbers."),
        ("COS 103", "x = 2\nwhile x < 10:\n    x = x * 2\nprint(x)", "8", "10", "16", "32", "C", "x starts at 2. Loop 1: x=4. Loop 2: x=8. Loop 3: x=16. 16 is not < 10, so loop ends and prints 16."),
        ("COS 103", "def add(a,b):\n    return a+b\n\nprint(add(4,5))", "20", "9", "45", "None", "B", "The function add(4,5) returns 4 + 5 = 9."),
        ("COS 103", "Which flowchart symbol represents input/output?", "Diamond", "Rectangle", "Parallelogram", "Oval", "C", "A parallelogram is used for input and output operations in flowcharts."),
        ("COS 103", "arr = [1,2,3,4]\ntotal = 0\n\nfor x in arr:\n    total += x\n\nprint(total)", "8", "9", "10", "24", "C", "The loop sums the elements: 1 + 2 + 3 + 4 = 10."),
        ("COS 103", "def f(x):\n    print(x*2)\n\ny = f(5)\nprint(y)", "10", "5", "10 then None", "None only", "C", "f(5) prints 10. Since it has no return statement, it returns None. So y is None. The output is 10 then None."),
        ("COS 103", "Binary of decimal 6 is", "100", "101", "110", "111", "C", "6 in binary is 110 (4*1 + 2*1 + 1*0)."),
        ("COS 103", "Which is NOT a programming data type?", "Integer", "Boolean", "Float", "Scanner", "D", "Scanner is a class (e.g., in Java) used for input, not a basic data type like Integer, Boolean, or Float."),
    ]
    
    cursor.executemany('INSERT INTO questions (course_code, question_text, option_a, option_b, option_c, option_d, correct_option, solution) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', questions)
    
    conn.commit()
    conn.close()
    print("Database initialized successfully with updated schema and COS 103 questions.")

if __name__ == '__main__':
    init_db()
