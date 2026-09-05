"""
PrepCampus Coach - AI coaching system for exam preparation.
"""

from .coach import PrepCampusCoach
from .client import AIClient, OpenAIClient, GeminiClient, create_client
from .context import StudentContext, StudentProfile, TopicMastery, ExamAttempt
from .analytics import PerformanceAnalyzer, PerformanceMetrics
from .recommendations import RecommendationEngine, StudyRecommendation, DailyGoal
from .formatter import ResponseFormatter
from .prompts import (
    SYSTEM_PROMPT,
    ANALYSIS_PROMPT_TEMPLATE,
    TOPIC_RECOMMENDATION_TEMPLATE,
    QUESTION_RECOMMENDATION_TEMPLATE,
    EXAM_FEEDBACK_TEMPLATE,
    CONFIDENCE_ANALYSIS_TEMPLATE,
)

__version__ = "1.0.0"
__author__ = "PrepCampus Team"

__all__ = [
    "PrepCampusCoach",
    "AIClient",
    "OpenAIClient",
    "GeminiClient",
    "create_client",
    "StudentContext",
    "StudentProfile",
    "TopicMastery",
    "ExamAttempt",
    "PerformanceAnalyzer",
    "PerformanceMetrics",
    "RecommendationEngine",
    "StudyRecommendation",
    "DailyGoal",
    "ResponseFormatter",
    "SYSTEM_PROMPT",
    "ANALYSIS_PROMPT_TEMPLATE",
    "TOPIC_RECOMMENDATION_TEMPLATE",
    "QUESTION_RECOMMENDATION_TEMPLATE",
    "EXAM_FEEDBACK_TEMPLATE",
    "CONFIDENCE_ANALYSIS_TEMPLATE",
]
