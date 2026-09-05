"""
Analytics helpers for PrepCampus Coach.
Analyzes exam performance and generates insights.
"""

from typing import Dict, Any, List, Tuple
from dataclasses import dataclass


@dataclass
class PerformanceMetrics:
    """Performance metrics for analysis."""

    accuracy: float
    speed: float  # questions per minute
    consistency: float  # 0-1, based on variance
    confidence_calibration: float  # -1 to 1, negative = underconfident
    improvement_rate: float  # % improvement per attempt


class PerformanceAnalyzer:
    """Analyzes student exam performance."""

    @staticmethod
    def calculate_accuracy_by_topic(
        exam_attempt: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        Calculate accuracy for each topic.

        Args:
            exam_attempt: Exam attempt data

        Returns:
            Dict of topic -> accuracy percentage
        """
        accuracies = {}

        for topic, data in exam_attempt.get("questions_by_topic", {}).items():
            if data.get("total", 0) > 0:
                accuracy = (data.get("correct", 0) / data["total"]) * 100
                accuracies[topic] = accuracy

        return accuracies

    @staticmethod
    def identify_weak_areas(
        exam_attempt: Dict[str, Any], threshold: float = 70
    ) -> List[str]:
        """
        Identify topics below accuracy threshold.

        Args:
            exam_attempt: Exam attempt data
            threshold: Accuracy threshold (default 70%)

        Returns:
            List of weak topics
        """
        accuracies = PerformanceAnalyzer.calculate_accuracy_by_topic(exam_attempt)
        return [topic for topic, acc in accuracies.items() if acc < threshold]

    @staticmethod
    def identify_strong_areas(
        exam_attempt: Dict[str, Any], threshold: float = 85
    ) -> List[str]:
        """
        Identify topics above accuracy threshold.

        Args:
            exam_attempt: Exam attempt data
            threshold: Accuracy threshold (default 85%)

        Returns:
            List of strong topics
        """
        accuracies = PerformanceAnalyzer.calculate_accuracy_by_topic(exam_attempt)
        return [topic for topic, acc in accuracies.items() if acc >= threshold]

    @staticmethod
    def analyze_speed(exam_attempt: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze time management and speed.

        Args:
            exam_attempt: Exam attempt data

        Returns:
            Speed analysis metrics
        """
        total_time = exam_attempt.get("time_taken", 0)  # minutes
        total_questions = exam_attempt.get("total_questions", 1)

        questions_per_minute = total_questions / total_time if total_time > 0 else 0

        # Analyze speed by topic
        speed_by_topic = {}
        for topic, data in exam_attempt.get("questions_by_topic", {}).items():
            topic_time = data.get("time", 0)
            topic_questions = data.get("total", 1)
            speed_by_topic[topic] = (
                topic_questions / topic_time if topic_time > 0 else 0
            )

        return {
            "overall_speed": questions_per_minute,
            "total_time_minutes": total_time,
            "speed_by_topic": speed_by_topic,
            "speed_assessment": PerformanceAnalyzer._assess_speed(questions_per_minute),
        }

    @staticmethod
    def _assess_speed(questions_per_minute: float) -> str:
        """Assess if speed is appropriate."""
        if questions_per_minute < 0.5:
            return "too_slow"
        elif questions_per_minute > 2:
            return "too_fast"
        else:
            return "appropriate"

    @staticmethod
    def detect_guessing_patterns(
        exam_attempt: Dict[str, Any], confidence_threshold: float = 0.5
    ) -> Dict[str, Any]:
        """
        Detect guessing patterns (low confidence answers).

        Args:
            exam_attempt: Exam attempt data
            confidence_threshold: Confidence threshold for guessing

        Returns:
            Guessing pattern analysis
        """
        confidence_ratings = exam_attempt.get("confidence_ratings", {})
        questions_by_topic = exam_attempt.get("questions_by_topic", {})

        guesses = []
        for q_id, confidence in confidence_ratings.items():
            if confidence < confidence_threshold:
                guesses.append({"question_id": q_id, "confidence": confidence})

        # Analyze guessing by topic
        guessing_by_topic = {}
        for topic, data in questions_by_topic.items():
            topic_guesses = [
                q for q in guesses if q["question_id"] in data.get("question_ids", [])
            ]
            if data.get("total", 0) > 0:
                guess_rate = len(topic_guesses) / data["total"]
                guessing_by_topic[topic] = {
                    "guess_count": len(topic_guesses),
                    "guess_rate": guess_rate,
                    "assessment": "high_guessing"
                    if guess_rate > 0.3
                    else "moderate_guessing"
                    if guess_rate > 0.1
                    else "low_guessing",
                }

        return {
            "total_guesses": len(guesses),
            "guess_rate": len(guesses) / len(confidence_ratings)
            if confidence_ratings
            else 0,
            "guessing_by_topic": guessing_by_topic,
        }

    @staticmethod
    def identify_repeated_mistakes(
        exam_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Identify patterns of repeated mistakes.

        Args:
            exam_history: List of exam attempts

        Returns:
            Repeated mistake analysis
        """
        if len(exam_history) < 2:
            return {"status": "insufficient_data"}

        mistake_patterns = {}

        for attempt in exam_history:
            for topic, data in attempt.get("questions_by_topic", {}).items():
                if topic not in mistake_patterns:
                    mistake_patterns[topic] = {
                        "total_attempts": 0,
                        "incorrect_attempts": 0,
                        "attempts": [],
                    }

                incorrect = data.get("total", 0) - data.get("correct", 0)
                mistake_patterns[topic]["total_attempts"] += data.get("total", 0)
                mistake_patterns[topic]["incorrect_attempts"] += incorrect
                mistake_patterns[topic]["attempts"].append(
                    {
                        "accuracy": (data.get("correct", 0) / data.get("total", 1))
                        * 100,
                        "timestamp": attempt.get("timestamp"),
                    }
                )

        # Identify persistent mistakes
        persistent_mistakes = {}
        for topic, data in mistake_patterns.items():
            if data["total_attempts"] > 0:
                error_rate = data["incorrect_attempts"] / data["total_attempts"]
                if error_rate > 0.2:  # More than 20% error rate
                    persistent_mistakes[topic] = {
                        "error_rate": error_rate,
                        "attempts": data["attempts"],
                        "trend": PerformanceAnalyzer._calculate_trend(
                            data["attempts"]
                        ),
                    }

        return {
            "persistent_mistakes": persistent_mistakes,
            "status": "improving"
            if all(m["trend"] > 0 for m in persistent_mistakes.values())
            else "worsening"
            if all(m["trend"] < 0 for m in persistent_mistakes.values())
            else "mixed",
        }

    @staticmethod
    def _calculate_trend(attempts: List[Dict[str, Any]]) -> float:
        """Calculate accuracy trend (positive = improving)."""
        if len(attempts) < 2:
            return 0

        accuracies = [a["accuracy"] for a in attempts]
        return (accuracies[-1] - accuracies[0]) / len(attempts)

    @staticmethod
    def predict_score_improvement(
        weak_topics: List[str],
        current_accuracy: float,
        study_hours: float = 1,
    ) -> Dict[str, Any]:
        """
        Predict score improvement from focused study.

        Args:
            weak_topics: List of weak topics to study
            current_accuracy: Current accuracy percentage
            study_hours: Hours of focused study

        Returns:
            Improvement prediction
        """
        # Heuristic: ~2-3% improvement per hour of focused study on weak topics
        improvement_per_hour = 2.5
        predicted_improvement = min(study_hours * improvement_per_hour, 30)

        # Cap at 100%
        predicted_new_score = min(current_accuracy + predicted_improvement, 100)

        return {
            "current_score": current_accuracy,
            "predicted_score": predicted_new_score,
            "predicted_improvement": predicted_improvement,
            "study_hours": study_hours,
            "topics_to_focus": weak_topics,
            "confidence": "high"
            if len(weak_topics) <= 3
            else "medium"
            if len(weak_topics) <= 5
            else "low",
        }
