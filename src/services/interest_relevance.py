"""Shared public-interest admission. Primary remains a display ordering fact."""
import math

INTEREST_MATCH_MIN_RELEVANCE = 0.8


def public_interest_qualifies(relevance):
    if relevance is None or isinstance(relevance, bool):
        return False
    try:
        value = float(relevance)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(value) and INTEREST_MATCH_MIN_RELEVANCE <= value <= 1.0


def public_interest_predicate():
    from models.db import ArticleTagAssignmentRecord
    # NULL comparison is unknown/false; the same bounds as the in-memory path.
    return ArticleTagAssignmentRecord.relevance.between(INTEREST_MATCH_MIN_RELEVANCE, 1.0)
