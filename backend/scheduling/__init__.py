"""Deterministic scheduling state and policy engine."""

from functools import lru_cache

from .catalog import Catalog
from .availability import MockAvailability, MockBookingService, Slot
from .engine import SchedulingEngine
from .extractor import RuleBasedExtractor
from .service import ConversationService
from .state import SchedulingRequest, SchedulingState


@lru_cache(maxsize=1)
def shared_conversation_service() -> ConversationService:
    """Reuse one connection pool and catalog cache inside each worker process."""

    return ConversationService.default()

__all__ = [
    "Catalog",
    "MockAvailability",
    "MockBookingService",
    "SchedulingEngine",
    "RuleBasedExtractor",
    "ConversationService",
    "SchedulingRequest",
    "SchedulingState",
    "Slot",
    "shared_conversation_service",
]
