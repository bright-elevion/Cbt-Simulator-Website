"""
Main PrepCampus Coach module.
Orchestrates AI coaching for students.
"""

from typing import Dict, Any, Optional, List
import json
import os
from .providers import generate_text


from .client import AIClient, create_client
from .context import StudentContext, StudentProfile, TopicMastery, ExamAttempt
from .analytics import PerformanceAnalyzer
from .recommendations import RecommendationEngine
from .formatter import ResponseFormatter
from .prompts import (
    SYSTEM_PROMPT,
    ANALYSIS_PROMPT_TEMPLATE,
    TOPIC_RECOMMENDATION_TEMPLATE,
    EXAM_FEEDBACK_TEMPLATE,
)


def get_coach_response(question, user_data=None):

    context = ""

    if user_data:
        context = f"""
        Student course: {user_data.get('course', 'Unknown')}
        Weak topics: {user_data.get('weak_topics', [])}
        Accuracy: {user_data.get('accuracy', 0)}%
        """

    prompt = f"""
    You are PrepCampus AI Coach.

    Your job is to teach students, not just give answers.

    {context}

    Student question:
    {question}

    Rules:

    1. Explain clearly.
    2. Use simple language.
    3. Give examples.
    4. Give memory tricks when possible.
    5. Ask a follow-up question to test understanding.
    """

    answer, _provider = generate_text(
        system_prompt=(
            "You are the PrepCampus premium AI tutor. Teach clearly, "
            "avoid fabricated facts, and return a helpful plain-text answer."
        ),
        user_message=prompt,
        temperature=0.7,
        max_tokens=1500,
    )

    if not answer:
        raise RuntimeError("The AI tutor returned an empty response")

    return answer.strip()

class PrepCampusCoach:
    """Main AI coaching engine for PrepCampus."""

    def __init__(
        self,
        student_profile: StudentProfile,
        ai_provider: str = "openai",
        ai_key: Optional[str] = None,
        ai_model: Optional[str] = None,
    ):
        """
        Initialize PrepCampus Coach.

        Args:
            student_profile: Student profile
            ai_provider: "openai" or "gemini"
            ai_key: API key for AI provider
            ai_model: Model name (optional)
        """
        self.profile = student_profile
        self.context = StudentContext(student_profile)
        self.ai_client = create_client(
            provider=ai_provider, api_key=ai_key, model=ai_model
        )
        self.analyzer = PerformanceAnalyzer()
        self.recommender = RecommendationEngine()
        self.formatter = ResponseFormatter()

    def add_exam_attempt(self, attempt: ExamAttempt) -> None:
        """Add an exam attempt to student history."""
        self.context.add_exam_attempt(attempt)

    def add_topic_mastery(self, mastery: TopicMastery) -> None:
        """Add topic mastery data."""
        self.context.add_topic_mastery(mastery)

    def analyze_exam_performance(self, exam_attempt: ExamAttempt) -> Dict[str, Any]:
        """
        Analyze exam performance using AI.

        Args:
            exam_attempt: Exam attempt to analyze

        Returns:
            Analysis results
        """
        # Calculate metrics
        accuracy_by_topic = self.analyzer.calculate_accuracy_by_topic(
            {
                "questions_by_topic": exam_attempt.questions_by_topic,
                "total_questions": exam_attempt.total_questions,
                "correct_answers": exam_attempt.correct_answers,
            }
        )

        weak_areas = self.analyzer.identify_weak_areas(
            {
                "questions_by_topic": exam_attempt.questions_by_topic,
                "total_questions": exam_attempt.total_questions,
                "correct_answers": exam_attempt.correct_answers,
            }
        )

        strong_areas = self.analyzer.identify_strong_areas(
            {
                "questions_by_topic": exam_attempt.questions_by_topic,
                "total_questions": exam_attempt.total_questions,
                "correct_answers": exam_attempt.correct_answers,
            }
        )

        speed_analysis = self.analyzer.analyze_speed(
            {
                "time_taken": exam_attempt.time_taken,
                "total_questions": exam_attempt.total_questions,
                "questions_by_topic": exam_attempt.questions_by_topic,
            }
        )

        guessing_patterns = self.analyzer.detect_guessing_patterns(
            {
                "confidence_ratings": exam_attempt.confidence_ratings,
                "questions_by_topic": exam_attempt.questions_by_topic,
            }
        )

        # Build AI prompt
        student_context_str = self.context.get_study_recommendations_context()
        exam_results_str = f"""
Exam: {exam_attempt.subject}
Score: {exam_attempt.accuracy:.1f}%
Correct: {exam_attempt.correct_answers}/{exam_attempt.total_questions}
Time: {exam_attempt.time_taken} minutes

Accuracy by Topic: {json.dumps(accuracy_by_topic, indent=2)}
Strong Areas: {', '.join(strong_areas) or 'None'}
Weak Areas: {', '.join(weak_areas) or 'None'}
Speed: {speed_analysis['overall_speed']:.2f} questions/min
Guessing Rate: {guessing_patterns['guess_rate']:.1%}
"""

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            student_context=student_context_str, exam_results=exam_results_str
        )

        # Get AI analysis
        ai_response = self.ai_client.generate_response(
            system_prompt=SYSTEM_PROMPT,
            user_message=prompt,
            temperature=0.7,
            max_tokens=2000,
        )

        return {
            "exam_attempt": exam_attempt,
            "accuracy": exam_attempt.accuracy,
            "accuracy_by_topic": accuracy_by_topic,
            "weak_topics": weak_areas,
            "strong_topics": strong_areas,
            "speed_analysis": speed_analysis,
            "guessing_patterns": guessing_patterns,
            "ai_analysis": ai_response,
        }

    def get_study_recommendations(
        self,
        hours_available: float = 2,
        days_until_exam: int = 7,
    ) -> Dict[str, Any]:
        """
        Get personalized study recommendations.

        Args:
            hours_available: Hours available per day
            days_until_exam: Days until next exam

        Returns:
            Study recommendations
        """
        # Get topic analysis
        topic_analysis = self.context.get_topic_analysis()
        weak_topics = topic_analysis.get("weak_topics", [])

        if not weak_topics:
            return {
                "status": "no_weak_topics",
                "message": "You've mastered all topics! Focus on maintaining your performance.",
            }

        # Get mastery levels
        mastery_levels = {
            key.split("_", 1)[1]: mastery.accuracy
            for key, mastery in topic_analysis.get("mastery_levels", {}).items()
        }

        # Generate recommendations
        recommendations = self.recommender.prioritize_topics(
            weak_topics, mastery_levels
        )

        # Generate daily goals
        daily_goals = self.recommender.generate_daily_goals(
            recommendations, hours_available, days_until_exam
        )

        # Build AI prompt
        student_context_str = self.context.get_study_recommendations_context()
        mastery_str = json.dumps(mastery_levels, indent=2)
        performance_summary = self.context.get_performance_summary()
        recent_performance = f"Average Accuracy: {performance_summary['average_accuracy']:.1f}%, Trend: {performance_summary['improvement_trend']:+.2f}%"

        prompt = TOPIC_RECOMMENDATION_TEMPLATE.format(
            mastery_levels=mastery_str,
            recent_performance=recent_performance,
            exam_goals=f"Target: {self.profile.target_score or 'Not set'}",
        )

        # Get AI recommendations
        ai_response = self.ai_client.generate_response(
            system_prompt=SYSTEM_PROMPT,
            user_message=prompt,
            temperature=0.7,
            max_tokens=2000,
        )

        return {
            "recommendations": [
                {
                    "topic": r.topic,
                    "priority": r.priority,
                    "reason": r.reason,
                    "estimated_hours": r.estimated_hours,
                    "expected_improvement": r.expected_improvement,
                    "practice_questions": r.practice_questions,
                }
                for r in recommendations
            ],
            "daily_goals": [
                {
                    "date": g.date,
                    "topics": g.topics,
                    "estimated_time": g.estimated_time,
                    "questions_to_complete": g.questions_to_complete,
                    "success_criteria": g.success_criteria,
                }
                for g in daily_goals
            ],
            "ai_recommendations": ai_response,
        }

    def get_coaching_feedback(self) -> str:
        """
        Get overall coaching feedback.

        Returns:
            Formatted coaching feedback
        """
        performance = self.context.get_performance_summary()
        topic_analysis = self.context.get_topic_analysis()

        return self.formatter.format_coaching_summary(
            student_name=self.profile.name,
            performance=performance,
            recommendations=[
                {"topic": t, "reason": f"Weak area: {t}"}
                for t in topic_analysis.get("weak_topics", [])
            ],
            next_exam_date=self.profile.exam_date or "Unknown",
        )

    def ask_question(self, question: str) -> str:
        """
        Ask the coach a question.

        Args:
            question: Student's question

        Returns:
            Coach's response
        """
        student_context_str = self.context.get_study_recommendations_context()

        full_prompt = f"""
Student Context:
{student_context_str}

Student Question:
{question}

Provide a personalized response based on the student's data. Be specific and measurable.
"""

        response = self.ai_client.generate_response(
            system_prompt=SYSTEM_PROMPT,
            user_message=full_prompt,
            temperature=0.7,
            max_tokens=1500,
        )

        return response

    def get_practice_plan(self, topic: str, available_time: float = 1) -> str:
        """
        Get a practice plan for a specific topic.

        Args:
            topic: Topic to practice
            available_time: Available study time in hours

        Returns:
            Formatted practice plan
        """
        topic_analysis = self.context.get_topic_analysis()
        mastery_levels = topic_analysis.get("mastery_levels", {})

        # Find accuracy for this topic
        topic_key = next(
            (k for k in mastery_levels.keys() if topic in k), None
        )
        current_accuracy = (
            mastery_levels[topic_key]["accuracy"] if topic_key else 50
        )

        plan = self.recommender.generate_practice_plan(
            topic, current_accuracy, available_time
        )

        return json.dumps(plan, indent=2)

    def export_student_data(self) -> Dict[str, Any]:
        """
        Export complete student data and analysis.

        Returns:
            Complete student data
        """
        return {
            "profile": {
                "student_id": self.profile.student_id,
                "name": self.profile.name,
                "email": self.profile.email,
                "subjects": self.profile.subjects,
                "target_score": self.profile.target_score,
                "exam_date": self.profile.exam_date,
            },
            "performance": self.context.get_performance_summary(),
            "topic_analysis": self.context.get_topic_analysis(),
            "confidence_analysis": self.context.get_confidence_analysis(),
        }
