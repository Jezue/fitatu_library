"""Backwards-compatible imports for the old module layout."""

from .auth import FitatuAuthContext, FitatuTokenStore
from .client import FitatuApiClient
from .exceptions import FitatuApiError
from .modules import (
    ActivitiesModule,
    AuthModule,
    CmsModule,
    DietPlanModule,
    PlannerModule,
    ResourcesModule,
    UserSettingsModule,
    WaterModule,
)

__all__ = [
    "FitatuApiClient",
    "FitatuAuthContext",
    "FitatuApiError",
    "FitatuTokenStore",
    "AuthModule",
    "ActivitiesModule",
    "PlannerModule",
    "UserSettingsModule",
    "DietPlanModule",
    "WaterModule",
    "ResourcesModule",
    "CmsModule",
]
