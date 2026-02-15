import sqlite3
import os
import werkzeug.security

DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'quiz.db')

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create questions table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS questions (
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
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT,
        profile_picture TEXT,
        status TEXT DEFAULT 'Student',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Create scores table for leaderboard
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        course_code TEXT NOT NULL,
        score INTEGER NOT NULL,
        total INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    # Create feedback table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')
    
    # Create payments table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        reference TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')
    
    # Check if questions already exist to avoid duplicates
    cursor.execute('SELECT COUNT(*) FROM questions')
    if cursor.fetchone()[0] == 0:
        # Questions
        questions = [
            ("PHY101", "The slope of a velocity–time graph gives:", "Speed", "Distance", "Acceleration", "Momentum", "C", "The slope of a velocity-time graph represents the rate of change of velocity, which is acceleration (a = Δv/Δt)."),
            ("PHY101", "The area under a velocity–time graph gives:", "Acceleration", "Displacement", "Force", "Energy", "B", "The area under a velocity-time graph represents the product of velocity and time, which equals displacement (s = v × t)."),
            ("PHY101", "Which quantity is conserved in ideal projectile motion?", "Vertical velocity", "Horizontal velocity", "Kinetic energy", "Momentum", "B", "In ideal projectile motion (no air resistance), there is no horizontal force, so horizontal velocity remains constant."),
            ("PHY101", "The dimensional formula of energy is", "MLT^-2", "ML^2T^-2", "ML^2T^-1", "MLT^-1", "B", "Energy (Work) = Force x Distance = (MLT^-2) x L = ML^2T^-2."),
            ("PHY101", "A smooth surface implies", "High friction", "Zero friction", "Constant velocity", "No gravity", "B", "In physics problems, a 'smooth' surface is an idealized surface with no frictional resistance."),
            ("PHY101", "The force opposing motion between surfaces is", "Gravity", "Tension", "Friction", "Normal force", "C", "Friction is the force that resists the relative motion of solid surfaces sliding against each other."),
            ("PHY101", "The velocity of a freely falling body increases", "Linearly with time", "Exponentially", "Decreases", "Remains constant", "A", "v = u + gt. Since g is constant, velocity increases linearly with time."),
            ("PHY101", "The mass of a body on the moon is", "Less than on Earth", "More than on Earth", "Same as on Earth", "Zero", "C", "Mass is the amount of matter in an object and does not change with location. Weight changes, but mass stays the same."),
            ("PHY101", "Weight is measured in", "Kilograms", "Newtons", "Joules", "Watts", "B", "Weight is a force (W = mg), and the SI unit for force is the Newton."),
            ("PHY101", "The coefficient of friction has unit", "Newton", "Pascal", "No unit", "Meter", "C", "Coefficient of friction (μ) = Friction Force / Normal Force. Since it's a ratio of two forces, the units cancel out."),
            ("PHY101", "A horizontal distance-time graph indicates", "Constant speed", "Zero speed (Rest)", "Constant acceleration", "Infinite speed", "B", "A horizontal line on a distance-time graph means the distance is not changing as time passes, so the object is at rest."),
            ("PHY101", "Which force is perpendicular to a surface?", "Friction", "Tension", "Normal force", "Weight", "C", "The normal force is the component of a contact force that is perpendicular to the surface that an object contacts."),
            ("PHY101", "The equation F = ma represents", "Newton's 1st Law", "Newton's 2nd Law", "Newton's 3rd Law", "Law of Gravitation", "B", "Newton's Second Law states that the acceleration of an object is directly proportional to the net force acting on it and inversely proportional to its mass."),
            ("PHY101", "A projectile at 45 degrees has maximum", "Height", "Time of flight", "Range", "Velocity", "C", "For a given initial velocity, the maximum horizontal range is achieved at a launch angle of 45 degrees."),
            ("PHY101", "Which of these is not a simple machine?", "Lever", "Pulley", "Internal combustion engine", "Inclined plane", "C", "Simple machines include the lever, wheel and axle, pulley, inclined plane, wedge, and screw. An engine is a complex machine."),
            ("PHY101", "Efficiency of a real machine is always", "100%", "More than 100%", "Less than 100%", "0%", "C", "Due to friction and other energy losses (like heat), real machines always have an efficiency of less than 100%."),
            ("PHY101", "The slope of a distance-time graph gives", "Velocity", "Acceleration", "Force", "Work", "A", "Slope = Change in Distance / Change in Time = Velocity."),
            ("PHY101", "Acceleration is the rate of change of", "Distance", "Displacement", "Velocity", "Time", "C", "Acceleration is defined as the rate at which velocity changes over time."),
            ("PHY101", "The SI unit of acceleration is", "m/s", "m/s^2", "kg.m/s", "N.m", "B", "Acceleration is change in velocity (m/s) per unit time (s), resulting in meters per second squared (m/s²)."),
            ("PHY101", "The work-energy principle relates work to", "Change in PE", "Change in KE", "Total energy", "Power", "B", "The work-energy theorem states that the net work done on an object is equal to its change in kinetic energy."),
            ("PHY101", "If speed triples, KE becomes", "3 times", "6 times", "9 times", "12 times", "C", "KE = 1/2 mv². If v becomes 3v, KE becomes 1/2 m(3v)² = 1/2 m(9v²) = 9 times the original KE."),
            ("PHY101", "The force F on an object depends on its velocity v, the density ρ (rho) of the fluid, and the cross-sectional area A, according to the equation F = kρᵃvᵇAᶜ. Using dimensional analysis, what are the values of a, b, and c respectively?", "a=1, b=2, c=1", "a=1, b=1, c=2", "a=2, b=1, c=1", "a=1, b=2, c=2", "A", "No explanation"),
            ("PHY101", "A physical quantity P is related to four observables a, b, c, and d as P = a³b² / (√c * d). If the percentage errors in a, b, c, d are 1%, 3%, 4%, and '2%' respectively, calculate the total percentage error in P.", "10%", "13%", "7%", "12%", "B", "No explanation"),
            ("PHY101", "The period of oscillation T of a simple pendulum depends on the mass m, length l, and acceleration due to gravity g. Which of the following correctly represents the dimensional relationship for T?", "T ∝ √(l/g)", "T ∝ √(g/l)", "T ∝ l * g", "T ∝ m * l/g", "A", "No Explanation"),
            ("PHY101", "Convert a power of 100 Watts into a new system of units where the unit of mass is 10 kg, the unit of length is 100 m, and the unit of time is 1 minute (60 s). What is the numerical value in the new system?", "2.16 x 10⁶", "2.16 x 10⁴", "3.6 x 10⁵", "6.0 x 10³", "A", "No explanation"),
            ("PHY101", "The velocity v of water waves depends on wavelength λ (lambda), density ρ (rho), and gravity g. Using dimensional analysis, which relation is correct?", "v² ∝ gλ", "v ∝ gλ", "v ∝ ρgλ", "v² ∝ g/λ", "A", "No Explanation"),
            ("PHY101", "A bird flies northeast for 95.0 km. Taking the x-axis as East and y-axis as North, what is the displacement vector in km?", "67.2i + 67.2j", "95.0i + 95.0j", "47.5i + 47.5j", "82.3i + 47.5j", "A", "No Explanation"),
            ("PHY101", "A cyclist rides 5.0 km East, then 10.0 km at 20° West of North, then 8.0 km West. What is the magnitude of the final displacement from the starting point?", "11.3 km", "10.2 km", "9.4 km", "12.5 km", "B", "No Explanation"),
            ("PHY101", "The position of a particle is r(t) = 3.0t²i + 5.0j - 6.0tk m. What is the magnitude of its velocity at t = 1.0 s?", "6.0 m/s", "12.0 m/s", "8.5 m/s", "10.0 m/s", "C", "No Explanation"),
            ("PHY101", "A car accelerates from rest at 2.0 m/s² for 10 s, travels at constant speed for 30 s, and decelerates at 4.0 m/s² until it stops. Calculate the total distance traveled.", "800 m", "750 m", "700 m", "850 m", "B", "No Explanation"),
            ("PHY101", "A boat has an acceleration of 2.0 m/s²i and an initial velocity of (2.0i + 1.0j) m/s. What is its position vector at t = 10 s?", "120i + 10j", "100i + 20j", "110i + 10j", "120i + 20j", "A", "No Explanation"),
            ("PHY101", "A bullet is shot horizontally from a height of 1.5 m with a speed of 200 m/s. How far does it travel horizontally before hitting the ground? (Take g = 9.8 m/s²)", "110.6 m", "150.2 m", "95.4 m", "200.0 m", "A", "No Explanation"),
            ("PHY101", "A marble rolls off a 1.0 m high table and hits the floor 3.0 m away horizontally. What was its initial speed? (Take g = 9.8 m/s²)", "6.64 m/s", "4.52 m/s", "3.00 m/s", "9.80 m/s", "A", "No Explanation"),
            ("PHY101", "A projectile launched at 30° lands 20 s later at the same height. What is its initial speed? (Take g = 9.8 m/s²)", "98 m/s", "392 m/s", "150 m/s", "196 m/s", "D", "No Explanation"),
            ("PHY101", "A rock is thrown off a 100 m cliff at 30 m/s at an angle of 53° above horizontal. How long does it take to hit the ground? (Take g = 9.8 m/s², sin 53° ≈ 0.8)", "5.4 s", "8.2 s", "6.5 s", "7.1 s", "D", "No Explanation"),
            ("PHY101", "A 30.0-kg girl in a swing is held at rest by a horizontal force F such that the ropes make 30.0° with the vertical. What is the magnitude of the horizontal force F? (Take g = 9.8 m/s²)", "294.0 N", "169.7 N", "147.0 N", "196.5 N", "B", "No Explanation"),
            ("PHY101", "An elevator of mass 1700 kg accelerates upward at 1.20 m/s². What is the tension in the supporting cable? (Take g = 9.8 m/s²)", "18,700 N", "16,660 N", "14,620 N", "20,400 N", "A", "No Explanation"),
            ("PHY101", "A 20.0-g ball hangs from the roof of a car. When the car accelerates, the string makes an angle of 35.0° with the vertical. What is the acceleration of the car? (Take g = 9.8 m/s²)", "5.62 m/s²", "6.86 m/s²", "9.80 m/s²", "4.25 m/s²", "B", "No Explanation"),
            ("PHY101", "A  1200-kg car moving at 20 m/s brakes to a stop over a distance of 50 m. What is the average braking force?", "2400 N", "6000 N", "4800 N", "3600 N", "C", "No Explanation"),
            ("PHY101", "A spring (k = 500 N/m) is compressed by 10 cm and used to launch a 0.2-kg ball vertically. What is the maximum height reached by the ball? (Take g = 9.8 m/s²)", "2.50 m", "0.64 m", "1.28 m", "1.50 m", "C", "No Explanation"),
            ("PHY101", "A lever with an effort arm of 1.2 m and a resistance arm of 0.3 m is used to lift a 400-N load with an actual effort of 120 N. What is the efficiency of the lever?", "83.3%", "75.0%", "90.0%", "66.7%", "A", "No Explanation"),
            ("PHY101", "Which quantity is a vector?", "Work", "Energy", "Momentum", "Power", "C", "No Explanation"),
            ("PHY101", "The dimensional formula of force is: ", "MLT⁻¹", "MLT⁻²", "ML²T⁻²", "ML²T⁻¹", "B", "No Explanation"),
            ("PHY101", "A body moves 9 m east and 12 m north. Its displacement is: ", "15 m", "21 m", "3 m", "108 m", "A", "No Explanation"),

            # COS 103
            ("COS 103", "x = 5\nx = x + x\nprint(x)", "5", "10", "15", "25", "B", "x starts at 5. x + x is 5 + 5 = 10. So x becomes 10."),
            ("COS 103", "arr = [3, 6, 9]\nprint(arr[0] + arr[2])", "9", "12", "15", "18", "B", "arr[0] is 3 and arr[2] is 9. 3 + 9 = 12."),
            ("COS 103", "for i in range(3):\n    print(i)\nHow many numbers are printed?", "2", "3", "4", "Infinite", "B", "range(3) generates 0, 1, 2. That is 3 numbers."),
            ("COS 103", "x = 2\nwhile x < 10:\n    x = x * 2\nprint(x)", "8", "10", "16", "32", "C", "x starts at 2. Loop 1: x=4. Loop 2: x=8. Loop 3: x=16. 16 is not < 10, so loop ends and prints 16."),
            ("COS 103", "def add(a,b):\n    return a+b\n\nprint(add(4,5))", "20", "9", "45", "None", "B", "The function add(4,5) returns 4 + 5 = 9."),
            ("COS 103", "Which flowchart symbol represents input/output?", "Diamond", "Rectangle", "Parallelogram", "Oval", "C", "A parallelogram is used for input and output operations in flowcharts."),
            ("COS 103", "arr = [1,2,3,4]\ntotal = 0\n\nfor x in arr:\n    total += x\n\nprint(total)", "8", "9", "10", "24", "C", "The loop sums the elements: 1 + 2 + 3 + 4 = 10."),
            ("MTH101", "If the roots of ax² + bx + c = 0 are in the ratio m:n, prove that mnb² = ac(m+n)².", "Proof provided", "Proof not possible", "Identity is false", "Requires complex numbers", "A", "Proof: Let the roots be r₁ and r₂. Given r₁/r₂ = m/n, so r₁ = (m/n)r₂. From Vieta\'s formulas, r₁ + r₂ = -b/a and r₁r₂ = c/a. Substitute r₁: (m/n)r₂ + r₂ = -b/a => r₂((m+n)/n) = -b/a => r₂ = -nb/(a(m+n)). Also, ((m/n)r₂)r₂ = c/a => (m/n)r₂² = c/a => r₂² = nc/(am). Substitute r₂: (-nb/(a(m+n)))² = nc/(am). n²b²/(a²(m+n)²) = nc/(am). Divide by an: mnb² = ac(m+n)². This completes the proof."),
            ("MTH101", "Solve the inequality (x² - 3x + 2)/(x-3) > 0.", "(1, 2) U (3, inf)", "(-inf, 1) U (2, 3)", "(1, 3)", "(2, inf)", "A", "First, factor the numerator: x² - 3x + 2 = (x-1)(x-2). So the inequality is (x-1)(x-2)/(x-3) > 0. The critical points are x = 1, x = 2, x = 3. We test intervals: 1) x < 1 (e.g., x=0): (-1)(-2)/(-3) = -2/3 < 0 (False). 2) 1 < x < 2 (e.g., x=1.5): (0.5)(-0.5)/(-1.5) = 0.25/1.5 > 0 (True). 3) 2 < x < 3 (e.g., x=2.5): (1.5)(0.5)/(-0.5) = -1.5 < 0 (False). 4) x > 3 (e.g., x=4): (3)(2)/(1) = 6 > 0 (True). So the solution is (1, 2) U (3, inf)."),
            ("MTH101", "Find the inverse of the function f(x) = (2x-3)/(5x+4) and state its domain.", "f⁻¹(x) = (-4x-3)/(5x-2), Domain: x ≠ 2/5", "f⁻¹(x) = (4x+3)/(2-5x), Domain: x ≠ 2/5", "f⁻¹(x) = (4x+3)/(2-5x), Domain: x ≠ -4/5", "f⁻¹(x) = (-4x-3)/(5x-2), Domain: x ≠ -4/5", "A", "Let y = (2x-3)/(5x+4). To find the inverse, swap x and y: x = (2y-3)/(5y+4). x(5y+4) = 2y-3. 5xy + 4x = 2y - 3. 5xy - 2y = -4x - 3. y(5x - 2) = -4x - 3. y = (-4x - 3) / (5x - 2). So, f⁻¹(x) = (-4x - 3) / (5x - 2). The domain of f⁻¹(x) is all real numbers except where the denominator is zero: 5x - 2 ≠ 0 => 5x ≠ 2 => x ≠ 2/5."),
            ("MTH101", "A variable V varies directly as the square of x and inversely as y. If x increases by 20% and y decreases by 10%, find the percentage change in V.", "60% increase", "20% increase", "10% decrease", "50% increase", "A", "The variation can be written as V = kx²/y. Let the original values be x and y. The new values are x′ = x + 0.20x = 1.2x and y′ = y - 0.10y = 0.9y. The new V′ = k(x′)²/y′ = k(1.2x)²/(0.9y) = k(1.44x²)/(0.9y) = (1.44/0.9) * (kx²/y) = 1.6 * V. The percentage change is ((V′ - V)/V) * 100% = ((1.6V - V)/V) * 100% = (0.6V/V) * 100% = 60% increase."),
            ("MTH101", "In a group of 150 people, 70 like Math, 60 like Physics, and 50 like Chemistry. 30 like Math and Physics, 20 like Physics and Chemistry, and 25 like Math and Chemistry. 10 like all three. Find the number of people who like exactly two subjects.", "45", "50", "55", "60", "A", "Let M, P, C be the sets of people who like Math, Physics, and Chemistry respectively. Given: n(M)=70, n(P)=60, n(C)=50, n(M∩P)=30, n(P∩C)=20, n(M∩C)=25, n(M∩P∩C)=10. The number of people who like exactly two subjects is given by: n(M∩P only) + n(P∩C only) + n(M∩C only) = (30 - 10) + (20 - 10) + (25 - 10) = 20 + 10 + 15 = 45."),
            ("MTH101", "Solve for x in the equation logₓ 2 + log₂ x = 2.5.", "x = 4, sqrt(2)", "x = 2, 4", "x = 2, sqrt(2)", "x = 4, 2", "A", "Let y = log₂ x. Then logₓ 2 = 1/y. The equation becomes 1/y + y = 2.5 = 5/2. Multiply by 2y: 2 + 2y² = 5y. 2y² - 5y + 2 = 0. Factor the quadratic: (2y - 1)(y - 2) = 0. So, 2y - 1 = 0 => y = 1/2 or y - 2 = 0 => y = 2. Substitute back y = log₂ x: Case 1: log₂ x = 1/2 => x = 2^(1/2) = sqrt(2). Case 2: log₂ x = 2 => x = 2² = 4. So the solutions are x = 4, sqrt(2)."),
            ("MTH101", "Find the range of values of k for which the equation x² + kx + 4 = 0 has real and distinct roots.", "k < -4 or k > 4", "-4 < k < 4", "k <= -4 or k >= 4", "-4 <= k <= 4", "A", "For a quadratic equation ax² + bx + c = 0 to have real and distinct roots, the discriminant (Δ = b² - 4ac) must be greater than 0. Here, a=1, b=k, c=4. So, k² - 4(1)(4) > 0. k² - 16 > 0. (k - 4)(k + 4) > 0. The critical points are k = -4 and k = 4. We test intervals: 1) k < -4 (e.g., k=-5): (-9)(-1) = 9 > 0 (True). 2) -4 < k < 4 (e.g., k=0): (-4)(4) = -16 < 0 (False). 3) k > 4 (e.g., k=5): (1)(9) = 9 > 0 (True). So the range of values for k is k < -4 or k > 4."),
            ("MTH101", "Solve by completing the square: 3x² - 10x + 3 = 0.", "x = 1/3, 3", "x = -1/3, 3", "x = 1/3, -3", "x = -1/3, -3", "A", "Given 3x² - 10x + 3 = 0. Divide by 3: x² - (10/3)x + 1 = 0. Move the constant term: x² - (10/3)x = -1. To complete the square, add ((-10/3)/2)² = (-5/3)² = 25/9 to both sides: x² - (10/3)x + 25/9 = -1 + 25/9. (x - 5/3)² = -9/9 + 25/9 = 16/9. Take the square root of both sides: x - 5/3 = ±√(16/9) = ±4/3. Case 1: x - 5/3 = 4/3 => x = 5/3 + 4/3 = 9/3 = 3. Case 2: x - 5/3 = -4/3 => x = 5/3 - 4/3 = 1/3. So the solutions are x = 1/3, 3."),
            ("MTH101", "Form a quadratic equation whose roots are 2 + √3 and 2 - √3.", "x² - 4x + 1 = 0", "x² + 4x + 1 = 0", "x² - 4x - 1 = 0", "x² + 4x - 1 = 0", "A", "For a quadratic equation x² - (sum of roots)x + (product of roots) = 0. Sum of roots = (2 + √3) + (2 - √3) = 4. Product of roots = (2 + √3)(2 - √3) = 2² - (√3)² = 4 - 3 = 1. So the quadratic equation is x² - 4x + 1 = 0."),
            ("MTH101", "Solve the equation x⁴ - 5x² + 4 = 0.", "x = ±1, ±2", "x = ±1, ±4", "x = 1, 2", "x = -1, -2", "A", "Let y = x². The equation becomes y² - 5y + 4 = 0. Factor the quadratic: (y - 1)(y - 4) = 0. So, y = 1 or y = 4. Substitute back y = x²: Case 1: x² = 1 => x = ±1. Case 2: x² = 4 => x = ±2. So the solutions are x = ±1, ±2."),
            ("MTH101", "Let U = {1, 2, 3, ..., 10} be the universal set. If A = {1, 3, 5, 7, 9} and B = {2, 3, 5, 7}, find (A Δ B)′, where Δ denotes the symmetric difference.", "{1,2,3,4,5,6,7,8,9,10}", "{3, 4, 5, 6, 7, 8, 10}", "{1,9}", "{2,3,5,7}", "B", "Given U = {1, 2, 3, ..., 10}, A = {1, 3, 5, 7, 9}, B = {2, 3, 5, 7}. First, find the symmetric difference A Δ B = (A \ B) ∪ (B \ A). A \ B = {1, 9}. B \ A = {2}. So, A Δ B = {1, 2, 9}. Now, find the complement (A Δ B)′ with respect to U. (A Δ B)′ = U \ (A Δ B) = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10} \ {1, 2, 9} = {3, 4, 5, 6, 7, 8, 10}."),
            ("MTH101", "Given that A ⊂ B, simplify the expression (A ∩ B) ∪ (B \ A).", "A", "B", "A ∩ B", "A ∪ B", "B", "Given A ⊂ B, which means A is a subset of B. If A ⊂ B, then A ∩ B = A. Also, B \ A represents elements in B but not in A. The expression becomes A ∪ (B \ A). Since A and (B \ A) are disjoint (they have no common elements), their union is simply B. Alternatively, A ∪ (B \ A) = A ∪ (B ∩ A′). Using distributive law, this is (A ∪ B) ∩ (A ∪ A′) = (A ∪ B) ∩ U = A ∪ B. Since A ⊂ B, A ∪ B = B. So the simplified expression is B."),
        ]
        
        cursor.executemany('INSERT INTO questions (course_code, question_text, option_a, option_b, option_c, option_d, correct_option, solution) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', questions)
    
    conn.commit()
    conn.close()
    print("Database initialized/updated successfully.")

if __name__ == '__main__':
    init_db()
