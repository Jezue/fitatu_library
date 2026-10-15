"""Public package exports for fitatu-api."""

__version__ = "0.6.0"

from .agent import FitatuTools
from .auth import FitatuAuthContext, FitatuTokenStore
from .client import FitatuApiClient
from .exceptions import (
    FitatuApiError,
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuNotFoundError,
    FitatuPlannerSyncError,
    FitatuPremiumRequiredError,
    FitatuRateLimitError,
    FitatuValidationError,
    describe_error,
)
from .facade import FitatuLibrary
from .planner import PlannerModule
from .service_modules import (
    ActivitiesModule,
    DietPlanModule,
    FastingModule,
    FoodsModule,
    HabitsModule,
    MeasurementsModule,
    ResourcesModule,
    SummaryModule,
    UserSettingsModule,
    WaterModule,
)

__all__ = [
    "FitatuTools",
    "FitatuLibrary",
    "FitatuApiClient",
    "FitatuAuthContext",
    "FitatuApiError",
    "FitatuAuthError",
    "FitatuConnectionError",
    "FitatuRateLimitError",
    "FitatuMaintenanceError",
    "FitatuValidationError",
    "FitatuNotFoundError",
    "FitatuPlannerSyncError",
    "FitatuPremiumRequiredError",
    "describe_error",
    "FitatuTokenStore",
    "ActivitiesModule",
    "PlannerModule",
    "UserSettingsModule",
    "DietPlanModule",
    "WaterModule",
    "MeasurementsModule",
    "SummaryModule",
    "FoodsModule",
    "FastingModule",
    "HabitsModule",
    "ResourcesModule",
    "__version__",
]
