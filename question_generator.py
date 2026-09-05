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


def _clean_content_text(value, limit=4000):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return ''
    return str(value).strip()[:limit]


def _clean_equations(value):
    if not isinstance(value, list):
        return []
    return [_clean_content_text(item, 1000) for item in value if _clean_content_text(item, 1000)][:8]


def _clean_steps(value):
    if not isinstance(value, list):
        return []
    return [_clean_content_text(item, 1500) for item in value if _clean_content_text(item, 1500)][:12]


def _normalize_question_content(item, legacy=None):
    legacy = legacy or {}
    item = item if isinstance(item, dict) else {}
    question = item.get('question') or item.get('question_text') or legacy.get('question') or legacy.get('question_text')
    question = _clean_content_text(question, 5000)

    raw_options = item.get('options')
    if isinstance(raw_options, dict):
        options = [raw_options.get(letter, '') for letter in ('A', 'B', 'C', 'D')]
    elif isinstance(raw_options, list):
        options = raw_options[:4]
    else:
        options = [item.get(f'option_{letter.lower()}') or legacy.get(f'option_{letter.lower()}') or '' for letter in ('A', 'B', 'C', 'D')]
    options = [_clean_content_text(option, 2000) for option in options]

    raw_solution = item.get('solution')
    if not raw_solution and legacy.get('solution'):
        raw_solution = legacy.get('solution')
    if isinstance(raw_solution, dict):
        solution = {
            'steps': _clean_steps(raw_solution.get('steps')),
            'equations': _clean_equations(raw_solution.get('equations')),
            'answer': _clean_content_text(raw_solution.get('answer'), 2000),
            'explanation': _clean_content_text(raw_solution.get('explanation'), 5000),
        }
    else:
        solution = {
            'steps': _clean_steps(item.get('steps') or legacy.get('solution_steps')),
            'equations': _clean_equations(item.get('equations') or legacy.get('equations')),
            'answer': _clean_content_text(item.get('answer') or legacy.get('answer'), 2000),
            'explanation': _clean_content_text(raw_solution, 5000),
        }
    if not solution['explanation'] and solution['answer']:
        solution['explanation'] = solution['answer']

    return {
        'question': question,
        'options': options,
        'solution': solution,
    }


def _question_from_row(row, source='database'):
    legacy = {
        'question_text': row['question_text'],
        'option_a': row['option_a'],
        'option_b': row['option_b'],
        'option_c': row['option_c'],
        'option_d': row['option_d'],
        'solution': row['solution'] or '',
    }
    content = row.get('content_json') if hasattr(row, 'get') else None
    content = _normalize_question_content(content, legacy)
    options = content['options']
    return {
        'id': row['id'],
        'course': row['course_code'],
        'topic': row['topic'],
        'question': content['question'],
        'question_text': content['question'],
        'options': options,
        'option_a': options[0],
        'option_b': options[1],
        'option_c': options[2],
        'option_d': options[3],
        'correct_option': row['correct_option'],
        'solution': content['solution']['explanation'],
        'solution_content': content['solution'],
        'content': content,
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
               q.correct_option, q.solution, q.content_json

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

Return ONLY a valid JSON array. Do not return Markdown, code fences, HTML, or commentary.
Every object must follow this structure:
[
  {{
    "question": "Plain-text question stem. Put mathematical expressions in the equations arrays only.",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_option": "A",
    "solution": {{
      "steps": ["Step 1 written as plain text", "Step 2 written as plain text"],
      "equations": ["\\frac{{a}}{{b}}", "x^{{2}}"],
      "answer": "The correct answer in plain text.",
      "explanation": "A concise explanation suitable for a first-year university student."
    }}
  }}
]

Rules:
- Return exactly {count} objects.
- Use exactly four options for multiple-choice questions.
- For theory questions, return an empty options array and an empty correct_option.
- Do not use Markdown syntax, dollar-sign math delimiters, HTML tags, or code fences.
- Use LaTeX only inside the solution.equations array, without dollar signs.
- Keep solution.steps numbered by order in the array; do not include numeric prefixes.
- Make every answer and explanation clear, accurate, and directly tied to the question.
'''


def _parse_ai_questions(text, course, topic):
    if not text:
        raise RuntimeError('The question generator returned an empty response')

    cleaned_text = str(text).strip().replace('```json', '').replace('```', '').strip()
    try:
        questions = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        raise ValueError('The question generator returned malformed JSON') from exc
    if not isinstance(questions, list):
        raise ValueError('The question generator returned an invalid response')

    cleaned = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        content = _normalize_question_content(item)
        if not content['question']:
            continue
        options = content['options']
        cleaned.append({
            'course': course,
            'topic': topic,
            'question': content['question'],
            'option_a': options[0],
            'option_b': options[1],
            'option_c': options[2],
            'option_d': options[3],
            'options': options,
            'correct_option': str(item.get('correct_option') or '').strip().upper()[:1],
            'solution': content['solution']['explanation'],
            'solution_content': content['solution'],
            'content': content,
            'source': 'ai',
        })
    if not cleaned:
        raise ValueError('The question generator returned no usable questions')
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
                                option_c, option_d, correct_option, solution, content_json, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)

            ON CONFLICT (course_code, question_text) DO NOTHING
            RETURNING id, course_code, topic, question_text, option_a, option_b,
                                            option_c, option_d, correct_option, solution, content_json
            ''',

            (
                question['course'], question['topic'], question['question'],
                question['option_a'], question['option_b'], question['option_c'],
                                question['option_d'], question['correct_option'],
                question['solution'], psycopg2.extras.Json(question.get('content', {})), user_id,

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