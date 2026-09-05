"""
Study recommendations engine for PrepCampus Coach.
Generates personalized study plans and recommendations.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class StudyRecommendation:
    """Single study recommendation."""

    topic: str
    priority: int  # 1-5, 5 being highest
    reason: str
    estimated_hours: float
    expected_improvement: float  # percentage points
    practice_questions: int
    difficulty_progression: List[str]  # easy, medium, hard


@dataclass
class DailyGoal:
    """Daily study goal."""

    date: str
    topics: List[str]
    estimated_time: float
    questions_to_complete: int
    success_criteria: str


class RecommendationEngine:
    """Generates personalized study recommendations."""

    @staticmethod
    def prioritize_topics(
        weak_topics: List[str],
        topic_accuracies: Dict[str, float],
        exam_goals: Optional[Dict[str, Any]] = None,
    ) -> List[StudyRecommendation]:
        """
        Prioritize topics for study based on impact.

        Args:
            weak_topics: List of weak topics
            topic_accuracies: Current accuracy for each topic
            exam_goals: Optional exam goals (target score, time constraints)

        Returns:
            Prioritized list of study recommendations
        """
        recommendations = []

        for topic in weak_topics:
            accuracy = topic_accuracies.get(topic, 0)
            gap_to_mastery = 100 - accuracy

            # Priority based on accuracy gap and potential impact
            priority = min(5, max(1, int(gap_to_mastery / 20)))

            # Estimate study time (roughly 1 hour per 10% improvement)
            estimated_hours = gap_to_mastery / 10

            # Expected improvement (realistic, not optimistic)
            expected_improvement = min(gap_to_mastery * 0.7, 25)

            # Practice questions (more for lower accuracy)
            practice_questions = int(20 + (gap_to_mastery / 5))

            reason = f"Current accuracy {accuracy:.0f}%. Improving this topic could increase overall score by ~{expected_improvement:.0f}%."

            recommendations.append(
                StudyRecommendation(
                    topic=topic,
                    priority=priority,
                    reason=reason,
                    estimated_hours=estimated_hours,
                    expected_improvement=expected_improvement,
                    practice_questions=practice_questions,
                    difficulty_progression=["easy", "medium", "hard"],
                )
            )

        # Sort by priority (descending)
        recommendations.sort(key=lambda x: x.priority, reverse=True)

        return recommendations

    @staticmethod
    def generate_daily_goals(
        recommendations: List[StudyRecommendation],
        available_hours_per_day: float = 2,
        days_until_exam: int = 7,
    ) -> List[DailyGoal]:
        """
        Generate daily study goals.

        Args:
            recommendations: Prioritized recommendations
            available_hours_per_day: Hours available for study per day
            days_until_exam: Days remaining until exam

        Returns:
            List of daily goals
        """
        daily_goals = []

        # Distribute study across days
        total_study_hours = sum(r.estimated_hours for r in recommendations)
        hours_per_day = min(available_hours_per_day, total_study_hours / days_until_exam)

        current_day = 0
        remaining_recommendations = list(recommendations)

        while remaining_recommendations and current_day < days_until_exam:
            daily_topics = []
            daily_questions = 0
            daily_hours = 0

            # Add topics until we reach daily limit
            while (
                remaining_recommendations
                and daily_hours + remaining_recommendations[0].estimated_hours
                <= hours_per_day
            ):
                rec = remaining_recommendations.pop(0)
                daily_topics.append(rec.topic)
                daily_questions += rec.practice_questions
                daily_hours += rec.estimated_hours

            if daily_topics:
                success_criteria = f"Complete {daily_questions} practice questions with >75% accuracy"

                daily_goals.append(
                    DailyGoal(
                        date=f"Day {current_day + 1}",
                        topics=daily_topics,
                        estimated_time=daily_hours,
                        questions_to_complete=daily_questions,
                        success_criteria=success_criteria,
                    )
                )

            current_day += 1

        return daily_goals

    @staticmethod
    def generate_practice_plan(
        weak_topic: str,
        current_accuracy: float,
        available_time: float = 1,
    ) -> Dict[str, Any]:
        """
        Generate detailed practice plan for a weak topic.

        Args:
            weak_topic: Topic to practice
            current_accuracy: Current accuracy in this topic
            available_time: Available study time in hours

        Returns:
            Detailed practice plan
        """
        # Determine difficulty progression
        if current_accuracy < 50:
            progression = ["easy", "easy", "medium", "medium", "hard"]
        elif current_accuracy < 70:
            progression = ["easy", "medium", "medium", "hard", "hard"]
        else:
            progression = ["medium", "hard", "hard"]

        # Calculate questions per difficulty
        total_questions = int(20 + (100 - current_accuracy) / 5)
        questions_per_difficulty = total_questions // len(progression)

        plan = {
            "topic": weak_topic,
            "current_accuracy": current_accuracy,
            "target_accuracy": 85,
            "available_time_hours": available_time,
            "total_questions": total_questions,
            "phases": [],
        }

        time_per_question = (available_time * 60) / total_questions  # minutes

        for i, difficulty in enumerate(progression):
            phase_questions = questions_per_difficulty
            if i == len(progression) - 1:
                phase_questions = total_questions - (
                    questions_per_difficulty * (len(progression) - 1)
                )

            plan["phases"].append(
                {
                    "phase": i + 1,
                    "difficulty": difficulty,
                    "questions": phase_questions,
                    "estimated_time_minutes": phase_questions * time_per_question,
                    "success_criteria": f"{difficulty.capitalize()} questions: >80% accuracy",
                }
            )

        return plan

    @staticmethod
    def get_question_recommendations(
        weak_topics: List[str],
        topic_accuracies: Dict[str, float],
        available_questions: int = 50,
    ) -> Dict[str, Any]:
        """
        Recommend specific question types to practice.

        Args:
            weak_topics: Weak topics to focus on
            topic_accuracies: Current accuracy by topic
            available_questions: Total questions available to practice

        Returns:
            Question recommendations
        """
        recommendations = {}

        # Allocate questions based on weakness severity
        total_gap = sum(100 - topic_accuracies.get(t, 0) for t in weak_topics)

        for topic in weak_topics:
            accuracy = topic_accuracies.get(topic, 0)
            gap = 100 - accuracy
            allocation = int((gap / total_gap) * available_questions) if total_gap > 0 else 0

            recommendations[topic] = {
                "questions_to_practice": allocation,
                "difficulty_mix": {
                    "easy": int(allocation * 0.3),
                    "medium": int(allocation * 0.5),
                    "hard": int(allocation * 0.2),
                },
                "focus_areas": RecommendationEngine._identify_focus_areas(topic, accuracy),
                "success_criteria": f"Achieve >80% accuracy across all difficulties",
            }

        return recommendations

    @staticmethod
    def _identify_focus_areas(topic: str, accuracy: float) -> List[str]:
        """Identify specific focus areas within a topic."""
        # This is a placeholder - in real implementation, this would analyze
        # specific question types and subtopics within the main topic
        if accuracy < 50:
            return ["fundamentals", "concept_application", "problem_solving"]
        elif accuracy < 70:
            return ["advanced_concepts", "complex_scenarios", "edge_cases"]
        else:
            return ["optimization", "speed", "confidence"]

    @staticmethod
    def estimate_time_to_mastery(
        topic: str,
        current_accuracy: float,
        target_accuracy: float = 85,
    ) -> float:
        """
        Estimate time needed to reach target accuracy.

        Args:
            topic: Topic name
            current_accuracy: Current accuracy
            target_accuracy: Target accuracy

        Returns:
            Estimated hours needed
        """
        # Heuristic: ~0.5 hours per 10% improvement
        gap = target_accuracy - current_accuracy
        if gap <= 0:
            return 0

        # Diminishing returns: harder to improve from 80% to 90% than 50% to 60%
        if current_accuracy < 60:
            hours_per_10_percent = 0.5
        elif current_accuracy < 75:
            hours_per_10_percent = 0.75
        else:
            hours_per_10_percent = 1.0

        return (gap / 10) * hours_per_10_percent
