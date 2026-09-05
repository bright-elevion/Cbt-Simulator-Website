"""
Student context building for PrepCampus Coach.
Aggregates student data to provide personalized coaching.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class StudentProfile:
    """Student profile data."""

    student_id: str
    name: str
    email: str
    subjects: List[str]
    target_score: Optional[int] = None
    exam_date: Optional[str] = None
    study_hours_available: Optional[float] = None


@dataclass
class TopicMastery:
    """Topic mastery level."""

    topic: str
    subject: str
    accuracy: float  # 0-100
    attempts: int
    last_attempted: Optional[str] = None
    confidence: float = 0.5  # 0-1


@dataclass
class ExamAttempt:
    """Single exam attempt."""

    attempt_id: str
    subject: str
    total_questions: int
    correct_answers: int
    accuracy: float  # 0-100
    time_taken: float  # minutes
    timestamp: str
    questions_by_topic: Dict[str, Dict[str, Any]]  # topic -> {correct, total, time}
    confidence_ratings: Dict[str, float]  # question_id -> confidence


class StudentContext:
    """Builds comprehensive student context for coaching."""

    def __init__(self, profile: StudentProfile):
        """
        Initialize student context.

        Args:
            profile: StudentProfile object
        """
        self.profile = profile
        self.topic_mastery: Dict[str, TopicMastery] = {}
        self.exam_history: List[ExamAttempt] = []
        self.weak_topics: List[str] = []
        self.strong_topics: List[str] = []

    def add_topic_mastery(self, mastery: TopicMastery) -> None:
        """Add topic mastery data."""
        key = f"{mastery.subject}_{mastery.topic}"
        self.topic_mastery[key] = mastery
        self._update_topic_classifications()

    def add_exam_attempt(self, attempt: ExamAttempt) -> None:
        """Add exam attempt to history."""
        self.exam_history.append(attempt)
        self._update_topic_classifications()

    def _update_topic_classifications(self) -> None:
        """Update weak and strong topic classifications."""
        if not self.topic_mastery:
            return

        accuracies = [m.accuracy for m in self.topic_mastery.values()]
        if not accuracies:
            return

        avg_accuracy = sum(accuracies) / len(accuracies)
        threshold_weak = avg_accuracy - 15  # 15% below average
        threshold_strong = avg_accuracy + 15  # 15% above average

        self.weak_topics = [
            f"{m.subject}_{m.topic}"
            for m in self.topic_mastery.values()
            if m.accuracy < threshold_weak
        ]

        self.strong_topics = [
            f"{m.subject}_{m.topic}"
            for m in self.topic_mastery.values()
            if m.accuracy > threshold_strong
        ]

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get overall performance summary."""
        if not self.exam_history:
            return {
                "total_attempts": 0,
                "average_accuracy": 0,
                "average_time_per_question": 0,
                "improvement_trend": None,
            }

        accuracies = [a.accuracy for a in self.exam_history]
        times = [a.time_taken / a.total_questions for a in self.exam_history]

        # Calculate improvement trend
        if len(accuracies) > 1:
            trend = (accuracies[-1] - accuracies[0]) / len(accuracies)
        else:
            trend = 0

        return {
            "total_attempts": len(self.exam_history),
            "average_accuracy": sum(accuracies) / len(accuracies),
            "latest_accuracy": accuracies[-1],
            "average_time_per_question": sum(times) / len(times),
            "improvement_trend": trend,
        }

    def get_topic_analysis(self) -> Dict[str, Any]:
        """Get detailed topic analysis."""
        return {
            "total_topics": len(self.topic_mastery),
            "weak_topics": self.weak_topics,
            "strong_topics": self.strong_topics,
            "mastery_levels": {
                key: asdict(mastery) for key, mastery in self.topic_mastery.items()
            },
        }

    def get_confidence_analysis(self) -> Dict[str, Any]:
        """Analyze confidence calibration."""
        if not self.exam_history:
            return {"calibration": "insufficient_data"}

        calibration_data = []

        for attempt in self.exam_history:
            for topic, questions in attempt.questions_by_topic.items():
                if "confidence" in questions:
                    avg_confidence = sum(
                        attempt.confidence_ratings.get(q_id, 0.5)
                        for q_id in questions.get("question_ids", [])
                    ) / max(len(questions.get("question_ids", [])), 1)

                    actual_accuracy = (
                        questions["correct"] / questions["total"] * 100
                        if questions["total"] > 0
                        else 0
                    )

                    calibration_data.append(
                        {
                            "topic": topic,
                            "avg_confidence": avg_confidence,
                            "actual_accuracy": actual_accuracy,
                            "calibration_error": avg_confidence - (actual_accuracy / 100),
                        }
                    )

        if not calibration_data:
            return {"calibration": "insufficient_data"}

        avg_error = sum(d["calibration_error"] for d in calibration_data) / len(
            calibration_data
        )

        return {
            "calibration": "overconfident" if avg_error > 0.1 else "underconfident"
            if avg_error < -0.1
            else "well_calibrated",
            "average_error": avg_error,
            "details": calibration_data,
        }

    def get_study_recommendations_context(self) -> str:
        """Generate context string for study recommendations."""
        summary = self.get_performance_summary()
        topics = self.get_topic_analysis()
        confidence = self.get_confidence_analysis()

        context = f"""
Student: {self.profile.name}
Subjects: {', '.join(self.profile.subjects)}
Target Score: {self.profile.target_score or 'Not set'}
Study Hours Available: {self.profile.study_hours_available or 'Not specified'} hours/week

Performance Summary:
- Total Attempts: {summary['total_attempts']}
- Average Accuracy: {summary['average_accuracy']:.1f}%
- Latest Accuracy: {summary['latest_accuracy']:.1f}%
- Improvement Trend: {summary['improvement_trend']:.2f}% per attempt
- Average Time per Question: {summary['average_time_per_question']:.1f} minutes

Topic Mastery:
- Strong Topics ({len(topics['strong_topics'])}): {', '.join(topics['strong_topics']) or 'None yet'}
- Weak Topics ({len(topics['weak_topics'])}): {', '.join(topics['weak_topics']) or 'None identified'}

Confidence Calibration:
- Status: {confidence.get('calibration', 'unknown')}
- Average Error: {confidence.get('average_error', 0):.2f}
"""
        return context

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary."""
        return {
            "profile": asdict(self.profile),
            "performance_summary": self.get_performance_summary(),
            "topic_analysis": self.get_topic_analysis(),
            "confidence_analysis": self.get_confidence_analysis(),
        }
