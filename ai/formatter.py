"""
Response formatting for PrepCampus Coach.
Formats AI responses for clarity and readability.
"""

from typing import Dict, Any, List
from datetime import datetime


class ResponseFormatter:
    """Formats coaching responses for students."""

    @staticmethod
    def format_performance_feedback(
        performance_data: Dict[str, Any],
        include_predictions: bool = True,
    ) -> str:
        """
        Format performance feedback.

        Args:
            performance_data: Performance analysis data
            include_predictions: Whether to include predictions

        Returns:
            Formatted feedback string
        """
        feedback = []

        # Header
        feedback.append("📊 PERFORMANCE ANALYSIS")
        feedback.append("=" * 50)

        # Overall performance
        accuracy = performance_data.get("accuracy", 0)
        feedback.append(f"\n✓ Overall Accuracy: {accuracy:.1f}%")

        # Topic breakdown
        if "accuracy_by_topic" in performance_data:
            feedback.append("\n📚 Accuracy by Topic:")
            for topic, acc in performance_data["accuracy_by_topic"].items():
                status = "✓" if acc >= 80 else "⚠" if acc >= 60 else "✗"
                feedback.append(f"  {status} {topic}: {acc:.1f}%")

        # Strengths
        if "strong_topics" in performance_data:
            feedback.append("\n💪 Strengths:")
            for topic in performance_data["strong_topics"]:
                feedback.append(f"  • {topic}")

        # Weaknesses
        if "weak_topics" in performance_data:
            feedback.append("\n⚡ Areas to Improve:")
            for topic in performance_data["weak_topics"]:
                feedback.append(f"  • {topic}")

        # Speed analysis
        if "speed_analysis" in performance_data:
            speed = performance_data["speed_analysis"]
            feedback.append(f"\n⏱ Time Management:")
            feedback.append(f"  • Overall Speed: {speed.get('overall_speed', 0):.2f} questions/min")
            feedback.append(f"  • Assessment: {speed.get('speed_assessment', 'unknown')}")

        # Predictions
        if include_predictions and "predictions" in performance_data:
            pred = performance_data["predictions"]
            feedback.append("\n🎯 Predicted Improvement:")
            feedback.append(f"  • With focused study: {pred.get('predicted_improvement', 0):.1f}% improvement")
            feedback.append(f"  • Estimated new score: {pred.get('predicted_score', 0):.1f}%")

        return "\n".join(feedback)

    @staticmethod
    def format_recommendations(
        recommendations: List[Dict[str, Any]],
    ) -> str:
        """
        Format study recommendations.

        Args:
            recommendations: List of recommendations

        Returns:
            Formatted recommendations string
        """
        output = []

        output.append("📖 PERSONALIZED STUDY PLAN")
        output.append("=" * 50)

        for i, rec in enumerate(recommendations, 1):
            priority_icon = "🔴" if rec.get("priority", 0) >= 4 else "🟡" if rec.get("priority", 0) >= 3 else "🟢"

            output.append(f"\n{i}. {priority_icon} {rec.get('topic', 'Unknown')}")
            output.append(f"   Priority: {'High' if rec.get('priority', 0) >= 4 else 'Medium' if rec.get('priority', 0) >= 3 else 'Low'}")
            output.append(f"   Why: {rec.get('reason', 'N/A')}")
            output.append(f"   Study Time: {rec.get('estimated_hours', 0):.1f} hours")
            output.append(f"   Expected Improvement: +{rec.get('expected_improvement', 0):.1f}%")
            output.append(f"   Practice Questions: {rec.get('practice_questions', 0)}")

        return "\n".join(output)

    @staticmethod
    def format_daily_goals(
        daily_goals: List[Dict[str, Any]],
    ) -> str:
        """
        Format daily study goals.

        Args:
            daily_goals: List of daily goals

        Returns:
            Formatted daily goals string
        """
        output = []

        output.append("📅 YOUR STUDY SCHEDULE")
        output.append("=" * 50)

        for goal in daily_goals:
            output.append(f"\n{goal.get('date', 'Day')} | {goal.get('estimated_time', 0):.1f} hours")
            output.append(f"Topics: {', '.join(goal.get('topics', []))}")
            output.append(f"Questions: {goal.get('questions_to_complete', 0)}")
            output.append(f"Success Criteria: {goal.get('success_criteria', 'N/A')}")

        return "\n".join(output)

    @staticmethod
    def format_exam_feedback(
        exam_data: Dict[str, Any],
        student_name: str = "Student",
    ) -> str:
        """
        Format post-exam feedback.

        Args:
            exam_data: Exam performance data
            student_name: Student's name

        Returns:
            Formatted exam feedback string
        """
        output = []

        output.append(f"📝 EXAM FEEDBACK FOR {student_name.upper()}")
        output.append("=" * 50)

        # Overall result
        accuracy = exam_data.get("accuracy", 0)
        if accuracy >= 80:
            result = "Excellent work!"
        elif accuracy >= 70:
            result = "Good performance. Room for improvement."
        elif accuracy >= 60:
            result = "Passing, but needs focused study."
        else:
            result = "Below target. Significant improvement needed."

        output.append(f"\n{result}")
        output.append(f"Score: {accuracy:.1f}%")

        # Accuracy by topic
        if "accuracy_by_topic" in exam_data:
            output.append("\n📊 Topic Breakdown:")
            for topic, acc in exam_data["accuracy_by_topic"].items():
                output.append(f"  • {topic}: {acc:.1f}%")

        # Mistakes
        if "repeated_mistakes" in exam_data:
            mistakes = exam_data["repeated_mistakes"]
            if mistakes.get("persistent_mistakes"):
                output.append("\n🔍 Patterns Noticed:")
                for topic, data in mistakes["persistent_mistakes"].items():
                    output.append(f"  • {topic}: {data.get('error_rate', 0)*100:.0f}% error rate")

        # Confidence analysis
        if "confidence_analysis" in exam_data:
            conf = exam_data["confidence_analysis"]
            output.append(f"\n💭 Confidence Assessment: {conf.get('calibration', 'unknown')}")

        # Next steps
        if "next_steps" in exam_data:
            output.append("\n➡️ Next Steps:")
            for step in exam_data["next_steps"]:
                output.append(f"  • {step}")

        return "\n".join(output)

    @staticmethod
    def format_confidence_analysis(
        confidence_data: Dict[str, Any],
    ) -> str:
        """
        Format confidence calibration analysis.

        Args:
            confidence_data: Confidence analysis data

        Returns:
            Formatted confidence analysis string
        """
        output = []

        output.append("🧠 CONFIDENCE CALIBRATION ANALYSIS")
        output.append("=" * 50)

        calibration = confidence_data.get("calibration", "unknown")
        output.append(f"\nStatus: {calibration.upper()}")

        if calibration == "overconfident":
            output.append("\n⚠️ You're answering questions with more confidence than your accuracy warrants.")
            output.append("This can lead to careless mistakes. Slow down and double-check your work.")
        elif calibration == "underconfident":
            output.append("\n💪 You're being too cautious! Your actual accuracy is better than you think.")
            output.append("Trust your preparation and answer with more confidence.")
        else:
            output.append("\n✓ Your confidence matches your performance. Good calibration!")

        if "details" in confidence_data:
            output.append("\n📊 By Topic:")
            for detail in confidence_data["details"]:
                topic = detail.get("topic", "Unknown")
                error = detail.get("calibration_error", 0)
                output.append(f"  • {topic}: {error:+.2f} (confidence vs accuracy)")

        return "\n".join(output)

    @staticmethod
    def format_coaching_summary(
        student_name: str,
        performance: Dict[str, Any],
        recommendations: List[Dict[str, Any]],
        next_exam_date: str = "Unknown",
    ) -> str:
        """
        Format complete coaching summary.

        Args:
            student_name: Student's name
            performance: Performance data
            recommendations: Study recommendations
            next_exam_date: Date of next exam

        Returns:
            Formatted coaching summary string
        """
        output = []

        output.append(f"🎓 COACHING SUMMARY FOR {student_name}")
        output.append("=" * 60)
        output.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Current status
        output.append("\n📊 CURRENT STATUS")
        output.append("-" * 60)
        accuracy = performance.get("average_accuracy", 0)
        output.append(f"Average Accuracy: {accuracy:.1f}%")
        output.append(f"Total Attempts: {performance.get('total_attempts', 0)}")
        output.append(f"Improvement Trend: {performance.get('improvement_trend', 0):+.2f}% per attempt")

        # Priorities
        output.append("\n🎯 PRIORITY AREAS")
        output.append("-" * 60)
        for i, rec in enumerate(recommendations[:3], 1):
            output.append(f"{i}. {rec.get('topic')}: {rec.get('reason')}")

        # Exam info
        output.append(f"\n📅 Next Exam: {next_exam_date}")

        # Call to action
        output.append("\n" + "=" * 60)
        output.append("Start with the priority areas above. Focus on mastery, not speed.")
        output.append("Review your progress daily and adjust your study plan as needed.")

        return "\n".join(output)
