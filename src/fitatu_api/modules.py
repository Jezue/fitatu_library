"""Compatibility exports for the older flat module layout."""

from .planner import PlannerModule
from .service_modules import (
    ActivitiesModule,
    AuthModule,
    CmsModule,
    DietPlanModule,
    ResourcesModule,
    UserSettingsModule,
    WaterModule,
)

__all__ = [
    "PlannerModule",
    "ResourcesModule",
    "CmsModule",
    "AuthModule",
    "UserSettingsModule",
    "DietPlanModule",
    "WaterModule",
    "ActivitiesModule",
]
