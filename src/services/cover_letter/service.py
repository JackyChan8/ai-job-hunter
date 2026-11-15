import logging

from src.services.cover_letter.generator import CoverLetterGenerator
from src.services.cover_letter.reviewer import CoverLetterReviewer
from src.services.vacancy.schemas import VacancyData

logger = logging.getLogger(__name__)


class CoverLetterService:
    """Генерация и проверка сопроводительных писем без отправки email."""

    def __init__(self):
        self.generator = CoverLetterGenerator()
        self.reviewer = CoverLetterReviewer()

    @staticmethod
    def _is_approved(review) -> bool:
        return (
            bool(review.approved)
            and review.overall_score >= 80
            and review.factual_accuracy >= 90
            and not review.unsupported_claims
        )

    def generate_checked(
        self,
        vacancy: VacancyData,
        candidate_profile: dict,
        resume_text: str = "",
        max_attempts: int = 2,
        language: str = "русском",
    ) -> dict:
        feedback = None
        last_review = None
        letter = ""

        for attempt in range(1, max_attempts + 1):
            letter = self.generator.generate(
                vacancy=vacancy,
                candidate_profile=candidate_profile,
                resume_text=resume_text,
                feedback=feedback,
                language=language,
            )

            review = self.reviewer.review(
                letter=letter,
                vacancy=vacancy,
                candidate_profile=candidate_profile,
                resume_text=resume_text,
            )
            last_review = review

            logger.info(
                "Cover letter review: attempt=%s score=%s approved=%s",
                attempt,
                review.overall_score,
                self._is_approved(review),
            )

            if self._is_approved(review):
                return {
                    "success": True,
                    "letter": letter,
                    "review": review.model_dump(),
                    "attempts": attempt,
                }

            feedback = {
                "score": review.overall_score,
                "unsupported_claims": review.unsupported_claims,
                "missing_arguments": review.missing_arguments,
                "recommendations": review.recommendations,
            }

        return {
            "success": False,
            "letter": letter,
            "review": last_review.model_dump() if last_review else {},
            "attempts": max_attempts,
        }
