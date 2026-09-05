import json
import os

import psycopg2
import psycopg2.extras

from .providers import generate_text


DEFAULT_DAILY_AI_LIMIT = 20


class DailyAILimitReached(RuntimeError):
    """Raised when no AI-generated questions remain in today's allowance."""


def get_db_connection():
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError('DATABASE_URL environment variable not set')
    return psycopg2.connect(database_url)


def get_daily_ai_limit():
    try:
        return max(1, int(os.getenv('PREMIUM_AI_DAILY_LIMIT', DEFAULT_DAILY_AI_LIMIT)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_AI_LIMIT


def _generate_ai_question_text(prompt):
    return generate_text(
        system_prompt=(
            'You are the PrepCampus premium CBT question generator. '
            'Return only valid JSON matching the requested format.'
        ),
        user_message=prompt,
        temperature=0.2,
        max_tokens=5000,
    )


def normalize_course(course):
    return ''.join(str(course or '').upper().split())


def _question_from_row(row, source='database'):
    return {
        'id': row['id'],
        'course': row['course_code'],
        'topic': row['topic'],
        'question': row['question_text'],
        'option_a': row['option_a'],
        'option_b': row['option_b'],
        'option_c': row['option_c'],
        'option_d': row['option_d'],
        'correct_option': row['correct_option'],
        'solution': row['solution'] or '',
        'source': source,
    }


def _get_unseen_database_questions(conn, user_id, course, topic, count, question_type):
    if question_type != 'mcq' or count <= 0:
        return []

    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        '''
        SELECT q.id, q.course_code, q.topic, q.question_text,
               q.option_a, q.option_b, q.option_c, q.option_d,
               q.correct_option, q.solution
        FROM questions q
        WHERE UPPER(q.course_code) = UPPER(%s)
          AND q.topic ILIKE %s
          AND NOT EXISTS (
              SELECT 1
              FROM premium_question_views v
              WHERE v.user_id = %s AND v.question_id = q.id
          )
        ORDER BY RANDOM()
        LIMIT %s
        ''',
        (course, f'%{topic}%', user_id, count),
    )
    rows = cursor.fetchall()
    cursor.close()
    return [_question_from_row(row, source='database') for row in rows]


def _reserve_ai_questions(conn, user_id, requested):
    if requested <= 0:
        return 0, 0, get_daily_ai_limit()

    limit = get_daily_ai_limit()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        '''
        INSERT INTO premium_ai_usage (user_id, usage_date, generated_questions)
        VALUES (%s, CURRENT_DATE, 0)
        ON CONFLICT (user_id, usage_date) DO NOTHING
        ''',
        (user_id,),
    )
    cursor.execute(
        '''
        SELECT generated_questions
        FROM premium_ai_usage
        WHERE user_id = %s AND usage_date = CURRENT_DATE
        FOR UPDATE
        ''',
        (user_id,),
    )
    row = cursor.fetchone()
    used = int(row['generated_questions'] if row else 0)
    reserved = min(requested, max(limit - used, 0))
    if reserved:
        cursor.execute(
            '''
            UPDATE premium_ai_usage
            SET generated_questions = generated_questions + %s
            WHERE user_id = %s AND usage_date = CURRENT_DATE
            ''',
            (reserved, user_id),
        )
    conn.commit()
    cursor.close()
    return reserved, used + reserved, limit


def _refund_ai_questions(user_id, amount):
    if amount <= 0:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''
        UPDATE premium_ai_usage
        SET generated_questions = GREATEST(generated_questions - %s, 0)
        WHERE user_id = %s AND usage_date = CURRENT_DATE
        ''',
        (amount, user_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_ai_usage(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        '''
        SELECT generated_questions
        FROM premium_ai_usage
        WHERE user_id = %s AND usage_date = CURRENT_DATE
        ''',
        (user_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    used = int(row['generated_questions'] if row else 0)
    limit = get_daily_ai_limit()
    return {'used': used, 'limit': limit, 'remaining': max(limit - used, 0)}


def _build_prompt(course, topic, difficulty, question_type, count, examples):
    return f'''
Generate exactly {count} new {question_type} CBT questions.

Course: {course}
Topic: {topic}
Difficulty: {difficulty}

The examples below are only for context. Do not copy their wording, answer choices,
or solutions. Create questions that are materially different from them.

Examples:
{examples}

Return ONLY valid JSON in this format:
[
  {{
    "question": "...",
    "option_a": "...",
    "option_b": "...",
    "option_c": "...",
    "option_d": "...",
    "correct_option": "A",
    "solution": "..."
  }}
]

For theory questions, return empty strings for option_a, option_b, option_c,
option_d, and correct_option.
'''


def _parse_ai_questions(text, course, topic):
    if not text:
        raise RuntimeError('The question generator returned an empty response')

    text = text.replace('```json', '').replace('```', '').strip()
    questions = json.loads(text)
    if not isinstance(questions, list):
        raise ValueError('The question generator returned an invalid response')

    cleaned = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        question_text = str(item.get('question') or item.get('question_text') or '').strip()
        if not question_text:
            continue
        options = item.get('options') if isinstance(item.get('options'), list) else []
        cleaned.append({
            'course': course,
            'topic': topic,
            'question': question_text,
            'option_a': str(item.get('option_a') or (options[0] if len(options) > 0 else '')),
            'option_b': str(item.get('option_b') or (options[1] if len(options) > 1 else '')),
            'option_c': str(item.get('option_c') or (options[2] if len(options) > 2 else '')),
            'option_d': str(item.get('option_d') or (options[3] if len(options) > 3 else '')),
            'correct_option': str(item.get('correct_option') or '').strip(),
            'solution': str(item.get('solution') or '').strip(),
            'source': 'ai',
        })
    return cleaned


def _save_ai_questions(conn, user_id, questions):
    if not questions:
        return []

    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    saved = []
    for question in questions:
        cursor.execute(
            '''
            INSERT INTO questions (
                course_code, topic, question_text, option_a, option_b,
                option_c, option_d, correct_option, solution, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (course_code, question_text) DO NOTHING
            RETURNING id, course_code, topic, question_text, option_a, option_b,
                      option_c, option_d, correct_option, solution
            ''',
            (
                question['course'], question['topic'], question['question'],
                question['option_a'], question['option_b'], question['option_c'],
                question['option_d'], question['correct_option'],
                question['solution'], user_id,
            ),
        )
        row = cursor.fetchone()
        if row:
            saved.append(_question_from_row(row, source='ai'))
    conn.commit()
    cursor.close()
    return saved


def _mark_questions_seen(conn, user_id, questions):
    ids = [question.get('id') for question in questions if question.get('id')]
    if not ids:
        return
    cursor = conn.cursor()
    for question_id in ids:
        cursor.execute(
            '''
            INSERT INTO premium_question_views (user_id, question_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, question_id) DO NOTHING
            ''',
            (user_id, question_id),
        )
    conn.commit()
    cursor.close()


def generate_questions(user_id, course, topic, difficulty, question_type, count):
    course = normalize_course(course)
    topic = str(topic or '').strip()
    count = int(count)

    conn = get_db_connection()
    reused = _get_unseen_database_questions(conn, user_id, course, topic, count, question_type)
    missing = max(count - len(reused), 0)
    reserved, used_after_reserve, daily_limit = _reserve_ai_questions(conn, user_id, missing)
    conn.close()

    if missing and reserved == 0:
        if reused:
            seen_conn = get_db_connection()
            _mark_questions_seen(seen_conn, user_id, reused)
            seen_conn.close()
            usage = get_ai_usage(user_id)
            return reused, {
                'reused_count': len(reused),
                'generated_count': 0,
                'daily_limit': daily_limit,
                'used_today': usage['used'],
                'remaining_today': usage['remaining'],
                'limit_reached': True,
            }
        raise DailyAILimitReached('Daily AI question limit reached. Your allowance resets tomorrow.')

    generated = []
    provider_used = None
    if reserved:
        try:
            example_conn = get_db_connection()
            example_cursor = example_conn.cursor()
            example_cursor.execute(
                '''
                SELECT question_text, option_a, option_b, option_c, option_d, correct_option
                FROM questions
                WHERE UPPER(course_code) = UPPER(%s) AND topic ILIKE %s
                ORDER BY RANDOM()
                LIMIT 5
                ''',
                (course, f'%{topic}%'),
            )
            examples = example_cursor.fetchall()
            example_cursor.close()
            example_conn.close()

            response_text, provider_used = _generate_ai_question_text(
                _build_prompt(course, topic, difficulty, question_type, reserved, examples)
            )
            generated = _parse_ai_questions(response_text, course, topic)[:reserved]

            save_conn = get_db_connection()
            generated = _save_ai_questions(save_conn, user_id, generated)
            _mark_questions_seen(save_conn, user_id, generated)
            save_conn.close()

            if len(generated) < reserved:
                _refund_ai_questions(user_id, reserved - len(generated))
        except Exception:
            _refund_ai_questions(user_id, reserved)
            raise
    

    final_questions = reused + generated
    mark_conn = get_db_connection()
    _mark_questions_seen(mark_conn, user_id, reused)
    mark_conn.close()

    usage = get_ai_usage(user_id)
    return final_questions, {
        'reused_count': len(reused),
        'generated_count': len(generated),
        'daily_limit': daily_limit,
        'used_today': usage['used'],
        'remaining_today': usage['remaining'],
        'limit_reached': usage['remaining'] == 0,
        'provider': provider_used,
    }