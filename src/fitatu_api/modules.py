"""Compatibility exports for the older flat module layout."""

from .planner import PlannerModule
from .service_modules import (
    ActivitiesModule,
    AuthModule,
    CmsModule,
    DietPlanModule,
    FoodsModule,
    MeasurementsModule,
    ResourcesModule,
    SummaryModule,
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
    "MeasurementsModule",
    "SummaryModule",
    "FoodsModule",
    "ActivitiesModule",
]
