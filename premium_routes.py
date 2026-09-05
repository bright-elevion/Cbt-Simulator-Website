"""
Premium Features Routes
All premium-related routes are defined here to keep the main application clean
"""
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
import psycopg2
import psycopg2.extras
from ai.providers import AIProviderError, generate_text

import os
import uuid
import json
import math
import re
from collections import defaultdict, Counter
from datetime import datetime, timedelta, date
from decimal import Decimal

from dotenv import load_dotenv
from functools import wraps
from premium_utils import is_premium_user, premium_required, get_premium_stats, get_user_premium_info
from ai.question_generator import generate_questions, get_ai_usage, DailyAILimitReached, _question_from_row
from ai.coach import get_coach_response

from ai.recommendations import RecommendationEngine
from premium_utils import get_retention_percent

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_ENV_PATH = os.path.join(PROJECT_DIR, 'key.env')

if os.path.exists(KEY_ENV_PATH):
    load_dotenv(KEY_ENV_PATH, override=True)
else:
    load_dotenv(override=True)

DATABASE_URL = os.getenv('DATABASE_URL')
def _friendly_ai_error(exc, feature):
    """Return a safe, actionable message without exposing provider diagnostics."""
    attempts = getattr(exc, 'attempts', []) or []
    details = ' '.join(
        str(attempt.get('error', ''))
        for attempt in attempts
        if isinstance(attempt, dict)
    ).lower()

    connection_markers = (
        'connection',
        'connecterror',
        'timed out',
        'timeout',
        'network',
        'dns',
        'unreachable',
        'temporarily unavailable',
    )
    quota_markers = (
        'insufficient_quota',
        'credit_balance_exhausted',
        'rate limit',
        'too many requests',
        'quota',
        '429',
    )

    if any(marker in details for marker in connection_markers):
        return (
            f"We couldn't connect to your {feature} right now. "
            "Please check your internet connection and try again."
        )

    if any(marker in details for marker in quota_markers):
        return (
            f"Your {feature} is temporarily unavailable because the AI service "
            "is at capacity. Please try again in a little while."
        )

    return (
        f"We couldn't prepare your {feature} right now. "
        "Please try again in a moment."
    )

CAMPUSMATE_TUTOR_SYSTEM_PROMPT = """You are CampusMate, an expert university CBT tutor for a web-based CBT platform.

Return ONLY one valid JSON object. Never return Markdown, code fences, HTML, commentary outside the JSON object, or dollar-sign math delimiters.

Use exactly these fields:
{
  "question": "A short learner-facing lesson title",
  "steps": ["Numbered-step text without numbering prefixes"],
  "equations": ["LaTeX equation text only, without $, \\( \\), or <math> tags"],
  "answer": "The direct answer or key takeaway in plain text",
  "explanation": "A clear first-year university explanation in plain text"
}

Every solution must use short, ordered teaching steps. Put mathematical expressions only in the equations array using LaTeX commands such as \\frac{a}{b}, \\sqrt{x}, x^{2}, and x_{1}. Do not put raw HTML or Markdown in any field. Keep the tone warm, accurate, and practical. End the explanation with one actionable check for understanding when appropriate."""

CAMPUSMATE_TUTOR_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'question': {'type': 'string'},
        'steps': {'type': 'array', 'items': {'type': 'string'}},
        'equations': {'type': 'array', 'items': {'type': 'string'}},
        'answer': {'type': 'string'},
        'explanation': {'type': 'string'},
    },
    'required': ['question', 'steps', 'equations', 'answer', 'explanation'],
}


def _strip_json_wrappers(text):
    """Recover a JSON object from an otherwise clean model response."""
    value = str(text or '').strip()
    if '```json' in value:
        value = value.split('```json', 1)[1].split('```', 1)[0].strip()
    elif '```' in value:
        value = value.split('```', 1)[1].split('```', 1)[0].strip()
    start = value.find('{')
    end = value.rfind('}')
    if start >= 0 and end > start:
        value = value[start:end + 1]
    return value


def _plain_tutor_text(value, limit=3000):
    """Normalize one model field to safe plain text for textContent rendering."""
    if isinstance(value, (dict, list)):
        value = ' '.join(str(item) for item in value)
    text = str(value or '').replace('\\r', ' ').replace('\\n', ' ').strip()
    text = text.replace('```', '').replace('**', '').replace('__', '')
    return text[:limit]


def _tutor_equation(value):
    """Normalize an equation to bare LaTeX before the browser typesets it."""
    equation = _plain_tutor_text(value, 500)
    equation = equation.replace('$', '').replace('<math>', '').replace('</math>', '').strip()
    if equation.startswith('\\(') and equation.endswith('\\)'):
        equation = equation[2:-2].strip()
    if equation.startswith('\\[') and equation.endswith('\\]'):
        equation = equation[2:-2].strip()
    return equation


def _normalize_campusmate_tutor_response(payload, fallback_title='CampusMate lesson'):
    """Validate and normalize the JSON contract before it reaches the UI or database."""
    if not isinstance(payload, dict):
        raise ValueError('CampusMate returned an invalid lesson object.')
    steps = payload.get('steps') if isinstance(payload.get('steps'), list) else []
    equations = payload.get('equations') if isinstance(payload.get('equations'), list) else []
    normalized = {
        'question': _plain_tutor_text(payload.get('question') or fallback_title, 240),
        'steps': [_plain_tutor_text(item, 1200) for item in steps[:12] if _plain_tutor_text(item, 1200)],
        'equations': [_tutor_equation(item) for item in equations[:12] if _tutor_equation(item)],
        'answer': _plain_tutor_text(payload.get('answer'), 2000),
        'explanation': _plain_tutor_text(payload.get('explanation'), 4000),
    }
    if not normalized['answer'] and not normalized['explanation'] and not normalized['steps']:
        raise ValueError('CampusMate returned an empty lesson.')
    return normalized


def _generate_campusmate_tutor_response(prompt, fallback_title='CampusMate lesson'):
    """Generate and validate the structured tutor object through the configured provider chain."""
    raw_response, _provider = generate_text(
        system_prompt=CAMPUSMATE_TUTOR_SYSTEM_PROMPT,
        user_message=prompt + '\\n\\nReturn JSON matching this contract:\\n' + json.dumps(CAMPUSMATE_TUTOR_SCHEMA),
        temperature=0.35,
        max_tokens=1800,
    )
    try:
        payload = json.loads(_strip_json_wrappers(raw_response))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError('CampusMate returned an invalid structured lesson.') from exc
    return _normalize_campusmate_tutor_response(payload, fallback_title=fallback_title)


def _normalize_stored_tutor_content(content):
    """Make legacy plain-text history renderable beside new structured responses."""
    if isinstance(content, dict):
        try:
            return _normalize_campusmate_tutor_response(content)
        except ValueError:
            pass
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
        if isinstance(parsed, dict):
            return _normalize_campusmate_tutor_response(parsed)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {
        'question': 'CampusMate lesson',
        'steps': [],
        'equations': [],
        'answer': '',
        'explanation': _plain_tutor_text(content, 4000),
    }


def get_db_connection():
    """Create a database connection and return it."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable not set")
    return psycopg2.connect(DATABASE_URL)


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _row_value(row, key, default=None):
    if row is None:
        return default
    try:
        value = row.get(key, default)
    except AttributeError:
        value = row[key] if key in row else default
    return default if value is None else value


def _normalise_datetime(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=None)


def _clamp_score(value):
    return round(max(0.0, min(100.0, _as_float(value))), 1)


def _build_prediction(score_rows, attempts):
    history = []
    for row in score_rows:
        total = _as_float(_row_value(row, 'total'))
        score = _as_float(_row_value(row, 'score'))
        if total > 0:
            history.append({
                'score': _clamp_score(score / total * 100),
                'date': _normalise_datetime(_row_value(row, 'created_at')),
            })

    if not history:
        grouped = defaultdict(lambda: {'correct': 0, 'total': 0, 'date': None})
        for row in attempts:
            key = _row_value(row, 'attempt_id') or _row_value(row, 'answered_at')
            grouped[key]['total'] += 1
            grouped[key]['correct'] += 1 if _row_value(row, 'was_correct') else 0
            answered_at = _normalise_datetime(_row_value(row, 'answered_at'))
            if answered_at and (not grouped[key]['date'] or answered_at > grouped[key]['date']):
                grouped[key]['date'] = answered_at
        history = [
            {'score': _clamp_score(item['correct'] / item['total'] * 100), 'date': item['date']}
            for item in grouped.values() if item['total']
        ]

    if not history:
        return {'score': None, 'probabilities': [], 'sample_size': 0, 'message': 'Complete a few timed sets to unlock a reliable prediction.'}

    now = datetime.utcnow()
    weights = []
    values = []
    for item in history:
        age = max(0, (now - item['date']).days) if item['date'] else 0
        weight = 1 / (1 + age / 30)
        values.append(item['score'])
        weights.append(weight)
    predicted = sum(value * weight for value, weight in zip(values, weights)) / sum(weights)
    spread = max(6.0, (sum((value - predicted) ** 2 for value in values) / len(values)) ** 0.5)

    def cdf(boundary):
        return 0.5 * (1 + math.erf((boundary - predicted) / (spread * math.sqrt(2))))

    buckets = [
        ('Below 50', 0, 50),
        ('50–59', 50, 60),
        ('60–69', 60, 70),
        ('70–79', 70, 80),
        ('80–89', 80, 90),
        ('90+', 90, 101),
    ]
    probabilities = []
    for label, lower, upper in buckets:
        probability = max(0, cdf(upper) - cdf(lower)) if upper < 101 else max(0, 1 - cdf(lower))
        probabilities.append({'label': label, 'probability': round(probability * 100)})
    difference = 100 - sum(item['probability'] for item in probabilities)
    if probabilities:
        probabilities[max(range(len(probabilities)), key=lambda index: probabilities[index]['probability'])]['probability'] += difference

    return {
        'score': round(predicted),
        'probabilities': probabilities,
        'sample_size': len(history),
        'message': 'This estimate becomes more reliable as you complete more scored assessments.',
    }


def _build_streaks(attempts):
    dates = set()
    for row in attempts:
        answered_at = _normalise_datetime(_row_value(row, 'answered_at'))
        if answered_at:
            dates.add(answered_at.date())
    if not dates:
        return {'current': 0, 'longest': 0, 'active_days': 0}

    sorted_dates = sorted(dates)
    longest = current_run = 1
    for index in range(1, len(sorted_dates)):
        if sorted_dates[index] == sorted_dates[index - 1] + timedelta(days=1):
            current_run += 1
            longest = max(longest, current_run)
        else:
            current_run = 1
    cursor = datetime.utcnow().date()
    if cursor not in dates:
        cursor -= timedelta(days=1)
    current = 0
    while cursor in dates:
        current += 1
        cursor -= timedelta(days=1)
    span_days = max(1, (sorted_dates[-1] - sorted_dates[0]).days + 1)
    consistency = _clamp_score(min(100, len(dates) / max(7, span_days) * 100))
    return {'current': current, 'longest': longest, 'active_days': len(dates), 'consistency': consistency}


def _build_performance_insights(attempts, score_rows):
    now = datetime.utcnow()
    topic_stats = defaultdict(lambda: {'course': 'Other', 'total': 0, 'correct': 0, 'last': None, 'times': []})
    course_stats = defaultdict(lambda: {'total': 0, 'correct': 0, 'topics': set(), 'last': None, 'scores': []})
    confidence_weights = {'very_confident': 100, 'somewhat_confident': 70, 'guessing': 40}
    confidence_values = []
    timings = []
    total_correct = 0
    for row in attempts:
        course = _row_value(row, 'course_code') or 'Other'
        topic = _row_value(row, 'topic') or 'General practice'
        correct = bool(_row_value(row, 'was_correct'))
        answered_at = _normalise_datetime(_row_value(row, 'answered_at'))
        seconds = _as_float(_row_value(row, 'time_spent_seconds'), -1)
        topic_key = f'{course}::{topic}'
        topic_stats[topic_key]['course'] = course
        topic_stats[topic_key]['total'] += 1
        topic_stats[topic_key]['correct'] += int(correct)
        topic_stats[topic_key]['last'] = max(topic_stats[topic_key]['last'], answered_at) if answered_at and topic_stats[topic_key]['last'] else answered_at or topic_stats[topic_key]['last']
        if seconds >= 0:
            topic_stats[topic_key]['times'].append(seconds)
            timings.append(seconds)
        course_stats[course]['total'] += 1
        course_stats[course]['correct'] += int(correct)
        course_stats[course]['topics'].add(topic)
        course_stats[course]['last'] = max(course_stats[course]['last'], answered_at) if answered_at and course_stats[course]['last'] else answered_at or course_stats[course]['last']
        if _row_value(row, 'confidence') in confidence_weights:
            confidence_values.append(confidence_weights[_row_value(row, 'confidence')])
        total_correct += int(correct)

    topic_rows = []
    for key, values in topic_stats.items():
        course, topic = key.split('::', 1)
        accuracy = values['correct'] / values['total'] * 100 if values['total'] else 0
        days_since = max(0, (now - values['last']).days) if values['last'] else None
        retention = get_retention_percent(days_since) if days_since is not None else 0
        if accuracy >= 80:
            status = 'mastered'
            label = 'Mastered'
        elif accuracy >= 60:
            status = 'practice'
            label = 'Needs practice'
        else:
            status = 'critical'
            label = 'Critical weakness'
        topic_rows.append({
            'course': course, 'topic': topic, 'accuracy': round(accuracy, 1),
            'attempts': values['total'], 'status': status, 'label': label,
            'last_practiced': values['last'], 'days_since': days_since,
            'retention': round(retention), 'review_recommended': retention < 50 if days_since is not None else False,
        })
    topic_rows.sort(key=lambda row: (row['course'], row['accuracy']))

    mastery_map = []
    for course in sorted({row['course'] for row in topic_rows}):
        mastery_map.append({'course': course, 'topics': [row for row in topic_rows if row['course'] == course]})

    readiness = []
    course_topic_rows = defaultdict(list)
    for row in topic_rows:
        course_topic_rows[row['course']].append(row)
    for course, values in course_stats.items():
        accuracy = values['correct'] / values['total'] * 100 if values['total'] else 0
        topics = course_topic_rows[course]
        retention = sum(row['retention'] for row in topics) / len(topics) if topics else 0
        coverage = min(100, len(values['topics']) * 25)
        readiness_score = _clamp_score(accuracy * 0.55 + retention * 0.2 + coverage * 0.15 + accuracy * 0.1)
        readiness.append({'course': course, 'score': round(readiness_score), 'accuracy': round(accuracy), 'attempts': values['total']})
    readiness.sort(key=lambda row: row['score'], reverse=True)

    knowledge = sum(row['accuracy'] for row in topic_rows) / len(topic_rows) if topic_rows else 0
    retention_score = sum(row['retention'] for row in topic_rows) / len(topic_rows) if topic_rows else 0
    speed_score = 100 if not timings else _clamp_score(100 - max(0, (sum(timings) / len(timings) - 20) * 1.25))
    streaks = _build_streaks(attempts)
    consistency = streaks.get('consistency', 0)
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else knowledge
    health_components = [
        {'name': 'Knowledge', 'value': round(knowledge), 'tone': 'blue'},
        {'name': 'Retention', 'value': round(retention_score), 'tone': 'purple'},
        {'name': 'Speed', 'value': round(speed_score), 'tone': 'green'},
        {'name': 'Consistency', 'value': round(consistency), 'tone': 'amber'},
        {'name': 'Confidence', 'value': round(confidence), 'tone': 'pink'},
    ]
    academic_health = round(sum(item['value'] for item in health_components) / len(health_components)) if health_components else 0

    total_time = sum(timings)
    time_analysis = {
        'average': round(sum(timings) / len(timings)) if timings else None,
        'fastest': round(min(timings)) if timings else None,
        'slowest': round(max(timings)) if timings else None,
        'profile': 'Balanced learner',
    }
    if timings:
        average_time = time_analysis['average']
        if average_time <= 20 and knowledge < 70:
            time_analysis['profile'] = 'Rushing'
        elif average_time <= 20:
            time_analysis['profile'] = 'Fast learner'
        elif average_time >= 45 and knowledge >= 75:
            time_analysis['profile'] = 'Careful learner'
        elif average_time >= 45:
            time_analysis['profile'] = 'Overthinking'

    retention_topics = sorted(topic_rows, key=lambda row: (not row['review_recommended'], row['retention']))[:8]
    efficiency = {
        'questions': len(attempts), 'correct': total_correct,
        'study_hours': round(total_time / 3600, 1) if total_time else None,
        'correct_per_hour': round(total_correct / (total_time / 3600), 1) if total_time else None,
    }
    return {
        'academic_health': academic_health,
        'health_components': health_components,
        'mastery_map': mastery_map,
        'topics': topic_rows,
        'predicted_exam': _build_prediction(score_rows, attempts),
        'retention_topics': retention_topics,
        'retention_curve': [
            {'day': day, 'value': get_retention_percent(day)}
            for day in (1, 7, 14, 30)
        ],
        'readiness': readiness,
        'efficiency': efficiency,
        'time_analysis': time_analysis,
        'streaks': streaks,
        'total_attempts': len(attempts),
        'has_data': bool(attempts or score_rows),
    }


def _build_distractor_analysis(attempts):
    questions = {}
    for row in attempts:
        question_id = _row_value(row, 'question_id')
        if not question_id:
            continue
        item = questions.setdefault(question_id, {
            'question_id': question_id, 'question_text': _row_value(row, 'question_text') or 'Question',
            'correct_option': _row_value(row, 'correct_option') or '',
            'options': {'A': _row_value(row, 'option_a'), 'B': _row_value(row, 'option_b'), 'C': _row_value(row, 'option_c'), 'D': _row_value(row, 'option_d')},
            'counts': Counter(), 'total': 0,
        })
        selected = str(_row_value(row, 'selected_option') or '').upper()
        if selected in item['options']:
            item['counts'][selected] += 1
            item['total'] += 1
    results = []
    for item in questions.values():
        if not item['total']:
            continue
        selections = [
            {'option': option, 'text': item['options'][option], 'percentage': round(item['counts'][option] / item['total'] * 100), 'is_correct': option == item['correct_option']}
            for option in ('A', 'B', 'C', 'D')
        ]
        wrong = max((entry for entry in selections if not entry['is_correct']), key=lambda entry: entry['percentage'], default=None)
        if wrong and wrong['percentage'] >= 40:
            item['insight'] = f"You often choose {wrong['option']}. Review the distinction between {item['options'].get(wrong['option']) or wrong['option']} and the correct concept."
            item['severity'] = 'high'
        else:
            item['insight'] = 'No repeated distractor pattern detected yet.'
            item['severity'] = 'neutral'
        item['selections'] = selections
        results.append(item)
    results.sort(key=lambda item: max((entry['percentage'] for entry in item['selections'] if not entry['is_correct']), default=0), reverse=True)
    return results[:8]


def _build_plan_from_attempts(attempts):
    """Create a stable, explainable seven-day plan from reviewed attempts."""
    topic_counts = defaultdict(lambda: {'total': 0, 'correct': 0, 'misses': 0})
    miss_counts = Counter()
    for row in attempts:
        topic = (row.get('topic') if hasattr(row, 'get') else row['topic']) or 'Core concepts'
        topic_counts[topic]['total'] += 1
        if row['was_correct']:
            topic_counts[topic]['correct'] += 1
        else:
            topic_counts[topic]['misses'] += 1
            if row.get('miss_reason'):
                miss_counts[row['miss_reason']] += 1

    accuracies = {
        topic: (values['correct'] / values['total']) * 100
        for topic, values in topic_counts.items()
        if values['total']
    }
    weak_topics = [topic for topic, accuracy in sorted(accuracies.items(), key=lambda item: item[1]) if accuracy < 80]
    if not weak_topics:
        weak_topics = list(sorted(accuracies, key=accuracies.get)) or ['Core concepts']

    recommendations = RecommendationEngine.prioritize_topics(weak_topics, accuracies)
    recommended_topics = [item.topic for item in recommendations] or weak_topics
    generated_goals = RecommendationEngine.generate_daily_goals(
        recommendations,
        available_hours_per_day=1,
        days_until_exam=7,
    )
    generated_goal_by_day = {goal.date: goal for goal in generated_goals}
    first = recommended_topics[0]
    second = recommended_topics[1] if len(recommended_topics) > 1 else first
    plan = [
        {'day': 'Day 1', 'title': 'Rebuild the foundation', 'focus': first, 'task': f'Review the core definitions and worked examples in {first}.', 'questions': 10, 'minutes': 35},
        {'day': 'Day 2', 'title': 'Strengthen the next gap', 'focus': second, 'task': f'Use active recall to explain {second}, then complete targeted practice.', 'questions': 12, 'minutes': 40},
        {'day': 'Day 3', 'title': 'Apply the concepts', 'focus': first, 'task': 'Solve scenario-based questions and write down why each missed answer was wrong.', 'questions': 15, 'minutes': 45},
        {'day': 'Day 4', 'title': 'Mixed practice', 'focus': 'Mixed review', 'task': 'Combine your two priority topics in a mixed practice set; aim for at least 75%.', 'questions': 20, 'minutes': 50},
        {'day': 'Day 5', 'title': 'Review and retrieve', 'focus': 'Mistake review', 'task': 'Revisit missed questions, flashcards, and the concepts connected to each miss reason.', 'questions': 12, 'minutes': 35},
        {'day': 'Day 6', 'title': 'Build exam speed', 'focus': 'Timed test', 'task': 'Complete a timed mini-test without looking at notes, then analyse time-related misses.', 'questions': 25, 'minutes': 60},
        {'day': 'Day 7', 'title': 'Final assessment', 'focus': 'Readiness check', 'task': 'Take a final mixed assessment and schedule another review for any topic below 80%.', 'questions': 30, 'minutes': 70},
    ]
    for day in plan:
        goal = generated_goal_by_day.get(day['day'])
        if goal:
            day['questions'] = max(day['questions'], goal.questions_to_complete)
            day['task'] = f"{day['task']} Success target: {goal.success_criteria}."

    return {
        'days': plan,
        'priority_topics': recommended_topics[:4],
        'topic_accuracies': accuracies,
        'miss_reasons': dict(miss_counts),
    }


def _build_learning_profile(attempts):
    course_stats = defaultdict(lambda: {'total': 0, 'correct': 0})
    topic_stats = defaultdict(lambda: {'total': 0, 'correct': 0})
    confidence_counts = Counter()
    miss_counts = Counter()
    for row in attempts:
        course = row['course_code'] or 'Unknown course'
        topic = row['topic'] or 'General practice'
        course_stats[course]['total'] += 1
        topic_stats[topic]['total'] += 1
        if row['was_correct']:
            course_stats[course]['correct'] += 1
            topic_stats[topic]['correct'] += 1
        if row['confidence']:
            confidence_counts[row['confidence']] += 1
        if row['miss_reason']:
            miss_counts[row['miss_reason']] += 1

    def ranked(stats, reverse=False):
        return [
            {'name': name, 'accuracy': round(values['correct'] / values['total'] * 100, 1), 'attempts': values['total']}
            for name, values in sorted(
                stats.items(),
                key=lambda item: item[1]['correct'] / item[1]['total'] if item[1]['total'] else 0,
                reverse=reverse,
            )
        ]

    total = len(attempts)
    guessing_rate = (confidence_counts['guessing'] / total * 100) if total else 0
    wrong_total = sum(miss_counts.values())
    forgot_rate = miss_counts['forgot'] / wrong_total * 100 if wrong_total else 0
    application_rate = miss_counts['misunderstood_concept'] / wrong_total * 100 if wrong_total else 0
    time_rate = miss_counts['ran_out_of_time'] / wrong_total * 100 if wrong_total else 0

    signals = []
    if forgot_rate <= 25:
        signals.append({'kind': 'positive', 'text': 'Excellent at memorization'})
    if application_rate >= 25:
        signals.append({'kind': 'warning', 'text': 'Struggles with application questions'})
    if time_rate >= 20:
        signals.append({'kind': 'warning', 'text': 'Accuracy drops during long exams'})
    if guessing_rate >= 25:
        signals.append({'kind': 'warning', 'text': 'Confidence is low on a meaningful share of answers'})
    if not signals:
        signals.append({'kind': 'positive', 'text': 'Balanced learning pattern with room to keep building consistency'})

    strengths = [item for item in ranked(topic_stats, reverse=True) if item['accuracy'] >= 80][:4]
    weaknesses = [item for item in ranked(topic_stats) if item['accuracy'] < 70][:4]
    if application_rate >= 25:
        recommendation = 'Focus on solving more scenario-based questions and explain the reasoning behind every answer.'
    elif time_rate >= 20:
        recommendation = 'Add short timed sets to your routine, then review both accuracy and time spent per question.'
    elif guessing_rate >= 25:
        recommendation = 'Use confidence checks and active recall before attempting more difficult mixed questions.'
    else:
        recommendation = 'Keep a spaced-review routine and increase the difficulty gradually on your weaker topics.'

    return {
        'strengths': strengths,
        'weaknesses': weaknesses,
        'learning_signals': signals,
        'recommendation': recommendation,
        'course_stats': ranked(course_stats, reverse=True),
        'confidence_counts': dict(confidence_counts),
        'miss_reasons': dict(miss_counts),
        'guessing_rate': round(guessing_rate, 1),
    }


def _safe_json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _parse_campusmate_date(raw_value):
    if isinstance(raw_value, date):
        return raw_value
    try:
        return datetime.strptime(str(raw_value).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _campusmate_context(profile):
    profile = profile or {}
    return {
        'university': str(_row_value(profile, 'university', '') or '').strip(),
        'department': str(_row_value(profile, 'department', '') or '').strip(),
        'level': str(_row_value(profile, 'level', '') or '').strip(),
        'semester': str(_row_value(profile, 'semester', '') or '').strip(),
        'exam_date': _row_value(profile, 'exam_date'),
        'daily_minutes': int(_as_float(_row_value(profile, 'daily_minutes'), 60) or 60),
        'courses': _safe_json(_row_value(profile, 'courses'), []),
    }


def _build_campusmate_priorities(attempts):
    grouped = defaultdict(list)
    for row in attempts:
        key = (
            str(_row_value(row, 'course_code') or 'General'),
            str(_row_value(row, 'topic') or 'Core concepts'),
        )
        grouped[key].append(row)

    priorities = []
    now = datetime.utcnow()
    for (course, topic), rows in grouped.items():
        ordered = sorted(rows, key=lambda row: _normalise_datetime(_row_value(row, 'answered_at')) or datetime.min, reverse=True)
        total = len(ordered)
        correct = sum(bool(_row_value(row, 'was_correct')) for row in ordered)
        accuracy = correct / total * 100 if total else 0
        recent = ordered[:10]
        recent_misses = sum(not bool(_row_value(row, 'was_correct')) for row in recent)
        repeated_misses = sum(not bool(_row_value(row, 'was_correct')) for row in ordered)
        last = _normalise_datetime(_row_value(ordered[0], 'answered_at')) if ordered else None
        days_since = max(0, (now - last).days) if last else 0
        retention = get_retention_percent(days_since) if last else 100

        accuracy_gap = max(0, 100 - accuracy)
        recent_miss_rate = recent_misses / max(1, len(recent)) * 100
        retention_risk = max(0, 50 - retention) * 2
        repetition_risk = min(100, repeated_misses * 8)
        priority = _clamp_score(
            accuracy_gap * 0.42
            + recent_miss_rate * 0.35
            + retention_risk * 0.13
            + repetition_risk * 0.10
        )
        estimated_minutes = int(max(20, min(60, round(20 + recent_misses * 3 + repeated_misses * 1.5))))
        expected_improvement = None
        evidence = 'Early signal — complete at least five questions for a stronger estimate.'
        if total >= 5:
            expected_improvement = round(min(20, max(2, accuracy_gap * 0.18 + recent_misses * 0.8)), 1)
            evidence = f'Based on {total} recorded questions, including {recent_misses} misses in the latest {len(recent)}.'

        reason = f'You answered {recent_misses} of the last {len(recent)} questions incorrectly.'
        if retention < 50:
            reason += f' Estimated retention is now {retention}% after {days_since} days without practice.'
        elif repeated_misses >= 3:
            reason += f' The topic has appeared in {repeated_misses} missed questions.'

        priorities.append({
            'course': course,
            'topic': topic,
            'priority': round(priority),
            'accuracy': round(accuracy, 1),
            'attempts': total,
            'recent_misses': recent_misses,
            'estimated_minutes': estimated_minutes,
            'expected_improvement': expected_improvement,
            'reason': reason,
            'evidence': evidence,
            'retention': retention,
            'days_since': days_since,
            'last_practiced': last,
        })

    priorities.sort(key=lambda item: (-item['priority'], item['accuracy'], -item['attempts']))
    return priorities[:8]


def _build_failure_diagnosis(attempts):
    misses = [row for row in attempts if not bool(_row_value(row, 'was_correct'))]
    buckets = {'conceptual_errors': 0, 'memory_errors': 0, 'careless_mistakes': 0, 'time_pressure': 0}
    for row in misses:
        reason = str(_row_value(row, 'miss_reason') or '').lower()
        seconds = _as_float(_row_value(row, 'time_spent_seconds'), 0)
        confidence = str(_row_value(row, 'confidence') or '').lower()
        if reason == 'misunderstood_concept':
            buckets['conceptual_errors'] += 1
        elif reason == 'forgot':
            buckets['memory_errors'] += 1
        elif reason == 'ran_out_of_time' or seconds >= 90:
            buckets['time_pressure'] += 1
        else:
            buckets['careless_mistakes'] += 1 if reason == 'guessed' or confidence == 'guessing' else 0
            if reason not in {'guessed', 'forgot', 'misunderstood_concept', 'ran_out_of_time'} and confidence != 'guessing':
                buckets['conceptual_errors'] += 1

    total = sum(buckets.values())
    if total == 0:
        return {
            'items': [],
            'total_misses': 0,
            'evidence_label': 'Complete and review a few missed questions to unlock a diagnosis.',
        }

    labels = {
        'conceptual_errors': ('Conceptual errors', 'Review the underlying idea, then solve an application question.'),
        'memory_errors': ('Memory errors', 'Use spaced recall and a short self-test instead of rereading only.'),
        'careless_mistakes': ('Uncertainty / careless mistakes', 'Slow down for the final check and state why your chosen option is correct.'),
        'time_pressure': ('Time pressure', 'Practise short timed sets and review questions that consume too much time.'),
    }
    items = []
    for key, count in buckets.items():
        percentage = round(count / total * 100)
        label, action = labels[key]
        items.append({'key': key, 'label': label, 'percentage': percentage, 'count': count, 'action': action})
    items.sort(key=lambda item: item['percentage'], reverse=True)
    return {
        'items': items,
        'total_misses': total,
        'evidence_label': f'Based on {total} recorded misses. Add miss reasons during review to improve diagnostic precision.',
    }


def _build_mistake_replay(attempts, limit=20):
    grouped = {}
    now = datetime.utcnow()
    for row in attempts:
        if bool(_row_value(row, 'was_correct')):
            continue
        question_id = _row_value(row, 'question_id')
        if question_id is None:
            continue
        item = grouped.setdefault(question_id, {
            'question_id': question_id,
            'question_text': _row_value(row, 'question_text') or 'Previously missed question',
            'course': _row_value(row, 'course_code') or 'General',
            'topic': _row_value(row, 'topic') or 'Core concepts',
            'correct_option': _row_value(row, 'correct_option') or '',
            'selected_option': _row_value(row, 'selected_option') or '',
            'solution': _row_value(row, 'solution') or '',
            'misses': 0,
            'last_missed': None,
        })
        item['misses'] += 1
        answered_at = _normalise_datetime(_row_value(row, 'answered_at'))
        if answered_at and (not item['last_missed'] or answered_at > item['last_missed']):
            item['last_missed'] = answered_at

    replay = []
    for item in grouped.values():
        days_since = max(0, (now - item['last_missed']).days) if item['last_missed'] else 0
        retention = get_retention_percent(days_since)
        item['retention'] = retention
        item['importance'] = round(min(100, 45 + item['misses'] * 12 + max(0, 50 - retention) * 0.5))
        item['days_since'] = days_since
        replay.append(item)
    replay.sort(key=lambda item: (-item['importance'], -item['misses'], -(item['last_missed'].timestamp() if item['last_missed'] else 0)))
    return replay[:limit]


def _group_attempt_sessions(attempts):
    sessions = defaultdict(lambda: {'course': 'General', 'answered_at': None, 'questions': 0, 'correct': 0, 'seconds': 0.0})
    for row in attempts:
        answered_at = _normalise_datetime(_row_value(row, 'answered_at'))
        raw_attempt = _row_value(row, 'attempt_id') or (answered_at.date().isoformat() if answered_at else 'unknown')
        key = (str(raw_attempt), str(_row_value(row, 'course_code') or 'General'))
        session_data = sessions[key]
        session_data['course'] = key[1]
        session_data['questions'] += 1
        session_data['correct'] += int(bool(_row_value(row, 'was_correct')))
        session_data['seconds'] += max(0, _as_float(_row_value(row, 'time_spent_seconds'), 0))
        if answered_at and (not session_data['answered_at'] or answered_at > session_data['answered_at']):
            session_data['answered_at'] = answered_at
    return sorted(sessions.values(), key=lambda item: item['answered_at'] or datetime.min)


def _build_productivity_report(attempts):
    now = datetime.utcnow()
    week_rows = [row for row in attempts if (_normalise_datetime(_row_value(row, 'answered_at')) or datetime.min) >= now - timedelta(days=7)]
    sessions = _group_attempt_sessions(attempts)
    week_sessions = [item for item in sessions if (item['answered_at'] or datetime.min) >= now - timedelta(days=7)]
    total_seconds = sum(max(0, _as_float(_row_value(row, 'time_spent_seconds'), 0)) for row in week_rows)
    total_questions = len(week_rows)
    correct = sum(bool(_row_value(row, 'was_correct')) for row in week_rows)
    active_days = len({(_normalise_datetime(_row_value(row, 'answered_at')) or datetime.min).date() for row in week_rows if _normalise_datetime(_row_value(row, 'answered_at'))})

    all_sessions = sessions
    improvement = None
    if len(all_sessions) >= 6:
        early = all_sessions[:3]
        recent = all_sessions[-3:]
        early_score = sum(item['correct'] / max(1, item['questions']) for item in early) / len(early) * 100
        recent_score = sum(item['correct'] / max(1, item['questions']) for item in recent) / len(recent) * 100
        improvement = round(recent_score - early_score, 1)

    course_values = defaultdict(list)
    for row in attempts:
        course = str(_row_value(row, 'course_code') or 'General')
        course_values[course].append(int(bool(_row_value(row, 'was_correct'))))
    course_scores = {course: sum(values) / len(values) * 100 for course, values in course_values.items() if values}
    weakest = min(course_scores, key=course_scores.get) if course_scores else None
    most_improved = None
    course_session_scores = defaultdict(list)
    for item in sessions:
        course_session_scores[item['course']].append(item['correct'] / max(1, item['questions']) * 100)
    course_deltas = {
        course: values[-1] - values[0]
        for course, values in course_session_scores.items()
        if len(values) >= 2
    }
    if course_deltas:
        most_improved = max(course_deltas, key=course_deltas.get)

    return {
        'week': {
            'study_hours': round(total_seconds / 3600, 1),
            'questions': total_questions,
            'correct': correct,
            'sessions': len(week_sessions),
            'active_days': active_days,
            'improvement': improvement,
            'most_improved_course': most_improved,
            'weakest_course': weakest,
        },
        'all_time': {
            'questions': len(attempts),
            'correct': sum(bool(_row_value(row, 'was_correct')) for row in attempts),
            'sessions': len(all_sessions),
        },
        'evidence_label': 'Weekly figures use recorded question activity from the last seven days. Time is shown only when timing data exists.',
    }


def _build_accountability_notifications(profile, priorities, attempts, productivity):
    now = datetime.utcnow()
    notifications = []
    last_activity = max((_normalise_datetime(_row_value(row, 'answered_at')) for row in attempts if _normalise_datetime(_row_value(row, 'answered_at'))), default=None)
    if not attempts:
        notifications.append({'kind': 'start', 'title': 'Start a focused session', 'message': 'CampusMate has no recent study data yet. Begin with a 20-minute practice session today.', 'action_url': '/premium/coach'})
    elif last_activity and (now - last_activity).days >= 3:
        days = (now - last_activity).days
        notifications.append({'kind': 'inactive', 'title': 'Your study rhythm needs a restart', 'message': f"You haven't studied in {days} days. Complete a 20-minute session today to rebuild momentum.", 'action_url': '/premium/coach'})

    exam_date = _parse_campusmate_date(_row_value(profile, 'exam_date'))
    if exam_date:
        days_until = (exam_date - now.date()).days
        if 0 <= days_until <= 14:
            notifications.append({'kind': 'exam', 'title': 'Exam countdown', 'message': f'Your exam is in {days_until} day{"s" if days_until != 1 else ""}. CampusMate has adjusted your next priorities around the deadline.', 'action_url': '/premium/coach#planner'})

    if priorities and priorities[0].get('retention', 100) < 50:
        top = priorities[0]
        notifications.append({'kind': 'retention', 'title': 'Retention review recommended', 'message': f"Your {top['topic']} retention is estimated at {top['retention']}%. Review it for {top['estimated_minutes']} minutes today.", 'action_url': '/premium/coach#today'})

    if attempts and not notifications:
        minutes = int(_as_float(_row_value(profile, 'daily_minutes'), 60) or 60)
        notifications.append({'kind': 'today', 'title': 'CampusMate daily check-in', 'message': f'Your next best action is a focused {min(30, max(20, minutes))}-minute study block on your highest-priority topic.', 'action_url': '/premium/coach#today'})
    return notifications[:3]


def _persist_and_load_campusmate_notifications(user_id, notifications):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        today_key = datetime.utcnow().date().isoformat()
        for item in notifications:
            dedupe_key = f"{item['kind']}:{today_key}"
            cursor.execute('''
                INSERT INTO campusmate_notifications
                    (user_id, kind, dedupe_key, title, message, action_url, source, severity)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, dedupe_key) DO NOTHING
            ''', (
                user_id,
                item['kind'],
                dedupe_key,
                item.get('title') or 'CampusMate check-in',
                item['message'],
                item.get('action_url'),
                item.get('source') or 'campusmate',
                item.get('severity') or 'info',
            ))
        conn.commit()
        cursor.execute('''
            SELECT id, kind, title, message, action_url, source, severity
            FROM campusmate_notifications
            WHERE user_id = %s AND dismissed_at IS NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 3
        ''', (user_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        icons = {'exam': 'fa-calendar-days', 'retention': 'fa-rotate', 'inactive': 'fa-bolt', 'start': 'fa-compass', 'today': 'fa-check'}
        return [dict(row, icon=icons.get(row['kind'], 'fa-check')) for row in rows]
    except Exception:
        return notifications[:3]


def _load_user_notifications(user_id, limit=30, unread_only=False):
    """Load notification records for the shared notification centre."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        where = 'user_id = %s AND dismissed_at IS NULL'
        params = [user_id]
        if unread_only:
            where += ' AND read_at IS NULL'
        cursor.execute(f'''
            SELECT id, title, kind, message, action_url, source, severity,
                   entity_type, entity_id, created_at, read_at
            FROM campusmate_notifications
            WHERE {where}
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT %s
        ''', (*params, max(1, min(int(limit or 30), 100))))
        rows = cursor.fetchall()
        unread_count = 0
        cursor.execute('''
            SELECT COUNT(*) FROM campusmate_notifications
            WHERE user_id = %s AND dismissed_at IS NULL AND read_at IS NULL
        ''', (user_id,))
        unread_count = int(cursor.fetchone()[0] or 0)
        cursor.close()
        return [dict(row) for row in rows], unread_count
    except Exception:
        return [], 0
    finally:
        if conn:
            conn.close()


def _normalise_mistake_tags(tags, course='', topic='', miss_reasons=None):
    """Return short, safe, de-duplicated tags for filtering and display."""
    values = []
    if isinstance(tags, str):
        values.extend(tags.replace(',', ' ').split())
    elif isinstance(tags, (list, tuple, set)):
        values.extend(tags)
    values.extend([course, topic])
    values.extend(miss_reasons or [])
    result = []
    for value in values:
        text = str(value or '').strip().replace('#', '')
        if not text:
            continue
        text = ''.join(char for char in text if char.isalnum() or char in {' ', '-', '_'})
        text = ''.join(part.capitalize() for part in text.replace('_', ' ').split())
        if text and text.lower() not in {item.lower() for item in result}:
            result.append(text)
    return result[:8]


def _mistake_note_prompt(item):
    return f'''Create a concise learning note for a university CBT learner who missed one question.
Return only one valid JSON object with exactly these fields:
{{"summary":"one sentence explaining the likely confusion","remember":"one or two short sentences stating the distinction or rule","tags":["up to 4 short tags"]}}
Do not use Markdown, HTML, emojis, or dollar-sign math delimiters. Keep mathematical expressions as plain text or short LaTeX text without delimiters. Do not invent facts.
Course: {item.get('course', '')}
Topic: {item.get('topic', '')}
Question: {item.get('question_text', '')}
Learner selected: {item.get('selected_answer', '')}
Correct answer: {item.get('correct_answer', '')}
Existing solution: {item.get('solution', '')}
Miss reasons: {', '.join(item.get('miss_reasons', []))}'''


def _fallback_mistake_note(item):
    selected = item.get('selected_answer') or item.get('selected_option') or 'your selected answer'
    correct = item.get('correct_answer') or item.get('correct_option') or 'the correct answer'
    return {
        'summary': f"You selected {selected}, but the correct answer was {correct}.",
        'remember': str(item.get('solution') or 'Compare the defining clue in the question with the correct option before answering again.')[:500],
        'tags': _normalise_mistake_tags([], item.get('course'), item.get('topic'), item.get('miss_reasons')),
    }


def _build_mistake_heatmap(mistakes):
    """Aggregate unique mistake pressure by course and topic for a compact heat map."""
    counts = defaultdict(int)
    for item in mistakes:
        key = (str(item.get('course') or 'General'), str(item.get('topic') or 'Core concepts'))
        counts[key] += int(item.get('misses') or 0)
    grouped = defaultdict(list)
    for (course, topic), count in counts.items():
        grouped[course].append({'topic': topic, 'mistakes': count})
    result = []
    for course, topics in grouped.items():
        topics.sort(key=lambda row: (-row['mistakes'], row['topic'].lower()))
        result.append({'course': course, 'topics': topics[:10], 'total': sum(row['mistakes'] for row in topics)})
    result.sort(key=lambda row: (-row['total'], row['course'].lower()))
    return result


def _review_interval_for_rating(previous_interval, rating):
    """Apply a conservative 1/3/7/30-day learning schedule after a review."""
    rating = str(rating or '').strip().lower()
    if rating in {'forgot', 'again', 'incorrect'}:
        return 1
    if rating in {'hard', 'almost'}:
        return min(30, max(3, int(previous_interval or 1) * 2))
    if rating in {'got_it', 'correct', 'remembered'}:
        sequence = {1: 3, 3: 7, 7: 30}
        return sequence.get(int(previous_interval or 1), 30)
    return min(30, max(1, int(previous_interval or 1)))


def _record_retest_review(user_id, question_id, rating):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('''
            SELECT review_interval_days, review_count
            FROM premium_mistake_notes
            WHERE user_id = %s AND question_id = %s
        ''', (user_id, question_id))
        prior = cursor.fetchone() or {}
        interval = _review_interval_for_rating(prior.get('review_interval_days', 1), rating)
        now = datetime.utcnow()
        next_review = now + timedelta(days=interval)
        cursor.execute('''
            INSERT INTO premium_mistake_notes
                (user_id, question_id, next_review_at, last_reviewed_at,
                 review_interval_days, review_count, last_review_result, updated_at)
            VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT (user_id, question_id) DO UPDATE SET
                next_review_at = EXCLUDED.next_review_at,
                last_reviewed_at = EXCLUDED.last_reviewed_at,
                review_interval_days = EXCLUDED.review_interval_days,
                review_count = premium_mistake_notes.review_count + 1,
                last_review_result = EXCLUDED.last_review_result,
                updated_at = EXCLUDED.updated_at
        ''', (user_id, question_id, next_review, now, interval, rating, now))
        conn.commit()
        return jsonify({'ok': True, 'next_review': next_review.isoformat(), 'interval_days': interval, 'rating': rating})
    except Exception:
        if conn:
            conn.rollback()
        flask_app.logger.exception('Mistake review scheduling failed')
        return jsonify({'ok': False, 'message': 'We could not save this review right now.'}), 500
    finally:
        if conn:
            conn.close()


def _build_campusmate_plan(exam_date, courses, daily_minutes, attempts, emergency=False, existing_plan=None):
    today = datetime.utcnow().date()
    days_until = max(0, (exam_date - today).days) if exam_date else 7
    day_count = 3 if emergency or days_until <= 3 else min(7, max(1, days_until))
    courses = [str(course).strip().upper() for course in courses if str(course).strip()]
    courses = list(dict.fromkeys(courses))[:12]
    if not courses:
        courses = sorted({str(_row_value(row, 'course_code') or '').strip().upper() for row in attempts if _row_value(row, 'course_code')})[:12]
    if not courses:
        courses = ['Your priority course']

    course_rows = defaultdict(list)
    for row in attempts:
        course_rows[str(_row_value(row, 'course_code') or 'General').strip().upper()].append(row)
    course_priority = {}
    for course in courses:
        rows = course_rows.get(course, [])
        accuracy = sum(bool(_row_value(row, 'was_correct')) for row in rows) / len(rows) * 100 if rows else 50
        course_priority[course] = max(1, 100 - accuracy)
    ordered_courses = sorted(courses, key=lambda course: (-course_priority[course], course))

    topic_by_course = defaultdict(list)
    for row in attempts:
        course = str(_row_value(row, 'course_code') or '').strip().upper()
        topic = str(_row_value(row, 'topic') or 'Core concepts').strip()
        if course and topic not in topic_by_course[course]:
            topic_by_course[course].append(topic)

    plan = []
    for index in range(day_count):
        plan_date = min(today + timedelta(days=index), exam_date) if exam_date else today + timedelta(days=index)
        if emergency:
            focus_type = ('Highest-priority topics', 'Targeted practice questions', 'Mistake review and mock exam')[min(index, 2)]
            selected = ordered_courses[:2] if index == 0 else ordered_courses[:1]
            allocations = []
            remaining = max(20, int(daily_minutes))
            for position, course in enumerate(selected):
                minutes = remaining if len(selected) == 1 else (round(remaining * 0.6) if position == 0 else remaining - round(remaining * 0.6))
                allocations.append({'course': course, 'minutes': max(15, minutes), 'focus': focus_type, 'topic': (topic_by_course[course] or ['Core concepts'])[0]})
                remaining -= minutes
            task = focus_type
        else:
            primary = ordered_courses[index % len(ordered_courses)]
            secondary = ordered_courses[(index + 1) % len(ordered_courses)] if len(ordered_courses) > 1 and daily_minutes >= 75 else None
            allocations = [{'course': primary, 'minutes': int(daily_minutes if not secondary else round(daily_minutes * 0.6)), 'focus': 'Priority review and targeted practice', 'topic': (topic_by_course[primary] or ['Core concepts'])[index % max(1, len(topic_by_course[primary]) or 1)]}]
            if secondary:
                allocations.append({'course': secondary, 'minutes': int(daily_minutes - allocations[0]['minutes']), 'focus': 'Active recall and short practice set', 'topic': (topic_by_course[secondary] or ['Core concepts'])[0]})
            task = 'Complete the planned blocks, then record what still feels unclear.'

        plan.append({'day': f'Day {index + 1}', 'date': plan_date.isoformat(), 'task': task, 'allocations': allocations, 'completed': False})

    if existing_plan:
        previous = {item.get('date'): item for item in existing_plan if isinstance(item, dict)}
        for item in plan:
            if previous.get(item['date'], {}).get('completed'):
                item['completed'] = True
    return {'days': plan, 'exam_date': exam_date.isoformat() if exam_date else None, 'courses': ordered_courses, 'daily_minutes': int(daily_minutes), 'emergency': bool(emergency), 'days_until_exam': days_until}


def _load_campusmate_data(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('''
        SELECT a.id, a.attempt_id, a.question_id, a.course_code, a.topic,
               a.selected_option, a.correct_option, a.was_correct, a.confidence,
               a.miss_reason, a.time_spent_seconds, a.answered_at,
               q.question_text, q.option_a, q.option_b, q.option_c, q.option_d, q.solution
        FROM premium_question_attempts a
        LEFT JOIN questions q ON q.id = a.question_id
        WHERE a.user_id = %s
        ORDER BY a.answered_at DESC, a.id DESC
        LIMIT 3000
    ''', (user_id,))
    attempts = cursor.fetchall()
    cursor.execute('SELECT * FROM campusmate_profiles WHERE user_id = %s', (user_id,))
    profile_row = cursor.fetchone()
    cursor.execute('''
        SELECT id, exam_date, available_minutes, courses, plan, status, updated_at
        FROM campusmate_exam_plans
        WHERE user_id = %s AND status = 'active'
        ORDER BY updated_at DESC, id DESC LIMIT 1
    ''', (user_id,))
    plan_row = cursor.fetchone()
    cursor.close()
    conn.close()
    return attempts, profile_row, plan_row


def _save_campusmate_profile(user_id, payload):
    allowed = ('university', 'department', 'level', 'semester')
    values = {key: str(payload.get(key, '') or '').strip()[:120] for key in allowed}
    exam_date = _parse_campusmate_date(payload.get('exam_date'))
    daily_minutes = int(payload.get('daily_minutes', 60))
    daily_minutes = max(20, min(480, daily_minutes))
    courses = payload.get('courses', [])
    if isinstance(courses, str):
        courses = courses.split(',')
    courses = list(dict.fromkeys(str(course).strip().upper() for course in courses if str(course).strip()))[:12]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO campusmate_profiles (user_id, university, department, level, semester, exam_date, daily_minutes, courses, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id) DO UPDATE SET
            university = EXCLUDED.university, department = EXCLUDED.department,
            level = EXCLUDED.level, semester = EXCLUDED.semester,
            exam_date = EXCLUDED.exam_date, daily_minutes = EXCLUDED.daily_minutes,
            courses = EXCLUDED.courses, updated_at = CURRENT_TIMESTAMP
    ''', (user_id, values['university'], values['department'], values['level'], values['semester'], exam_date, daily_minutes, psycopg2.extras.Json(courses)))
    conn.commit()
    cursor.close()
    conn.close()
    return {**values, 'exam_date': exam_date, 'daily_minutes': daily_minutes, 'courses': courses}


def _save_campusmate_plan(user_id, plan):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE campusmate_exam_plans SET status = 'archived' WHERE user_id = %s AND status = 'active'", (user_id,))
    cursor.execute('''
        INSERT INTO campusmate_exam_plans (user_id, exam_date, available_minutes, courses, plan, status, updated_at)
        VALUES (%s, %s, %s, %s, %s, 'active', CURRENT_TIMESTAMP)
    ''', (
        user_id,
        _parse_campusmate_date(plan.get('exam_date')) or datetime.utcnow().date(),
        int(plan.get('daily_minutes', 60)),
        psycopg2.extras.Json(plan.get('courses', [])),
        psycopg2.extras.Json(plan.get('days', [])),
    ))
    conn.commit()
    cursor.close()
    conn.close()


def refresh_campusmate_plan_after_activity(user_id):
    """Recompute an active plan after a quiz without ever breaking quiz submission."""
    try:
        attempts, profile_row, plan_row = _load_campusmate_data(user_id)
        profile = _campusmate_context(profile_row)
        exam_date = _parse_campusmate_date(profile.get('exam_date'))
        if not exam_date or not profile.get('courses'):
            return False
        existing = _safe_json(_row_value(plan_row, 'plan'), []) if plan_row else []
        plan = _build_campusmate_plan(exam_date, profile['courses'], profile['daily_minutes'], attempts, existing_plan=existing)
        _save_campusmate_plan(user_id, plan)
        return True
    except Exception:
        return False


def _campusmate_conversation_id(raw_value):
    value = str(raw_value or '').strip()
    if not value:
        return uuid.uuid4().hex
    if len(value) > 64 or not all(character.isalnum() or character in '-_' for character in value):
        raise ValueError('Invalid conversation identifier.')
    return value


def _load_campusmate_conversation(user_id, conversation_id, limit=12):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('''
        SELECT mode, question, response, created_at
        FROM campusmate_coach_history
        WHERE user_id = %s AND conversation_id = %s
        ORDER BY created_at ASC, id ASC
        LIMIT %s
    ''', (user_id, conversation_id, limit))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    turns = []
    for row in rows:
        turns.append({
            'role': 'user',
            'content': _plain_tutor_text(row['question'], 3000),
            'mode': row['mode'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
        })
        structured = _normalize_stored_tutor_content(row['response'])
        turns.append({
            'role': 'assistant',
            'content': structured.get('explanation') or structured.get('answer') or '',
            'structured': structured,
            'mode': row['mode'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
        })
    return turns


def _normalise_course_list(raw, limit=12):
    """Normalize learner course input without allowing unbounded or malformed values."""
    values = raw.split(',') if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    cleaned = []
    for value in values:
        course = re.sub(r'[^A-Za-z0-9_-]', '', str(value or '').strip().upper())[:30]
        if course and course not in cleaned:
            cleaned.append(course)
    return cleaned[:limit]


def _bounded_int(value, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _adaptive_question_rows(conn, user_id, courses=None, topic='', limit=10):
    """Select questions using learner-specific weakness, recency, and unseen-question signals."""
    clauses = []
    params = [user_id]
    if courses:
        clauses.append('q.course_code = ANY(%s)')
        params.append(courses)
    if topic:
        clauses.append('LOWER(COALESCE(q.topic, \'\')) = LOWER(%s)')
        params.append(topic[:200])
    where_sql = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    params.append(limit)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute(f'''
        SELECT q.id, q.course_code, q.topic, q.question_text,
               q.option_a, q.option_b, q.option_c, q.option_d,
               q.correct_option, q.solution, q.content_json,
               COALESCE(stats.total_attempts, 0) AS learner_attempts,
               COALESCE(stats.missed_attempts, 0) AS learner_misses,
               stats.last_answered
        FROM questions q
        LEFT JOIN (
            SELECT question_id, COUNT(*) AS total_attempts,
                   SUM(CASE WHEN NOT was_correct THEN 1 ELSE 0 END) AS missed_attempts,
                   MAX(answered_at) AS last_answered
            FROM premium_question_attempts
            WHERE user_id = %s
            GROUP BY question_id
        ) stats ON stats.question_id = q.id
        {where_sql}
        ORDER BY
            CASE WHEN COALESCE(stats.missed_attempts, 0) > 0 THEN 0
                 WHEN COALESCE(stats.total_attempts, 0) = 0 THEN 1 ELSE 2 END,
            COALESCE(stats.missed_attempts, 0) DESC,
            COALESCE(stats.last_answered, TIMESTAMP '1970-01-01') ASC,
            q.id ASC
        LIMIT %s
    ''', tuple(params))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def _mock_question_payload(row):
    """Return a CBT-safe question object without exposing the answer key."""
    payload = _question_from_row(row, source='premium_mock_exam')
    payload.pop('correct_option', None)
    payload.pop('solution', None)
    payload.pop('solution_content', None)
    return payload


def _mock_session_payload(row):
    return {
        'id': _row_value(row, 'id'),
        'title': _row_value(row, 'title') or 'Premium Mock Exam',
        'course_codes': _safe_json(_row_value(row, 'course_codes'), []),
        'question_ids': [int(value) for value in (_safe_json(_row_value(row, 'question_ids'), []) or []) if str(value).isdigit()],
        'answers': _safe_json(_row_value(row, 'answers'), {}) or {},
        'duration_seconds': int(_row_value(row, 'duration_seconds') or 3600),
        'started_at': _normalise_datetime(_row_value(row, 'started_at')),
        'submitted_at': _normalise_datetime(_row_value(row, 'submitted_at')),
        'status': _row_value(row, 'status') or 'active',
        'score': _row_value(row, 'score'),
        'total': _row_value(row, 'total'),
    }


def register_premium_routes(flask_app: Flask):
    """Register all premium routes with the Flask application"""
    
    # ==================== PREMIUM HUB ====================

    @flask_app.route('/premium')
    def premium():
        """Render the AI tool chooser without blocking on status lookup."""
        user_is_premium = False

        if 'user_id' in session:
            try:
                user_is_premium = is_premium_user(session['user_id'])
            except Exception:
                flask_app.logger.exception(
                    'Premium status lookup failed while opening AI tools'
                )

        return render_template(
            'premium/hub.html',
            user_is_premium=user_is_premium
        )

    
    # ==================== PREMIUM DASHBOARD ====================
    @flask_app.route('/premium/dashboard')
    @premium_required
    def premium_dashboard():
        """Render the premium command centre with explainable, real learner data."""
        user_id = session['user_id']
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()

            cursor.execute('''
                SELECT id, score, total, course_code, created_at
                FROM scores
                WHERE user_id = %s
                ORDER BY created_at DESC NULLS LAST, id DESC
                LIMIT 10
            ''', (user_id,))
            recent_scores = cursor.fetchall()

            cursor.execute('''
                SELECT score, total, course_code, created_at
                FROM scores
                WHERE user_id = %s
                ORDER BY created_at DESC NULLS LAST, id DESC
                LIMIT 100
            ''', (user_id,))
            score_rows = cursor.fetchall()

            cursor.execute('''
                SELECT a.id, a.attempt_id, a.question_id, a.course_code, a.topic,
                       a.selected_option, a.correct_option, a.was_correct,
                       a.confidence, a.miss_reason, a.time_spent_seconds, a.answered_at,
                       q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
                       q.solution, q.content_json
                FROM premium_question_attempts a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE a.user_id = %s
                ORDER BY a.answered_at DESC NULLS LAST, a.id DESC
                LIMIT 3000
            ''', (user_id,))
            attempts = cursor.fetchall()

            cursor.execute('SELECT * FROM campusmate_profiles WHERE user_id = %s', (user_id,))
            profile = cursor.fetchone()

            cursor.execute('''
                SELECT id, exam_date, available_minutes, courses, plan, status, updated_at
                FROM campusmate_exam_plans
                WHERE user_id = %s AND status = 'active'
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT 1
            ''', (user_id,))
            active_plan = cursor.fetchone()
            cursor.close()

            premium_info = get_user_premium_info(user_id)
            insights = _build_performance_insights(attempts, score_rows)
            priorities = _build_campusmate_priorities(attempts)
            productivity = _build_productivity_report(attempts)
            streaks = insights.get('streaks') or _build_streaks(attempts)
            generated_notifications = _build_accountability_notifications(
                profile, priorities, attempts, productivity
            )
            notifications = _persist_and_load_campusmate_notifications(
                user_id, generated_notifications
            )

            exam_date = _parse_campusmate_date(_row_value(profile, 'exam_date'))
            days_until_exam = (exam_date - datetime.utcnow().date()).days if exam_date else None
            exam_countdown = {
                'date': exam_date,
                'days': days_until_exam,
                'is_urgent': days_until_exam is not None and 0 <= days_until_exam <= 7,
                'is_valid': days_until_exam is not None and days_until_exam >= 0,
            }
            top_priority = priorities[0] if priorities else None
            today_action = {
                'title': f"Review {top_priority['topic']}" if top_priority else 'Start your first focused session',
                'message': top_priority.get('reason') if top_priority else 'Complete a short premium practice set so CampusMate can personalise your next steps.',
                'url': url_for('mistake_notebook') if top_priority and top_priority.get('recent_misses') else url_for('ai_questions'),
            }

            return render_template(
                'premium/dashboard.html',
                user=user,
                display_name=_row_value(user, 'username', 'learner'),
                premium_info=premium_info,
                recent_scores=recent_scores,
                avg_score=insights.get('predicted_exam', {}).get('score') or 0,
                insights=insights,
                priorities=priorities,
                productivity=productivity,
                streaks=streaks,
                notifications=notifications,
                profile=profile,
                active_plan=active_plan,
                exam_countdown=exam_countdown,
                today_action=today_action,
            )
        except Exception:
            flask_app.logger.exception('Premium dashboard loading failed')
            flash('We could not load your premium dashboard right now. Please try again.', 'error')
            return redirect(url_for('premium'))
        finally:
            if conn:
                conn.close()
    
    # ==================== CAMPUSMATE AI STUDY PARTNER ====================
    @flask_app.route('/premium/coach')
    @premium_required
    def ai_coach():
        """CampusMate's persistent premium study workspace."""
        user_id = session['user_id']
        try:
            attempts, profile_row, plan_row = _load_campusmate_data(user_id)
            profile = _campusmate_context(profile_row)
            priorities = _build_campusmate_priorities(attempts)
            diagnosis = _build_failure_diagnosis(attempts)
            replay = _build_mistake_replay(attempts)
            productivity = _build_productivity_report(attempts)
            notifications = _build_accountability_notifications(profile, priorities, attempts, productivity)
            notifications = _persist_and_load_campusmate_notifications(user_id, notifications)
            plan = None
            if plan_row:
                plan = {
                    'id': plan_row['id'],
                    'exam_date': plan_row['exam_date'].isoformat() if plan_row['exam_date'] else None,
                    'daily_minutes': plan_row['available_minutes'],
                    'courses': _safe_json(plan_row['courses'], []),
                    'days': _safe_json(plan_row['plan'], []),
                    'updated_at': plan_row['updated_at'],
                }
            elif profile.get('exam_date') and profile.get('courses'):
                draft = _build_campusmate_plan(profile['exam_date'], profile['courses'], profile['daily_minutes'], attempts)
                plan = {'id': None, **draft}
            exam_date = _parse_campusmate_date(profile.get('exam_date'))
            emergency_plan = None
            if exam_date and (exam_date - datetime.utcnow().date()).days <= 3 and (exam_date - datetime.utcnow().date()).days >= 0:
                emergency_plan = _build_campusmate_plan(exam_date, profile.get('courses', []), profile.get('daily_minutes', 60), attempts, emergency=True)
            return render_template(
                'premium/coach.html',
                campusmate_profile=profile,
                priorities=priorities,
                diagnosis=diagnosis,
                replay=replay,
                productivity=productivity,
                notifications=notifications,
                plan=plan,
                emergency_plan=emergency_plan,
                total_attempts=len(attempts),
                last_activity=max((_normalise_datetime(_row_value(row, 'answered_at')) for row in attempts if _normalise_datetime(_row_value(row, 'answered_at'))), default=None),
            )
        except Exception:
            flask_app.logger.exception('CampusMate workspace loading failed')
            flash('We could not load CampusMate right now. Please try again in a moment.', 'error')
            return redirect(url_for('premium_dashboard'))

    @flask_app.route('/premium/coach/profile', methods=['POST'])
    @premium_required
    def save_campusmate_profile():
        """Save only learner-supplied context used for personalisation."""
        payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
        try:
            profile = _save_campusmate_profile(session['user_id'], payload)
            if profile.get('exam_date') and profile.get('courses'):
                attempts, _, plan_row = _load_campusmate_data(session['user_id'])
                existing = _safe_json(_row_value(plan_row, 'plan'), []) if plan_row else None
                plan = _build_campusmate_plan(profile['exam_date'], profile['courses'], profile['daily_minutes'], attempts, existing_plan=existing)
                _save_campusmate_plan(session['user_id'], plan)
            return jsonify({'success': True, 'message': 'CampusMate profile updated.'})
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Please check your profile values and try again.'}), 400
        except Exception:
            flask_app.logger.exception('CampusMate profile save failed')
            return jsonify({'success': False, 'error': 'We could not save your CampusMate profile right now.'}), 500

    @flask_app.route('/premium/coach/planner', methods=['POST'])
    @premium_required
    def save_campusmate_planner():
        """Create or adapt the active exam plan from a validated learner request."""
        payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
        exam_date = _parse_campusmate_date(payload.get('exam_date'))
        if not exam_date or exam_date < datetime.utcnow().date():
            return jsonify({'success': False, 'error': 'Choose an exam date today or later.'}), 400
        courses = payload.get('courses', [])
        if isinstance(courses, str):
            courses = courses.split(',')
        courses = [str(course).strip().upper() for course in courses if str(course).strip()]
        if not courses:
            return jsonify({'success': False, 'error': 'Add at least one course to build your exam plan.'}), 400
        try:
            daily_minutes = max(20, min(480, int(payload.get('daily_minutes', 60))))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Daily study time must be a whole number of minutes.'}), 400
        try:
            _save_campusmate_profile(session['user_id'], {**payload, 'exam_date': exam_date.isoformat(), 'courses': courses, 'daily_minutes': daily_minutes})
            attempts, _, plan_row = _load_campusmate_data(session['user_id'])
            existing = _safe_json(_row_value(plan_row, 'plan'), []) if plan_row else None
            emergency = bool(payload.get('emergency')) or (exam_date - datetime.utcnow().date()).days <= 3
            plan = _build_campusmate_plan(exam_date, courses, daily_minutes, attempts, emergency=emergency, existing_plan=existing)
            _save_campusmate_plan(session['user_id'], plan)
            return jsonify({'success': True, 'plan': plan})
        except Exception:
            flask_app.logger.exception('CampusMate exam planner save failed')
            return jsonify({'success': False, 'error': 'We could not build your plan right now. Please try again.'}), 500

    @flask_app.route('/premium/coach/planner/complete', methods=['POST'])
    @premium_required
    def complete_campusmate_plan_day():
        payload = request.get_json(silent=True) or {}
        selected_date = str(payload.get('date', '')).strip()
        if not selected_date:
            return jsonify({'success': False, 'error': 'A plan date is required.'}), 400
        try:
            attempts, profile_row, plan_row = _load_campusmate_data(session['user_id'])
            if not plan_row:
                return jsonify({'success': False, 'error': 'Create an exam plan first.'}), 404
            days = _safe_json(_row_value(plan_row, 'plan'), [])
            found = False
            for item in days:
                if isinstance(item, dict) and item.get('date') == selected_date:
                    item['completed'] = True
                    found = True
            if not found:
                return jsonify({'success': False, 'error': 'That study day is not in the active plan.'}), 404
            plan = {'exam_date': plan_row['exam_date'].isoformat(), 'courses': _safe_json(_row_value(plan_row, 'courses'), []), 'daily_minutes': plan_row['available_minutes'], 'days': days}
            _save_campusmate_plan(session['user_id'], plan)
            return jsonify({'success': True, 'date': selected_date})
        except Exception:
            flask_app.logger.exception('CampusMate plan completion failed')
            return jsonify({'success': False, 'error': 'We could not update that study day right now.'}), 500

    @flask_app.route('/premium/coach/tutor', methods=['POST'])
    @premium_required
    def campusmate_tutor():
        """Provide lecturer, alternate-explanation, and Socratic tutoring modes."""
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get('mode', 'study_with_me')).strip().lower()
        allowed_modes = {'lecturer', 'simplify', 'analogy', 'nigerian_example', 'step_by_step', 'visual', 'study_with_me'}
        if mode not in allowed_modes:
            return jsonify({'success': False, 'error': 'Choose a supported CampusMate teaching mode.'}), 400
        question = str(payload.get('question', '')).strip()
        topic = str(payload.get('topic', '')).strip()
        if not question and not topic:
            return jsonify({'success': False, 'error': 'Enter a question or topic for CampusMate.'}), 400
        if len(question) > 3000 or len(topic) > 200:
            return jsonify({'success': False, 'error': 'Please keep the topic and question concise.'}), 400
        try:
            conversation_id = _campusmate_conversation_id(payload.get('conversation_id'))
            previous_turns = _load_campusmate_conversation(session['user_id'], conversation_id) if payload.get('conversation_id') else []
            attempts, profile_row, _ = _load_campusmate_data(session['user_id'])
            profile = _campusmate_context(profile_row)
            priorities = _build_campusmate_priorities(attempts)
            mode_instructions = {
                'lecturer': 'Explain this as a clear 100-level university lecturer. Use the supplied university, department, level, semester, and course context, but do not invent institution-specific rules.',
                'simplify': 'Explain the same idea in simpler language without removing the important meaning.',
                'analogy': 'Use one accurate everyday analogy, then map each part of the analogy back to the concept.',
                'nigerian_example': 'Use a familiar Nigerian real-life example only when it genuinely clarifies the concept; do not use stereotypes or unsupported claims.',
                'step_by_step': 'Teach the idea as a short sequence of numbered steps and finish with a self-check question.',
                'visual': 'Describe a simple diagram or mental model in text, including labels and relationships.',
                'study_with_me': 'Use a Socratic coaching sequence. First ask what the learner already knows, what answer they think is correct, and what part is confusing. Give a hint before giving a complete explanation.',
            }
            context_lines = [
                f"University: {profile['university'] or 'Not provided'}",
                f"Department: {profile['department'] or 'Not provided'}",
                f"Level: {profile['level'] or 'Not provided'}",
                f"Semester: {profile['semester'] or 'Not provided'}",
                f"Course: {payload.get('course') or (priorities[0]['course'] if priorities else 'Not provided')}",
                f"Topic: {topic or 'Not provided'}",
                f"Known priority topics: {', '.join(item['topic'] for item in priorities[:4]) or 'Not enough data'}",
            ]
            learner_context = '\n'.join(context_lines)
            conversation_context = '\n'.join(f"{turn['role'].title()}: {turn['content']}" for turn in previous_turns[-8:]) or 'No earlier turns in this conversation.'
            prompt = f"CampusMate mode: {mode}\n\nLearner context:\n{learner_context}\n\nRecent conversation:\n{conversation_context}\n\nNew learner message:\n{question or topic}\n\nTeaching requirement:\n{mode_instructions[mode]}\n\nRespond to the new message as a continuation of the conversation. Do not repeat the full previous answer unless it is needed. Use a warm senior-colleague tone. Be accurate, concise, and end with one actionable check for understanding."
            response = _generate_campusmate_tutor_response(
                prompt,
                fallback_title=topic or payload.get('course') or 'CampusMate lesson',
            )
            response_json = json.dumps(response, ensure_ascii=False)
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('INSERT INTO campusmate_coach_history (user_id, conversation_id, mode, question, response) VALUES (%s, %s, %s, %s, %s)', (session['user_id'], conversation_id, mode, question or topic, response_json))
            cursor.execute('DELETE FROM campusmate_coach_history WHERE user_id = %s AND id NOT IN (SELECT id FROM campusmate_coach_history WHERE user_id = %s ORDER BY created_at DESC LIMIT 50)', (session['user_id'], session['user_id']))
            conn.commit()
            cursor.close()
            conn.close()
            turns = previous_turns + [
                {'role': 'user', 'content': question or topic, 'mode': mode},
                {
                    'role': 'assistant',
                    'content': response.get('explanation') or response.get('answer') or '',
                    'structured': response,
                    'mode': mode,
                },
            ]
            return jsonify({'success': True, 'conversation_id': conversation_id, 'mode': mode, 'response': response, 'turns': turns})
        except AIProviderError as exc:
            flask_app.logger.error('CampusMate tutor providers failed: %s', exc.attempts)
            return jsonify({'success': False, 'error': _friendly_ai_error(exc, 'CampusMate tutor')}), 502
        except ValueError:
            flask_app.logger.warning('CampusMate returned an invalid structured lesson')
            return jsonify({'success': False, 'error': 'CampusMate returned an incomplete lesson. Please try again.'}), 502
        except Exception:
            flask_app.logger.exception('CampusMate tutor failed')
            return jsonify({'success': False, 'error': 'CampusMate could not prepare that lesson right now. Please try again.'}), 502

    @flask_app.route('/premium/coach/history/<conversation_id>')
    @premium_required
    def campusmate_conversation_history(conversation_id):
        try:
            conversation_id = _campusmate_conversation_id(conversation_id)
            turns = _load_campusmate_conversation(session['user_id'], conversation_id)
            return jsonify({'success': True, 'conversation_id': conversation_id, 'turns': turns})
        except ValueError:
            return jsonify({'success': False, 'error': 'That conversation identifier is invalid.'}), 400
        except Exception:
            flask_app.logger.exception('CampusMate conversation history loading failed')
            return jsonify({'success': False, 'error': 'We could not load that conversation right now.'}), 500

    @flask_app.route('/premium/coach/notifications/<int:notification_id>/dismiss', methods=['POST'])
    @premium_required
    def dismiss_campusmate_notification(notification_id):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE campusmate_notifications
                SET dismissed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s AND dismissed_at IS NULL
            ''', (notification_id, session['user_id']))
            updated = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({'success': bool(updated)})
        except Exception:
            flask_app.logger.exception('CampusMate notification dismissal failed')
            return jsonify({'success': False, 'error': 'We could not dismiss that notification right now.'}), 500

    @flask_app.route('/premium/coach/replay/<int:question_id>')
    @premium_required
    def campusmate_replay_question(question_id):
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT DISTINCT ON (a.question_id)
                       a.question_id, a.course_code, a.topic, q.question_text,
                       q.option_a, q.option_b, q.option_c, q.option_d,
                       q.correct_option, q.solution
                FROM premium_question_attempts a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE a.user_id = %s AND a.question_id = %s AND a.was_correct = FALSE
                ORDER BY a.question_id, a.answered_at DESC, a.id DESC
            ''', (session['user_id'], question_id))
            question = cursor.fetchone()
            cursor.close()
            conn.close()
            if not question:
                return jsonify({'success': False, 'error': 'That replay question is no longer available.'}), 404
            return jsonify({'success': True, **dict(question)})
        except Exception:
            flask_app.logger.exception('CampusMate replay question loading failed')
            return jsonify({'success': False, 'error': 'We could not load that replay question right now.'}), 500

    @flask_app.route('/premium/coach/ask', methods=['POST'])
    @premium_required
    def ask_coach():
        """Backward-compatible endpoint for the legacy coach form."""
        data = request.get_json(silent=True) or {}
        question = str(data.get('question', '')).strip()
        if not question:
            return jsonify({'error': 'Question is required'}), 400
        try:
            answer = get_coach_response(question)
            return jsonify({'answer': answer})
        except AIProviderError as exc:
            flask_app.logger.error('CampusMate legacy tutor providers failed: %s', exc.attempts)
            return jsonify({'error': _friendly_ai_error(exc, 'CampusMate tutor')}), 502
        except Exception:
            flask_app.logger.exception('CampusMate legacy tutor failed')
            return jsonify({'error': 'CampusMate is temporarily unavailable. Please try again.'}), 502

    # ==================== AI CHAT ====================

    @flask_app.route('/premium/chat')
    @premium_required
    def ai_chat():
        """AI chat assistant"""
        return render_template('premium/chat.html')


    @flask_app.route('/premium/chat/send', methods=['POST'])
    @premium_required
    def send_ai_chat():
        """Send a message to the premium AI tutor."""
        data = request.get_json(silent=True) or {}
        message = str(data.get('message', '')).strip()

        if not message:
            return jsonify({
                'error': 'Please enter a question or topic before sending your message.'
            }), 400

        if len(message) > 4000:
            return jsonify({
                'error': 'Please keep your message under 4,000 characters.'
            }), 400

        try:
            return jsonify({'reply': get_coach_response(message)})
        except AIProviderError as exc:
            flask_app.logger.error(
                'Premium AI chat providers failed: %s',
                exc.attempts,
            )
            return jsonify({
                'error': _friendly_ai_error(exc, 'AI tutor'),
            }), 502
        except Exception:
            flask_app.logger.exception('Premium AI chat failed')
            return jsonify({
                'error': (
                    "We couldn't prepare a response right now. "
                    "Please try again in a moment."
                ),
            }), 502
        
        except Exception:
            flask_app.logger.exception('Premium AI chat failed')
            return jsonify({
                'error': 'The AI tutor is temporarily unavailable. Please try again.'
            }), 502

    # ==================== AI QUESTIONS ====================

    @flask_app.route('/premium/questions')
    @premium_required
    def ai_questions():
        usage = get_ai_usage(session['user_id'])
        return render_template('premium/questions.html', ai_usage=usage)

    @flask_app.route('/premium/questions/generate', methods=['POST'])
    @premium_required
    def generate_ai_questions():
        data = request.get_json(silent=True) or {}
        course = str(data.get('course', '')).strip()
        topic = str(data.get('topic', '')).strip()
        difficulty = str(data.get('difficulty', 'Medium')).strip() or 'Medium'
        question_type = str(data.get('question_type', 'mcq')).strip() or 'mcq'
        try:
            count = int(data.get('count', 10))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Question count must be a valid number'}), 400

        if not course or not topic:
            return jsonify({
                'success': False,
                'error': 'Please enter both a course and a topic to build your practice set.'
            }), 400
        if count < 1 or count > 30:
            return jsonify({
                'success': False,
                'error': 'Choose between 1 and 30 questions for each practice set.'
            }), 400
        if difficulty not in {'Easy', 'Medium', 'Hard'}:
            return jsonify({
                'success': False,
                'error': 'Please choose Easy, Medium, or Hard difficulty.'
            }), 400
        if question_type not in {'mcq', 'theory'}:
            return jsonify({
                'success': False,
                'error': 'Please choose a supported question format.'
            }), 400
        
        try:
            questions, usage = generate_questions(
                user_id=session['user_id'],
                course=course,
                topic=topic,
                difficulty=difficulty,
                question_type=question_type,
                count=count,
            )
            return jsonify({
                'success': True,
                'questions': questions,
                'usage': usage,
            })
        
        except DailyAILimitReached:
            usage = get_ai_usage(session['user_id'])
            return jsonify({
                'success': False,
                'error': (
                    "You've used today's AI question allowance. "
                    "It resets tomorrow, so you can generate more questions then."
                ),
                'usage': usage,
            }), 429

        except AIProviderError as exc:
            flask_app.logger.error(
                'Premium AI question providers failed: %s',
                exc.attempts,
            )
            return jsonify({
                'success': False,
                'error': _friendly_ai_error(exc, 'question generator'),
            }), 502

        except Exception:
            flask_app.logger.exception('Premium AI question generation failed')
            return jsonify({
                'success': False,
                'error': (
                    "We couldn't prepare your question set right now. "
                    "Please try again in a moment."
                ),
            }), 502
                
        # ==================== STUDY PLAN ====================
    @flask_app.route('/premium/study-plan')
    @premium_required
    def study_plan():
        """Show the durable adaptive study plan and its current progress."""
        try:
            attempts, profile_row, plan_row = _load_campusmate_data(session['user_id'])
            profile = _campusmate_context(profile_row)
            plan = None
            if plan_row:
                plan = {
                    'id': _row_value(plan_row, 'id'),
                    'exam_date': _parse_campusmate_date(_row_value(plan_row, 'exam_date')),
                    'daily_minutes': int(_row_value(plan_row, 'available_minutes') or profile['daily_minutes'] or 60),
                    'courses': _safe_json(_row_value(plan_row, 'courses'), []) or [],
                    'days': _safe_json(_row_value(plan_row, 'plan'), []) or [],
                    'updated_at': _normalise_datetime(_row_value(plan_row, 'updated_at')),
                }
            elif profile.get('exam_date') and profile.get('courses'):
                draft = _build_campusmate_plan(
                    _parse_campusmate_date(profile['exam_date']),
                    profile['courses'],
                    profile['daily_minutes'],
                    attempts,
                )
                plan = {'id': None, **draft}

            if plan:
                days = plan.get('days') or []
                completed = sum(1 for item in days if isinstance(item, dict) and item.get('completed'))
                plan['completed_days'] = completed
                plan['total_days'] = len(days)
                plan['progress_percent'] = round((completed / len(days)) * 100) if days else 0
                plan['days_until_exam'] = ((plan.get('exam_date') or datetime.utcnow().date()) - datetime.utcnow().date()).days

            available_courses = sorted({
                str(_row_value(row, 'course_code') or '').strip().upper()
                for row in attempts
                if _row_value(row, 'course_code')
            } | {str(course).strip().upper() for course in profile.get('courses', []) if str(course).strip()})
            return render_template(
                'premium/study_plan.html',
                plan=plan,
                campusmate_profile=profile,
                available_courses=available_courses,
                total_attempts=len(attempts),
            )
        except Exception:
            flask_app.logger.exception('Premium study plan loading failed')
            flash('We could not load your study plan right now. Please try again in a moment.', 'error')
            return redirect(url_for('premium_dashboard'))

    @flask_app.route('/premium/questions/record-attempts', methods=['POST'])
    @premium_required
    def record_ai_attempts():
        """Persist completed premium AI practice sets for later review."""
        payload = request.get_json(silent=True) or {}
        course_code = str(payload.get('course') or '').strip()[:100]
        topic = str(payload.get('topic') or 'General practice').strip()[:200]
        answers = payload.get('answers') if isinstance(payload.get('answers'), list) else []
        if not course_code or not answers:
            return jsonify({'success': False, 'error': 'A course and completed answers are required.'}), 400

        question_ids = list(dict.fromkeys(
            int(item['question_id'])
            for item in answers
            if isinstance(item, dict) and str(item.get('question_id', '')).isdigit()
        ))
        if not question_ids:
            return jsonify({'success': False, 'error': 'No valid question answers were received.'}), 400

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute(
                'SELECT id, course_code, topic, correct_option FROM questions WHERE id = ANY(%s)',
                (question_ids,),
            )
            questions_by_id = {row['id']: row for row in cursor.fetchall()}
            attempt_id = uuid.uuid4().hex
            saved = 0
            answered = 0
            correct_count = 0
            for item in answers:
                if not isinstance(item, dict) or not str(item.get('question_id', '')).isdigit():
                    continue
                question_id = int(item['question_id'])
                question = questions_by_id.get(question_id)
                if not question:
                    continue
                selected = str(item.get('answer') or '').strip()
                correct = str(question['correct_option'] or '').strip()
                try:
                    time_spent = max(0.0, min(float(item.get('time_spent_seconds')), 3600.0)) if item.get('time_spent_seconds') is not None else None
                except (TypeError, ValueError):
                    time_spent = None
                answered += 1
                if correct and selected.upper() == correct.upper():
                    correct_count += 1
                cursor.execute(
                    '''
                    INSERT INTO premium_question_attempts
                        (user_id, attempt_id, question_id, course_code, topic,
                         selected_option, correct_option, was_correct, time_spent_seconds)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, attempt_id, question_id) DO NOTHING
                    ''',
                    (session['user_id'], attempt_id, question_id,
                     str(question['course_code'] or course_code).strip()[:100],
                     question['topic'] or topic, selected, correct,
                     bool(correct and selected.upper() == correct.upper()), time_spent),
                )
                saved += cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({
                'success': True,
                'saved': saved,
                'correct': correct_count,
                'total': answered,
                'percentage': round((correct_count / answered) * 100) if answered else 0,
            })
        except Exception:
            flask_app.logger.exception('Premium AI attempt persistence failed')
            return jsonify({'success': False, 'error': 'Your score was calculated, but we could not save the review history. Please try again next session.'}), 500
    

    
    # ==================== WEAKNESS ANALYSIS ====================
    @flask_app.route('/premium/weakness-analysis')
    @premium_required
    def weakness_analysis():
        """Show course-level performance gaps from the user's completed tests."""
        user_id = session['user_id']
        analysis = []
        profile = _build_learning_profile([])
        selected_course = request.args.get('course', '').strip()
        selected_plan = None
        forgetting_curve = []
        load_error = False

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT
                    course_code,
                    AVG(CAST(score AS FLOAT) / NULLIF(total, 0) * 100) AS avg_score,
                    COUNT(*) AS total_attempts,
                    MIN(CAST(score AS FLOAT) / NULLIF(total, 0) * 100) AS lowest_score,
                    MAX(CAST(score AS FLOAT) / NULLIF(total, 0) * 100) AS highest_score,
                    MAX(created_at) AS last_attempt
                FROM scores
                WHERE user_id = %s
                  AND total > 0
                GROUP BY course_code
                ORDER BY avg_score ASC, last_attempt DESC
            ''', (user_id,))
            analysis = cursor.fetchall()
            cursor.execute('''
                SELECT course_code, topic, was_correct, confidence, miss_reason, answered_at
                FROM premium_question_attempts
                WHERE user_id = %s
                ORDER BY answered_at DESC
                LIMIT 500
            ''', (user_id,))
            attempt_rows = cursor.fetchall()
            profile = _build_learning_profile(attempt_rows)
            if selected_course:
                course_attempts = [row for row in attempt_rows if row['course_code'] == selected_course]
                if course_attempts:
                    selected_plan = _build_plan_from_attempts(course_attempts)
                    forgetting_curve = [
                        {'day': 'Day 1', 'retention': get_retention_percent(1)},
                        {'day': 'Day 7', 'retention': get_retention_percent(7)},
                        {'day': 'Day 14', 'retention': get_retention_percent(14)},
                        {'day': 'Day 30', 'retention': get_retention_percent(30)},
                    ]
            cursor.close()
            conn.close()
        except Exception:
            flask_app.logger.exception('Premium weakness analysis loading failed')
            load_error = True

        valid_scores = [float(row['avg_score']) for row in analysis if row['avg_score'] is not None]
        overall_average = sum(valid_scores) / len(valid_scores) if valid_scores else 0
        priority_count = sum(1 for score in valid_scores if score < 70)

        return render_template(
            'premium/weakness_analysis.html',
            analysis=analysis,
            overall_average=overall_average,
            priority_count=priority_count,
            load_error=load_error,
            profile=profile,
            selected_course=selected_course,
            selected_plan=selected_plan,
            forgetting_curve=forgetting_curve,
        )

    # ==================== QUESTION REVIEW AND LEARNING PROFILE ====================
    @flask_app.route('/premium/weakness-analysis/review/<course_code>')
    @premium_required
    def course_review(course_code):
        """Show the learner's latest answered questions for one course."""
        user_id = session['user_id']
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute(
                """
                SELECT DISTINCT ON (a.question_id)
                    a.id, a.question_id, a.course_code, a.topic,
                    a.selected_option, a.correct_option, a.was_correct,
                    a.confidence, a.miss_reason, a.answered_at,
                    q.question_text, q.option_a, q.option_b, q.option_c,
                    q.option_d, q.solution
                FROM premium_question_attempts a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE a.user_id = %s AND a.course_code = %s
                ORDER BY a.question_id, a.answered_at DESC, a.id DESC
                """,
                (user_id, course_code),
            )
            attempts = cursor.fetchall()
            cursor.close()
            conn.close()
            attempts = sorted(attempts, key=lambda row: row['answered_at'] or datetime.min, reverse=True)
        except Exception:
            flask_app.logger.exception('Premium course review loading failed')
            flash('We could not load this course review right now. Please try again.', 'error')
            return redirect(url_for('weakness_analysis'))

        if not attempts:
            flash('Complete a practice set in this course before opening a review.', 'info')
            return redirect(url_for('weakness_analysis'))

        return render_template(
            'premium/course_review.html',
            course_code=course_code,
            attempts=attempts,
            answered_count=len(attempts),
        )

    @flask_app.route('/premium/weakness-analysis/review/<course_code>/save', methods=['POST'])
    @premium_required
    def save_review_responses(course_code):
        """Persist confidence and miss-reason responses for reviewed questions."""
        user_id = session['user_id']
        confidence_values = {'very_confident', 'somewhat_confident', 'guessing'}
        miss_reason_values = {'forgot', 'guessed', 'ran_out_of_time', 'misunderstood_concept'}
        payload = request.get_json(silent=True) if request.is_json else None
        responses = payload.get('responses', []) if isinstance(payload, dict) else []

        if not responses:
            responses = []
            question_ids = request.form.getlist('question_id')
            for question_id in question_ids:
                responses.append({
                    'question_id': question_id,
                    'confidence': request.form.get(f'confidence_{question_id}'),
                    'miss_reason': request.form.get(f'miss_reason_{question_id}'),
                })

        clean_responses = []
        for response in responses:
            if not isinstance(response, dict) or not str(response.get('question_id', '')).isdigit():
                continue
            confidence = response.get('confidence') or None
            miss_reason = response.get('miss_reason') or None
            if confidence not in confidence_values:
                confidence = None
            if miss_reason not in miss_reason_values:
                miss_reason = None
            clean_responses.append((int(response['question_id']), confidence, miss_reason))

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            for question_id, confidence, miss_reason in clean_responses:
                cursor.execute(
                    """
                    UPDATE premium_question_attempts
                    SET confidence = CASE WHEN was_correct THEN %s ELSE confidence END,
                        miss_reason = CASE WHEN NOT was_correct THEN %s ELSE miss_reason END
                    WHERE id = %s AND user_id = %s AND course_code = %s
                    """,
                    (confidence, miss_reason, question_id, user_id, course_code),
                )
            cursor.execute(
                """
                SELECT topic, was_correct, confidence, miss_reason
                FROM premium_question_attempts
                WHERE user_id = %s AND course_code = %s
                ORDER BY answered_at DESC
                LIMIT 100
                """,
                (user_id, course_code),
            )
            attempts = cursor.fetchall()
            conn.commit()
            cursor.close()
            conn.close()
        except Exception:
            flask_app.logger.exception('Premium course review save failed')
            if request.is_json:
                return jsonify({'success': False, 'error': 'We could not save your review yet. Please try again.'}), 500
            flash('We could not save your review yet. Please try again.', 'error')
            return redirect(url_for('course_review', course_code=course_code))

        plan = _build_plan_from_attempts(attempts)
        if request.is_json:
            return jsonify({'success': True, 'study_plan': plan['days'], 'forgetting_curve': [
                {'day': day, 'retention': retention}
                for day, retention in ((1, 90), (7, 80), (14, 65), (30, 35))
            ], 'redirect_url': url_for('course_study_plan', course_code=course_code)})

        flash('Your review responses were saved. Your personalised seven-day plan is ready.', 'success')
        return redirect(url_for('course_study_plan', course_code=course_code))

    @flask_app.route('/premium/weakness-analysis/study-plan/<course_code>')
    @premium_required
    def course_study_plan(course_code):
        """Render a seven-day plan and transparent forgetting-curve anchors."""
        user_id = session['user_id']
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute(
                """
                SELECT topic, was_correct, confidence, miss_reason, answered_at
                FROM premium_question_attempts
                WHERE user_id = %s AND course_code = %s
                ORDER BY answered_at DESC
                LIMIT 100
                """,
                (user_id, course_code),
            )
            attempts = cursor.fetchall()
            cursor.close()
            conn.close()
        except Exception:
            flask_app.logger.exception('Premium study plan loading failed')
            flash('We could not prepare your study plan right now. Please try again.', 'error')
            return redirect(url_for('weakness_analysis'))

        if not attempts:
            flash('Review at least one answered question before generating a study plan.', 'info')
            return redirect(url_for('course_review', course_code=course_code))

        plan = _build_plan_from_attempts(attempts)
        last_studied = max(row['answered_at'] for row in attempts if row['answered_at'])
        days_since = max(0, (datetime.utcnow() - last_studied).days)
        curve = [
            {'day': 'Day 1', 'retention': get_retention_percent(1)},
            {'day': 'Day 7', 'retention': get_retention_percent(7)},
            {'day': 'Day 14', 'retention': get_retention_percent(14)},
            {'day': 'Day 30', 'retention': get_retention_percent(30)},
        ]
        return render_template(
            'premium/study_plan.html',
            course_code=course_code,
            plan=plan,
            forgetting_curve=curve,
            days_since=days_since,
        )

    @flask_app.route('/premium/learning-profile')
    @premium_required
    def learning_profile():
        """Show cross-course strengths, weaknesses, confidence, and study advice."""
        user_id = session['user_id']
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute(
                """
                SELECT course_code, topic, was_correct, confidence, miss_reason, answered_at
                FROM premium_question_attempts
                WHERE user_id = %s
                ORDER BY answered_at DESC
                LIMIT 500
                """,
                (user_id,),
            )
            attempts = cursor.fetchall()
            cursor.close()
            conn.close()
        except Exception:
            flask_app.logger.exception('Premium learning profile loading failed')
            flash('We could not load your learning profile right now. Please try again.', 'error')
            return redirect(url_for('premium_dashboard'))

        profile = _build_learning_profile(attempts)
        return render_template('premium/learning_profile.html', profile=profile, total_attempts=len(attempts))

    # ==================== ADAPTIVE PRACTICE ====================
    @flask_app.route('/premium/adaptive-practice')
    @premium_required
    def adaptive_practice():
        """Configure a weakness-aware adaptive practice session."""
        conn = None
        try:
            attempts, profile_row, _ = _load_campusmate_data(session['user_id'])
            profile = _campusmate_context(profile_row)
            courses = sorted({
                str(_row_value(row, 'course_code') or '').strip().upper()
                for row in attempts
                if _row_value(row, 'course_code')
            } | {str(course).strip().upper() for course in profile.get('courses', []) if str(course).strip()})
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT topic
                FROM questions
                WHERE topic IS NOT NULL AND BTRIM(topic) <> ''
                ORDER BY topic
                LIMIT 300
            ''')
            topics = [str(row[0]).strip() for row in cursor.fetchall() if row[0]]
            cursor.close()
            return render_template(
                'premium/adaptive_practice.html',
                courses=courses,
                topics=topics,
                profile=profile,
                total_attempts=len(attempts),
            )
        except Exception:
            flask_app.logger.exception('Adaptive practice workspace loading failed')
            flash('We could not load Adaptive Practice right now. Please try again in a moment.', 'error')
            return redirect(url_for('premium_dashboard'))
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/adaptive-practice/questions')
    @premium_required
    def adaptive_practice_questions():
        """Return a bounded, answer-key-free adaptive question set."""
        courses = _normalise_course_list(request.args.get('courses') or request.args.getlist('course'))
        topic = str(request.args.get('topic') or '').strip()[:200]
        limit = _bounded_int(request.args.get('limit'), 10, 5, 30)
        conn = None
        try:
            conn = get_db_connection()
            rows = _adaptive_question_rows(conn, session['user_id'], courses=courses, topic=topic, limit=limit)
            if not rows:
                return jsonify({'success': False, 'error': 'There are no matching questions available yet. Try another course or topic.'}), 404
            questions = []
            for row in rows:
                item = _mock_question_payload(row)
                item['adaptive_signal'] = 'priority review' if int(_row_value(row, 'learner_misses') or 0) else ('new topic' if not int(_row_value(row, 'learner_attempts') or 0) else 'spaced review')
                questions.append(item)
            return jsonify({'success': True, 'questions': questions, 'count': len(questions), 'record_url': url_for('record_ai_attempts')})
        except Exception:
            flask_app.logger.exception('Adaptive practice question selection failed')
            return jsonify({'success': False, 'error': 'We could not prepare adaptive questions right now. Please try again.'}), 500
        finally:
            if conn:
                conn.close()
    
    # ==================== MISTAKE NOTEBOOK ====================
    @flask_app.route('/premium/mistakes')
    @premium_required
    def mistake_notebook():
        """Review unique question-level mistakes with explainable ranking and filters."""
        user_id = session['user_id']
        conn = None
        try:
            filter_course = (request.args.get('course') or '').strip()
            filter_reason = (request.args.get('miss_reason') or '').strip()
            filter_tag = (request.args.get('tag') or '').strip()
            sort_by = (request.args.get('sort') or 'importance').strip().lower()
            if sort_by not in {'importance', 'recent', 'misses'}:
                sort_by = 'importance'

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT a.id, a.question_id, a.course_code, a.topic,
                       a.selected_option, a.correct_option, a.confidence,
                       a.miss_reason, a.answered_at,
                       q.question_text, q.option_a, q.option_b, q.option_c, q.option_d,
                       q.solution, q.content_json
                FROM premium_question_attempts a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE a.user_id = %s AND a.was_correct = FALSE
                ORDER BY a.answered_at DESC NULLS LAST, a.id DESC
            ''', (user_id,))
            rows = cursor.fetchall()
            cursor.close()

            now = datetime.utcnow()
            grouped = {}
            all_courses = set()
            all_reasons = set()
            for row in rows:
                question_id = _row_value(row, 'question_id')
                if question_id is None:
                    continue
                course = str(_row_value(row, 'course_code', 'General')).strip() or 'General'
                topic = str(_row_value(row, 'topic', 'Core concepts')).strip() or 'Core concepts'
                reason = str(_row_value(row, 'miss_reason', '')).strip()
                all_courses.add(course)
                if reason:
                    all_reasons.add(reason)
                item = grouped.setdefault(question_id, {
                    'question_id': question_id,
                    'question_text': _row_value(row, 'question_text') or 'Previously missed question',
                    'course': course,
                    'topic': topic,
                    'correct_option': str(_row_value(row, 'correct_option', '') or '').upper(),
                    'selected_option': str(_row_value(row, 'selected_option', '') or '').upper(),
                    'selected_answer': None,
                    'correct_answer': None,
                    'options': {
                        'A': _row_value(row, 'option_a'),
                        'B': _row_value(row, 'option_b'),
                        'C': _row_value(row, 'option_c'),
                        'D': _row_value(row, 'option_d'),
                    },
                    'solution': _row_value(row, 'solution') or '',
                    'content_json': _safe_json(_row_value(row, 'content_json'), None),
                    'misses': 0,
                    'miss_reasons': set(),
                    'last_missed': None,
                    'reviewed': False,
                })
                item['misses'] += 1
                if reason:
                    item['miss_reasons'].add(reason)
                answered_at = _normalise_datetime(_row_value(row, 'answered_at'))
                if item['last_missed'] is None and answered_at is not None:
                    item['last_missed'] = answered_at
                    item['selected_option'] = str(_row_value(row, 'selected_option', '') or '').upper()
                    item['correct_option'] = str(_row_value(row, 'correct_option', '') or '').upper()
                    item['reviewed'] = _row_value(row, 'confidence') == 'very_confident'
                elif answered_at and item['last_missed'] and answered_at > item['last_missed']:
                    item['last_missed'] = answered_at
                    item['selected_option'] = str(_row_value(row, 'selected_option', '') or '').upper()
                    item['correct_option'] = str(_row_value(row, 'correct_option', '') or '').upper()
                    item['reviewed'] = _row_value(row, 'confidence') == 'very_confident'

            notes_by_question = {}
            if grouped:
                notes_cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                notes_cursor.execute('''
                    SELECT question_id, summary, remember_text, tags, provider,
                           generated_at, next_review_at, last_reviewed_at,
                           review_interval_days, review_count, last_review_result
                    FROM premium_mistake_notes
                    WHERE user_id = %s AND question_id = ANY(%s)
                ''', (user_id, list(grouped.keys())))
                notes_by_question = {row['question_id']: dict(row) for row in notes_cursor.fetchall()}
                notes_cursor.close()

            all_mistakes = []
            mistakes = []
            for item in grouped.values():
                days_since = max(0, (now - item['last_missed']).days) if item['last_missed'] else 0
                retention = round(get_retention_percent(days_since))
                item['retention'] = retention
                item['days_since'] = days_since
                item['importance'] = round(min(100, 45 + item['misses'] * 12 + max(0, 50 - retention) * 0.5))
                item['last_missed_iso'] = item['last_missed'].isoformat() if item['last_missed'] else ''
                item['last_missed_label'] = item['last_missed'].strftime('%b %d, %Y') if item['last_missed'] else 'Date unavailable'
                item['miss_reasons'] = sorted(item['miss_reasons'])
                item['selected_answer'] = item['options'].get(item['selected_option']) or item['selected_option'] or 'Not recorded'
                item['correct_answer'] = item['options'].get(item['correct_option']) or item['correct_option'] or 'Not recorded'
                note = notes_by_question.get(item['question_id']) or {}
                note_tags = _safe_json(note.get('tags'), [])
                item['ai_note'] = {
                    'summary': note.get('summary') or '',
                    'remember': note.get('remember_text') or '',
                    'tags': _normalise_mistake_tags(note_tags, item['course'], item['topic'], item['miss_reasons']),
                    'generated': bool(note.get('generated_at')),
                    'provider': note.get('provider') or '',
                }
                item['review_schedule'] = {
                    'next_review_iso': note.get('next_review_at').isoformat() if note.get('next_review_at') else '',
                    'next_review_label': note.get('next_review_at').strftime('%b %d, %Y') if note.get('next_review_at') else 'Not scheduled',
                    'due': bool(note.get('next_review_at') and _normalise_datetime(note.get('next_review_at')) <= now),
                    'interval_days': int(note.get('review_interval_days') or 1),
                    'review_count': int(note.get('review_count') or 0),
                    'last_result': note.get('last_review_result') or '',
                }
                item.pop('last_missed', None)
                all_mistakes.append(dict(item))
                if filter_course and item['course'].lower() != filter_course.lower():
                    continue
                if filter_reason and filter_reason not in item['miss_reasons']:
                    continue
                if filter_tag and filter_tag.lower() not in {tag.lower() for tag in item.get('ai_note', {}).get('tags', [])}:
                    continue
                mistakes.append(item)

            if sort_by == 'recent':
                mistakes.sort(key=lambda item: item['last_missed_iso'], reverse=True)
            elif sort_by == 'misses':
                mistakes.sort(key=lambda item: (-item['misses'], -item['importance']))
            else:
                mistakes.sort(key=lambda item: (-item['importance'], -item['misses'], item['last_missed_iso']), reverse=False)

            reviewed_count = sum(1 for item in mistakes if item['reviewed'])
            critical_topics = len({item['topic'] for item in mistakes if item['importance'] >= 75})
            return render_template(
                'premium/mistakes.html',
                mistakes=mistakes,
                courses=sorted(all_courses),
                miss_reasons=sorted(all_reasons),
                total_mistakes=len(mistakes),
                total_unique_mistakes=len(grouped),
                reviewed_count=reviewed_count,
                critical_topics=critical_topics,
                due_reviews=sum(1 for item in all_mistakes if item.get('review_schedule', {}).get('due')),
                heatmap=_build_mistake_heatmap(all_mistakes),
                all_tags=sorted({tag for item in all_mistakes for tag in item.get('ai_note', {}).get('tags', [])}),
                filter_course=filter_course,
                filter_reason=filter_reason,
                filter_tag=filter_tag,
                sort_by=sort_by,
            )
        except Exception:
            flask_app.logger.exception('Premium mistake notebook loading failed')
            flash('We could not load your Mistake Notebook right now. Please try again.', 'error')
            return redirect(url_for('premium_dashboard'))
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mistakes/<int:question_id>/dismiss', methods=['POST'])
    @premium_required
    def dismiss_mistake(question_id):
        """Mark the latest recorded miss as reviewed without changing the question history."""
        user_id = session['user_id']
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE premium_question_attempts
                SET confidence = 'very_confident'
                WHERE id = (
                    SELECT id
                    FROM premium_question_attempts
                    WHERE user_id = %s AND question_id = %s AND was_correct = FALSE
                    ORDER BY answered_at DESC NULLS LAST, id DESC
                    LIMIT 1
                )
            ''', (user_id, question_id))
            updated = cursor.rowcount
            conn.commit()
            cursor.close()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'ok': bool(updated), 'reviewed': bool(updated)})
            flash('Mistake marked as reviewed.', 'success')
            return redirect(url_for('mistake_notebook'))
        except Exception:
            if conn:
                conn.rollback()
            flask_app.logger.exception('Premium mistake review update failed')
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'ok': False, 'message': 'We could not update this mistake right now.'}), 500
            flash('We could not update this mistake right now. Please try again.', 'error')
            return redirect(url_for('mistake_notebook'))
        finally:
            if conn:
                conn.close()
    
    @flask_app.route('/premium/mistakes/<int:question_id>/note', methods=['POST'])
    @premium_required
    def generate_mistake_note(question_id):
        """Generate and cache one concise AI learning note for a missed question."""
        user_id = session['user_id']
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT a.course_code, a.topic, a.selected_option, a.correct_option,
                       a.miss_reason, q.question_text, q.option_a, q.option_b,
                       q.option_c, q.option_d, q.solution
                FROM premium_question_attempts a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE a.user_id = %s AND a.question_id = %s AND a.was_correct = FALSE
                ORDER BY a.answered_at DESC NULLS LAST, a.id DESC
                LIMIT 1
            ''', (user_id, question_id))
            row = cursor.fetchone()
            if not row:
                return jsonify({'ok': False, 'message': 'This question is not in your Mistake Notebook.'}), 404
            cursor.execute('''
                SELECT summary, remember_text, tags, provider, generated_at
                FROM premium_mistake_notes
                WHERE user_id = %s AND question_id = %s
            ''', (user_id, question_id))
            existing = cursor.fetchone()
            if existing and existing['summary'] and existing['remember_text']:
                return jsonify({'ok': True, 'note': {
                    'summary': existing['summary'],
                    'remember': existing['remember_text'],
                    'tags': _normalise_mistake_tags(_safe_json(existing['tags'], []), row['course_code'], row['topic']),
                    'generated': True,
                }})

            options = {
                'A': row['option_a'], 'B': row['option_b'],
                'C': row['option_c'], 'D': row['option_d'],
            }
            item = {
                'course': row['course_code'],
                'topic': row['topic'],
                'question_text': row['question_text'],
                'selected_option': row['selected_option'],
                'correct_option': row['correct_option'],
                'selected_answer': options.get(str(row['selected_option'] or '').upper()) or row['selected_option'],
                'correct_answer': options.get(str(row['correct_option'] or '').upper()) or row['correct_option'],
                'solution': row['solution'] or '',
                'miss_reasons': [row['miss_reason']] if row['miss_reason'] else [],
            }
            note = _fallback_mistake_note(item)
            provider = None
            try:
                text, provider = generate_text(
                    system_prompt='You are CampusMate. Return only valid JSON with summary, remember, and tags.',
                    user_message=_mistake_note_prompt(item),
                    temperature=0.2,
                    max_tokens=350,
                )
                parsed = _safe_json(_strip_json_wrappers(text), {})
                if isinstance(parsed, dict):
                    note['summary'] = str(parsed.get('summary') or note['summary']).strip()[:500]
                    note['remember'] = str(parsed.get('remember') or note['remember']).strip()[:700]
                    note['tags'] = _normalise_mistake_tags(parsed.get('tags'), item['course'], item['topic'], item['miss_reasons'])
            except (AIProviderError, Exception) as exc:
                flask_app.logger.warning('Mistake note AI generation fell back: %s', _friendly_ai_error(exc, 'mistake note'))

            now = datetime.utcnow()
            tags_json = json.dumps(note['tags'])
            cursor.execute('''
                INSERT INTO premium_mistake_notes
                    (user_id, question_id, summary, remember_text, tags, provider,
                     generated_at, next_review_at, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (user_id, question_id) DO UPDATE SET
                    summary = EXCLUDED.summary,
                    remember_text = EXCLUDED.remember_text,
                    tags = EXCLUDED.tags,
                    provider = EXCLUDED.provider,
                    generated_at = EXCLUDED.generated_at,
                    next_review_at = COALESCE(premium_mistake_notes.next_review_at, EXCLUDED.next_review_at),
                    updated_at = EXCLUDED.updated_at
            ''', (user_id, question_id, note['summary'], note['remember'], tags_json, provider or 'fallback', now, now + timedelta(days=1), now))
            conn.commit()
            return jsonify({'ok': True, 'note': {**note, 'generated': bool(provider), 'provider': provider or 'fallback'}})
        except Exception:
            if conn:
                conn.rollback()
            flask_app.logger.exception('Mistake note generation failed')
            return jsonify({'ok': False, 'message': 'We could not prepare this learning note right now. Please try again.'}), 500
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mistakes/<int:question_id>/review', methods=['POST'])
    @premium_required
    def review_mistake(question_id):
        """Record a learner review and schedule the next spaced repetition checkpoint."""
        user_id = session['user_id']
        payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
        rating = str(payload.get('rating') or '').strip().lower()
        if rating not in {'forgot', 'hard', 'got_it'}:
            return jsonify({'ok': False, 'message': 'Choose a valid review result.'}), 400
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT review_interval_days, review_count
                FROM premium_mistake_notes
                WHERE user_id = %s AND question_id = %s
            ''', (user_id, question_id))
            prior = cursor.fetchone() or {}
            interval = _review_interval_for_rating(prior.get('review_interval_days', 1), rating)
            now = datetime.utcnow()
            next_review = now + timedelta(days=interval)
            cursor.execute('''
                INSERT INTO premium_mistake_notes
                    (user_id, question_id, next_review_at, last_reviewed_at,
                     review_interval_days, review_count, last_review_result, updated_at)
                VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                ON CONFLICT (user_id, question_id) DO UPDATE SET
                    next_review_at = EXCLUDED.next_review_at,
                    last_reviewed_at = EXCLUDED.last_reviewed_at,
                    review_interval_days = EXCLUDED.review_interval_days,
                    review_count = premium_mistake_notes.review_count + 1,
                    last_review_result = EXCLUDED.last_review_result,
                    updated_at = EXCLUDED.updated_at
            ''', (user_id, question_id, next_review, now, interval, rating, now))
            conn.commit()
            return jsonify({'ok': True, 'next_review': next_review.isoformat(), 'interval_days': interval, 'rating': rating})
        except Exception:
            if conn:
                conn.rollback()
            flask_app.logger.exception('Mistake review scheduling failed')
            return jsonify({'ok': False, 'message': 'We could not save this review right now.'}), 500
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mistakes/retest')
    @premium_required
    def mistake_retest():
        return render_template('premium/mistake_retest.html')

    @flask_app.route('/premium/mistakes/retest/questions')
    @premium_required
    def mistake_retest_questions():
        """Return a bounded retest set from the learner's own missed questions."""
        user_id = session['user_id']
        mode = str(request.args.get('mode') or 'random').strip().lower()
        course = str(request.args.get('course') or '').strip()
        if mode not in {'random', 'today', 'week', 'course'}:
            mode = 'random'
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            clauses = ['a.user_id = %s', 'a.was_correct = FALSE']
            params = [user_id]
            if mode == 'today':
                clauses.append("a.answered_at >= CURRENT_DATE")
            elif mode == 'week':
                clauses.append("a.answered_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'")
            elif mode == 'course' and course:
                clauses.append('a.course_code = %s')
                params.append(course)
            cursor.execute(f'''
                SELECT a.question_id, a.course_code, a.topic, a.selected_option,
                       a.correct_option, a.answered_at, q.question_text, q.option_a,
                       q.option_b, q.option_c, q.option_d, q.solution, q.content_json,
                       COUNT(*) OVER (PARTITION BY a.question_id) AS misses
                FROM premium_question_attempts a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE {' AND '.join(clauses)}
                ORDER BY a.answered_at DESC NULLS LAST, a.id DESC
            ''', tuple(params))
            grouped = {}
            for row in cursor.fetchall():
                if row['question_id'] in grouped:
                    continue
                grouped[row['question_id']] = {
                    'question_id': row['question_id'], 'course': row['course_code'], 'topic': row['topic'],
                    'question_text': row['question_text'],
                    'options': {'A': row['option_a'], 'B': row['option_b'], 'C': row['option_c'], 'D': row['option_d']},
                    'correct_option': row['correct_option'], 'solution': row['solution'] or '',
                    'content_json': _safe_json(row['content_json'], None), 'misses': int(row['misses'] or 1),
                }
            questions = list(grouped.values())
            if mode == 'random':
                import random
                random.shuffle(questions)
            questions = questions[:10]
            cursor.close()
            return jsonify({'ok': True, 'mode': mode, 'course': course, 'questions': questions, 'total': len(questions)})
        except Exception:
            flask_app.logger.exception('Mistake retest loading failed')
            return jsonify({'ok': False, 'message': 'We could not prepare your retest right now.'}), 500
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mistakes/retest/answer', methods=['POST'])
    @premium_required
    def submit_mistake_retest_answer():
        payload = request.get_json(silent=True) or {}
        question_id = payload.get('question_id')
        selected = str(payload.get('selected_option') or '').upper()
        if not question_id or selected not in {'A', 'B', 'C', 'D'}:
            return jsonify({'ok': False, 'message': 'The retest answer was incomplete.'}), 400

        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT correct_option
                FROM premium_question_attempts
                WHERE user_id = %s AND question_id = %s AND was_correct = FALSE
                ORDER BY answered_at DESC NULLS LAST, id DESC
                LIMIT 1
            ''', (session['user_id'], int(question_id)))
            row = cursor.fetchone()
            cursor.close()
            if not row:
                return jsonify({'ok': False, 'message': 'This question is not available in your Mistake Notebook.'}), 404
            correct = str(row['correct_option'] or '').upper()
            if correct not in {'A', 'B', 'C', 'D'}:
                return jsonify({'ok': False, 'message': 'This question has incomplete answer data.'}), 409
            rating = 'got_it' if selected == correct else 'forgot'
            return _record_retest_review(session['user_id'], int(question_id), rating)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'message': 'The retest question identifier is invalid.'}), 400
        except Exception:
            flask_app.logger.exception('Mistake retest answer verification failed')
            return jsonify({'ok': False, 'message': 'We could not save this retest answer right now.'}), 500
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/notifications')
    @premium_required
    def premium_notification_center():
        notifications, unread_count = _load_user_notifications(session['user_id'], limit=50)
        return render_template('notifications.html', notifications=notifications, unread_count=unread_count)

    # ==================== MOCK EXAMS ====================
    @flask_app.route('/premium/mock-exams')
    @premium_required
    def mock_exams():
        """Show mock-exam configuration and the learner's resumable exam, if any."""
        conn = None
        try:
            attempts, profile_row, _ = _load_campusmate_data(session['user_id'])
            profile = _campusmate_context(profile_row)
            courses = sorted({
                str(_row_value(row, 'course_code') or '').strip().upper()
                for row in attempts
                if _row_value(row, 'course_code')
            } | {str(course).strip().upper() for course in profile.get('courses', []) if str(course).strip()})
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT id, title, course_codes, question_ids, answers, duration_seconds,
                       started_at, submitted_at, status, score, total
                FROM premium_mock_exam_sessions
                WHERE user_id = %s AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT 1
            ''', (session['user_id'],))
            active_row = cursor.fetchone()
            cursor.close()
            active_exam = _mock_session_payload(active_row) if active_row else None
            if active_exam and active_exam.get('started_at'):
                active_exam['started_at_iso'] = active_exam['started_at'].isoformat()
            return render_template(
                'premium/mock_exams.html',
                courses=courses,
                profile=profile,
                active_exam=active_exam,
                total_attempts=len(attempts),
            )
        except Exception:
            flask_app.logger.exception('Mock exam workspace loading failed')
            flash('We could not load Mock Exams right now. Please try again in a moment.', 'error')
            return redirect(url_for('premium_dashboard'))
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mock-exams/start', methods=['POST'])
    @premium_required
    def start_mock_exam():
        """Create a durable mock exam with a server-controlled question set."""
        payload = request.get_json(silent=True) or request.form.to_dict(flat=True)
        courses = _normalise_course_list(payload.get('courses') or payload.get('course'))
        if not courses:
            try:
                _, profile_row, _ = _load_campusmate_data(session['user_id'])
                courses = _normalise_course_list(_campusmate_context(profile_row).get('courses', []))
            except Exception:
                courses = []
        if not courses:
            return jsonify({'success': False, 'error': 'Choose at least one course before starting the mock exam.'}), 400
        question_count = _bounded_int(payload.get('question_count'), 30, 10, 100)
        duration_minutes = _bounded_int(payload.get('duration_minutes'), max(20, question_count * 2), 10, 180)
        title = str(payload.get('title') or 'Premium Mock Exam').strip()[:120] or 'Premium Mock Exam'
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT id, course_code, topic, question_text, option_a, option_b,
                       option_c, option_d, correct_option, solution, content_json
                FROM questions
                WHERE course_code = ANY(%s)
                ORDER BY RANDOM()
                LIMIT %s
            ''', (courses, question_count))
            rows = cursor.fetchall()
            if len(rows) < 10:
                cursor.close()
                return jsonify({'success': False, 'error': 'There are not enough questions for that exam selection. Choose more courses or a smaller exam.'}), 400
            question_ids = [int(row['id']) for row in rows]
            exam_id = uuid.uuid4().hex
            cursor.execute('''
                INSERT INTO premium_mock_exam_sessions
                    (id, user_id, title, course_codes, question_ids, answers, duration_seconds, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
            ''', (
                exam_id, session['user_id'], title,
                psycopg2.extras.Json(courses), psycopg2.extras.Json(question_ids),
                psycopg2.extras.Json({}), duration_minutes * 60,
            ))
            conn.commit()
            cursor.close()
            return jsonify({'success': True, 'session_id': exam_id, 'redirect_url': url_for('mock_exam_session', session_id=exam_id)})
        except Exception:
            if conn:
                conn.rollback()
            flask_app.logger.exception('Mock exam creation failed')
            return jsonify({'success': False, 'error': 'We could not create your mock exam right now. Please try again.'}), 500
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mock-exams/<session_id>')
    @premium_required
    def mock_exam_session(session_id):
        """Render an active exam or its immutable server-scored result."""
        if not re.fullmatch(r'[0-9a-f]{32}', str(session_id or '').lower()):
            flash('That mock exam link is invalid.', 'error')
            return redirect(url_for('mock_exams'))
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT id, title, course_codes, question_ids, answers, duration_seconds,
                       started_at, submitted_at, status, score, total
                FROM premium_mock_exam_sessions
                WHERE id = %s AND user_id = %s
            ''', (session_id, session['user_id']))
            session_row = cursor.fetchone()
            if not session_row:
                cursor.close()
                flash('That mock exam could not be found.', 'error')
                return redirect(url_for('mock_exams'))
            exam = _mock_session_payload(session_row)
            question_ids = exam['question_ids']
            cursor.execute('''
                SELECT id, course_code, topic, question_text, option_a, option_b,
                       option_c, option_d, correct_option, solution, content_json
                FROM questions
                WHERE id = ANY(%s)
            ''', (question_ids,))
            rows_by_id = {int(row['id']): row for row in cursor.fetchall()}
            cursor.close()
            ordered_rows = [rows_by_id[qid] for qid in question_ids if qid in rows_by_id]
            if exam['status'] == 'submitted':
                result_items = []
                for row in ordered_rows:
                    selected = str(exam['answers'].get(str(row['id']), '') or '').upper()
                    correct = str(row['correct_option'] or '').upper()
                    result_items.append({
                        **_question_from_row(row, source='premium_mock_exam_result'),
                        'selected_option': selected,
                        'was_correct': bool(selected and selected == correct),
                    })
                return render_template('premium/mock_exams.html', mode='result', exam=exam, result_items=result_items, courses=[])
            questions = [_mock_question_payload(row) for row in ordered_rows]
            deadline = None
            if exam.get('started_at'):
                deadline = (exam['started_at'] + timedelta(seconds=exam['duration_seconds'])).isoformat()
            return render_template(
                'premium/mock_exams.html',
                mode='exam', exam=exam, questions=questions,
                deadline_iso=deadline, courses=exam.get('course_codes', []),
            )
        except Exception:
            flask_app.logger.exception('Mock exam session loading failed')
            flash('We could not load that mock exam right now. Please try again.', 'error')
            return redirect(url_for('mock_exams'))
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mock-exams/<session_id>/answer', methods=['POST'])
    @premium_required
    def save_mock_exam_answer(session_id):
        """Persist one answer without revealing correctness before final submission."""
        if not re.fullmatch(r'[0-9a-f]{32}', str(session_id or '').lower()):
            return jsonify({'success': False, 'error': 'Invalid exam session.'}), 400
        payload = request.get_json(silent=True) or {}
        question_id = payload.get('question_id')
        selected = str(payload.get('answer') or '').strip().upper()
        if not str(question_id).isdigit() or selected not in {'A', 'B', 'C', 'D'}:
            return jsonify({'success': False, 'error': 'Choose one valid answer.'}), 400
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT question_ids, answers, status, started_at, duration_seconds
                FROM premium_mock_exam_sessions
                WHERE id = %s AND user_id = %s
                FOR UPDATE
            ''', (session_id, session['user_id']))
            row = cursor.fetchone()
            if not row or row['status'] != 'active':
                cursor.close()
                return jsonify({'success': False, 'error': 'This exam is no longer active.'}), 409
            started_at = row['started_at']
            duration_seconds = int(row['duration_seconds'] or 0)
            if started_at and duration_seconds > 0 and datetime.utcnow() >= started_at + timedelta(seconds=duration_seconds):
                cursor.close()
                return jsonify({'success': False, 'error': 'Time has expired. Submit the exam to receive your server-scored result.'}), 409
            question_ids = [int(value) for value in (_safe_json(row['question_ids'], []) or []) if str(value).isdigit()]
            if int(question_id) not in question_ids:
                cursor.close()
                return jsonify({'success': False, 'error': 'That question does not belong to this exam.'}), 403
            answers = _safe_json(row['answers'], {}) or {}
            answers[str(int(question_id))] = selected
            cursor.execute('''
                UPDATE premium_mock_exam_sessions
                SET answers = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s
            ''', (psycopg2.extras.Json(answers), session_id, session['user_id']))
            conn.commit()
            cursor.close()
            return jsonify({'success': True, 'answered': len(answers), 'total': len(question_ids)})
        except Exception:
            if conn:
                conn.rollback()
            flask_app.logger.exception('Mock exam answer save failed')
            return jsonify({'success': False, 'error': 'Your answer could not be saved. Please try again.'}), 500
        finally:
            if conn:
                conn.close()

    @flask_app.route('/premium/mock-exams/<session_id>/submit', methods=['POST'])
    @premium_required
    def submit_mock_exam(session_id):
        """Score a mock exam on the server and record its attempts exactly once."""
        if not re.fullmatch(r'[0-9a-f]{32}', str(session_id or '').lower()):
            return jsonify({'success': False, 'error': 'Invalid exam session.'}), 400
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute('''
                SELECT id, question_ids, answers, status, score, total,
                       started_at, duration_seconds
                FROM premium_mock_exam_sessions
                WHERE id = %s AND user_id = %s
                FOR UPDATE
            ''', (session_id, session['user_id']))
            exam = cursor.fetchone()
            if not exam:
                cursor.close()
                return jsonify({'success': False, 'error': 'That mock exam could not be found.'}), 404
            if exam['status'] == 'submitted':
                cursor.close()
                return jsonify({'success': True, 'already_submitted': True, 'score': exam['score'], 'total': exam['total'], 'redirect_url': url_for('mock_exam_session', session_id=session_id)})
            # Expiry is handled by accepting the final submission and scoring the saved answers.
            # The browser timer is only a convenience; the server remains authoritative.
            question_ids = [int(value) for value in (_safe_json(exam['question_ids'], []) or []) if str(value).isdigit()]
            answers = _safe_json(exam['answers'], {}) or {}
            cursor.execute('''
                SELECT id, course_code, topic, correct_option
                FROM questions
                WHERE id = ANY(%s)
            ''', (question_ids,))
            question_rows = cursor.fetchall()
            row_by_id = {int(row['id']): row for row in question_rows}
            score = 0
            for question_id in question_ids:
                row = row_by_id.get(question_id)
                if row and str(answers.get(str(question_id), '')).upper() == str(row['correct_option'] or '').upper():
                    score += 1
            total = len(question_rows)
            cursor.execute('''
                UPDATE premium_mock_exam_sessions
                SET status = 'submitted', score = %s, total = %s,
                    submitted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s
            ''', (score, total, session_id, session['user_id']))
            for row in question_rows:
                selected = str(answers.get(str(row['id']), '') or '').upper()
                correct = str(row['correct_option'] or '').upper()
                cursor.execute('''
                    INSERT INTO premium_question_attempts
                        (user_id, attempt_id, question_id, course_code, topic,
                         selected_option, correct_option, was_correct, answered_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ''', (
                    session['user_id'], session_id, row['id'], row['course_code'], row['topic'],
                    selected or None, correct, bool(selected and selected == correct),
                ))
            conn.commit()
            cursor.close()
            refresh_campusmate_plan_after_activity(session['user_id'])
            return jsonify({'success': True, 'score': score, 'total': total, 'percentage': round((score / total) * 100) if total else 0, 'redirect_url': url_for('mock_exam_session', session_id=session_id)})
        except Exception:
            if conn:
                conn.rollback()
            flask_app.logger.exception('Mock exam submission failed')
            return jsonify({'success': False, 'error': 'We could not submit your mock exam. Your answers have not been finalized.'}), 500
        finally:
            if conn:
                conn.close()
    
    # ==================== FLASHCARDS ====================
    @flask_app.route('/premium/flashcards')
    @premium_required
    def flashcards():
        """Smart flashcards with spaced repetition"""
        return render_template('premium/flashcards.html')
    
    # ==================== PERFORMANCE DASHBOARD ====================
    @flask_app.route('/premium/performance')
    @premium_required
    def performance_dashboard():
        """Production-level academic performance and learning analytics."""
        user_id = session['user_id']
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute(
                '''
                SELECT a.id, a.attempt_id, a.question_id, a.course_code, a.topic,
                       a.selected_option, a.correct_option, a.was_correct,
                       a.confidence, a.miss_reason, a.time_spent_seconds,
                       a.answered_at, q.question_text, q.option_a, q.option_b,
                       q.option_c, q.option_d
                FROM premium_question_attempts a
                LEFT JOIN questions q ON q.id = a.question_id
                WHERE a.user_id = %s
                ORDER BY a.answered_at DESC, a.id DESC
                LIMIT 2000
                ''',
                (user_id,),
            )
            attempts = cursor.fetchall()
            cursor.execute(
                '''
                SELECT score, total, course_code, created_at
                FROM scores
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 100
                ''',
                (user_id,),
            )
            score_rows = cursor.fetchall()
            cursor.close()
            insights = _build_performance_insights(attempts, score_rows)
            distractors = _build_distractor_analysis(attempts)
            return render_template(
                'premium/performance.html',
                insights=insights,
                distractors=distractors,
            )
        except Exception:
            flask_app.logger.exception('Premium performance insights loading failed')
            flash('We could not load your performance insights right now. Please try again.', 'error')
            return redirect(url_for('premium_dashboard'))
        finally:
            if conn:
                conn.close()

    # ==================== GPA PREDICTOR ====================
    @flask_app.route('/premium/gpa-predictor')
    @premium_required
    def gpa_predictor():
        """AI-powered GPA prediction"""
        user_id = session['user_id']
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            # Get user's average score for prediction
            cursor.execute('''
                SELECT AVG(CAST(score AS FLOAT) / total * 100) as avg_score
                FROM scores WHERE user_id = %s
            ''', (user_id,))
            result = cursor.fetchone()
            avg_score = result['avg_score'] if result['avg_score'] else 0
            
            # Simple GPA prediction (can be enhanced with ML)
            predicted_gpa = (avg_score / 100) * 4.0
            
            conn.close()
            
            return render_template('premium/gpa_predictor.html', 
                                 current_avg=avg_score,
                                 predicted_gpa=predicted_gpa)
        except Exception as e:
            print(f"Error loading GPA predictor: {e}")
            flash('Error loading prediction')
            return redirect(url_for('premium_dashboard'))
    
        # ==================== CGPA CALCULATOR ====================
    @flask_app.route('/premium/cgpa-calculator', methods=['GET', 'POST'])
    @premium_required
    def cgpa_calculator():
        """Calculate a student's cumulative grade point average on a 5-point scale."""
        grade_points = {
            'A': Decimal('5.0'),
            'B': Decimal('4.0'),
            'C': Decimal('3.0'),
            'D': Decimal('2.0'),
            'E': Decimal('1.0'),
            'F': Decimal('0.0'),
        }
        grade_scale = [
            {'grade': 'A', 'range': '70–100', 'point': '5.0'},
            {'grade': 'B', 'range': '60–69', 'point': '4.0'},
            {'grade': 'C', 'range': '50–59', 'point': '3.0'},
            {'grade': 'D', 'range': '45–49', 'point': '2.0'},
            {'grade': 'E', 'range': '40–44', 'point': '1.0'},
            {'grade': 'F', 'range': '0–39', 'point': '0.0'},
        ]
        rows = []
        errors = []
        calculation = None

        if request.method == 'POST':
            names = request.form.getlist('course_name[]')
            codes = request.form.getlist('course_code[]')
            units = request.form.getlist('credit_units[]')
            grades = request.form.getlist('grade[]')
            row_count = max(len(names), len(codes), len(units), len(grades))

            if row_count < 1:
                errors.append('Add at least one course before calculating your CGPA.')
            elif row_count > 30:
                errors.append('You can calculate up to 30 courses at a time.')

            if not errors:
                total_units = Decimal('0')
                total_quality_points = Decimal('0')
                for index in range(row_count):
                    name = (names[index] if index < len(names) else '').strip()
                    code = (codes[index] if index < len(codes) else '').strip()
                    raw_units = units[index].strip() if index < len(units) else ''
                    grade = (grades[index].strip().upper() if index < len(grades) else '')
                    label = code or name or f'Course {index + 1}'
                    row = {'name': name, 'code': code, 'units': raw_units, 'grade': grade}

                    if not name:
                        errors.append(f'{label}: enter the course name.')
                    if not code:
                        errors.append(f'{label}: enter the course code.')

                    try:
                        credit_units = int(raw_units)
                    except (TypeError, ValueError):
                        errors.append(f'{label}: enter a whole-number credit unit between 1 and 10.')
                        rows.append(row)
                        continue

                    if not 1 <= credit_units <= 10:
                        errors.append(f'{label}: credit units must be between 1 and 10.')
                    if grade not in grade_points:
                        errors.append(f'{label}: select a valid grade from A to F.')

                    if 1 <= credit_units <= 10 and grade in grade_points:
                        point = grade_points[grade]
                        quality_points = point * credit_units
                        row.update({
                            'units': credit_units,
                            'point': f'{point:.1f}',
                            'quality_points': f'{quality_points:.1f}',
                        })
                        total_units += credit_units
                        total_quality_points += quality_points
                    rows.append(row)

                if not errors and total_units > 0:
                    cgpa = total_quality_points / total_units
                    calculation = {
                        'total_units': int(total_units),
                        'total_quality_points': f'{total_quality_points:.1f}',
                        'cgpa': f'{cgpa:.2f}',
                        'classification': (
                            'First Class' if cgpa >= Decimal('4.50') else
                            'Second Class Upper' if cgpa >= Decimal('3.50') else
                            'Second Class Lower' if cgpa >= Decimal('2.40') else
                            'Third Class' if cgpa >= Decimal('1.50') else
                            'Below pass threshold'
                        ),
                        'courses': list(rows),
                    }

        return render_template(
            'premium/cgpa_calculator.html',
            rows=rows,
            errors=errors,
            calculation=calculation,
            grade_scale=grade_scale,
        )

    # ==================== CHALLENGE FRIENDS ====================

    @flask_app.route('/premium/challenges')
    @premium_required
    def challenges():
        """Challenge friends in real-time competitions"""
        return render_template('premium/challenges.html')
    
    # ==================== PREMIUM SETTINGS ====================
    @flask_app.route('/premium/settings')
    @premium_required
    def premium_settings():
        """Premium account settings and subscription management"""
        user_id = session['user_id']
        premium_info = get_user_premium_info(user_id)
        
        return render_template('premium/settings.html', premium_info=premium_info)
    
    @flask_app.route('/premium/cancel-subscription', methods=['POST'])
    @premium_required
    def cancel_subscription():
        """Cancel premium subscription"""
        user_id = session['user_id']
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE payments 
                SET status = %s 
                WHERE user_id = %s AND status = %s
            ''', ('cancelled', user_id, 'paid'))
            conn.commit()
            conn.close()
            
            flash('Your subscription has been cancelled.')
            return redirect(url_for('index'))
        except Exception as e:
            print(f"Error cancelling subscription: {e}")
            flash('Error cancelling subscription')
            return redirect(url_for('premium_settings'))

