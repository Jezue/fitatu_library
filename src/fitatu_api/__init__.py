"""Public package exports for fitatu-api."""

__version__ = "0.3.0"

from .api_client import (
    ActivitiesModule,
    AuthModule,
    CmsModule,
    DietPlanModule,
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuTokenStore,
    PlannerModule,
    ResourcesModule,
    UserSettingsModule,
    WaterModule,
)
from .constants import (
    FITATU_LIFECYCLE_HEALTHY,
    FITATU_LIFECYCLE_REAUTH_FAILED,
    FITATU_LIFECYCLE_REFRESH_ONLY,
    FITATU_LIFECYCLE_RELOGIN_REQUIRED,
    FITATU_LIFECYCLE_TOKEN_ONLY,
)
from .facade import FitatuLibrary
from .operational_store import FitatuOperationalEvent, FitatuOperationalStore

__all__ = [
    "FitatuLibrary",
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
    "FitatuOperationalStore",
    "FitatuOperationalEvent",
    "FITATU_LIFECYCLE_HEALTHY",
    "FITATU_LIFECYCLE_TOKEN_ONLY",
    "FITATU_LIFECYCLE_REFRESH_ONLY",
    "FITATU_LIFECYCLE_RELOGIN_REQUIRED",
    "FITATU_LIFECYCLE_REAUTH_FAILED",
    "__version__",
]
