"""Package constants for Fitatu API integration."""

DEFAULT_FITATU_API_BASE_URL = "https://pl-pl.fitatu.com/api"
FITATU_SESSION_CONTEXT_SCHEMA_VERSION = "1.1"

# Where `fitatu-api login` writes the session and FitatuApiClient.from_session() reads it,
# unless $FITATU_SESSION (or its alias $FITATU_SESSION_PATH) names another file.
DEFAULT_SESSION_PATH = "~/.fitatu/session.json"

# How the client identifies itself: as the Fitatu web app. API-Key and API-Secret are
# required on every call; the rest is sent the way the web app sends it. Sessions do not
# store these, so a fix here reaches every saved session.
WEB_CLIENT_PROFILE: dict[str, str] = {
    "api_key": "FITATU-MOBILE-APP",
    "api_secret": "PYRXtfs88UDJMuCCrNpLV",
    "app_uuid": "64c2d1b0-c8ad-11e8-8956-0242ac120008",
    "api_cluster": "pl-pl0",
    "app_os": "WEB",
    "app_version": "4.13.1",
    "user_agent": "Mozilla/5.0",
}
