# Flask CBT Web Application

This is a simple Computer-Based Test (CBT) application built with Flask and SQLite.

## Project Structure
- `app.py`: The main Flask application containing backend logic and API endpoints.
- `init_db.py`: Database initialization script to set up the SQLite database and seed questions.
- `database/`: Directory containing the SQLite database file (`quiz.db`).
- `static/`: Directory for static assets like CSS and JavaScript.
- `templates/`: Directory for HTML templates (base, index, quiz, result, error).

## Data Flow
1. **Database → Backend**: 
   - When the quiz starts, the backend (`app.py`) queries the SQLite database using `sqlite3`.
   - It fetches questions and converts them into a list of dictionaries.
2. **Backend → Frontend**:
   - The frontend (`quiz.html`) makes an asynchronous fetch request to the `/api/questions` endpoint.
   - The backend sends the question data (excluding correct answers) as a JSON response.
3. **Frontend → Backend**:
   - When the user submits the quiz, the frontend sends the collected answers as a JSON object to the `/submit` endpoint via a POST request.
   - The backend receives this data, compares it with the correct answers in the database, and calculates the score.
   - The score is stored in the session, and the user is redirected to the result page.

## Features
- Clean and responsive UI with CSS gradients.
- Frontend JavaScript timer (30 minutes).
- Question-by-question display with progress bar.
- Secure backend score calculation.
- Beginner-friendly, well-commented code.
