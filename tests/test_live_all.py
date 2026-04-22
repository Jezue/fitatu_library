"""
Live integration tests against the real Fitatu API.

Guarded by FITATU_RUN_LIVE_TESTS=1. All write operations add items and
clean them up via pytest fixtures (yield-based teardown). No day is cleared
in bulk; no user profile is permanently modified.

Required env vars:
    FITATU_RUN_LIVE_TESTS=1
    FITATU_SESSION_PATH       Path to a JSON session file
    FITATU_LIVE_USER_ID       The Fitatu numeric user ID

Optional env vars:
    FITATU_LIVE_RECIPE_ID     Recipe ID for add-recipe tests   (default: skipped)
    FITATU_LIVE_PRODUCT_ID    Known product ID                 (default: searched live)
    FITATU_LIVE_MEAL_KEY      Meal slot for write tests        (default: snack)
    FITATU_LIVE_DAY           ISO date for planner tests       (default: yesterday)
    FITATU_LIVE_RUN_WRITE_RECIPE=1   Enable create_recipe test
    FITATU_LIVE_RUN_WRITE_PRODUCT=1  Enable create_product test
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from fitatu_api import FitatuApiClient, FitatuApiError, FitatuAuthContext, FitatuLibrary

# ---------------------------------------------------------------------------
# Guard & helpers
# ---------------------------------------------------------------------------

LIVE_TESTS_ENABLED = os.getenv("FITATU_RUN_LIVE_TESTS") == "1"

skip_unless_live = pytest.mark.skipif(
    not LIVE_TESTS_ENABLED, reason="Set FITATU_RUN_LIVE_TESTS=1 to run"
)

SEARCH_FALLBACK_PHRASE = "jajko kurze"


def _env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key, "").strip() or None
    return value if value is not None else default


def _require_env(key: str) -> str:
    value = _env(key)
    if not value:
        pytest.skip(f"Missing required env var: {key}")
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def meal_clean_slate(
    session_data: dict[str, Any],
) -> Generator[None, None, None]:
    """Clear the test meal at the start and end of the module to prevent state leakage."""
    if not LIVE_TESTS_ENABLED:
        yield
        return
    uid = _env("FITATU_LIVE_USER_ID")
    if not uid:
        yield
        return
    raw = _env("FITATU_LIVE_DAY")
    day = date.fromisoformat(raw) if raw else date.today() - timedelta(days=1)
    mk = _env("FITATU_LIVE_MEAL_KEY", "snack") or "snack"

    auth = FitatuAuthContext.from_session_data(session_data)
    with FitatuApiClient(auth=auth) as c:
        for kind in ("normal_item", "custom_recipe_item", "custom_add_item"):
            try:
                c.planner.remove_day_items_by_kind(uid, day, item_kind=kind, meal_key=mk)
            except FitatuApiError:
                pass
    time.sleep(1)  # Allow API cache to settle after cleanup
    yield
    auth2 = FitatuAuthContext.from_session_data(session_data)
    with FitatuApiClient(auth=auth2) as c:
        for kind in ("normal_item", "custom_recipe_item", "custom_add_item"):
            try:
                c.planner.remove_day_items_by_kind(uid, day, item_kind=kind, meal_key=mk)
            except FitatuApiError:
                pass


@pytest.fixture(scope="module")
def session_data() -> dict[str, Any]:
    if not LIVE_TESTS_ENABLED:
        pytest.skip("Set FITATU_RUN_LIVE_TESTS=1 to run")
    session_path = Path(_require_env("FITATU_SESSION_PATH"))
    if not session_path.exists():
        pytest.skip(f"Session file does not exist: {session_path}")
    return json.loads(session_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client(session_data: dict[str, Any]) -> Generator[FitatuApiClient, None, None]:
    auth = FitatuAuthContext.from_session_data(session_data)
    with FitatuApiClient(auth=auth) as api_client:
        yield api_client


@pytest.fixture(scope="module")
def user_id() -> str:
    return _require_env("FITATU_LIVE_USER_ID")


@pytest.fixture(scope="module")
def test_date() -> date:
    raw = _env("FITATU_LIVE_DAY")
    if raw:
        return date.fromisoformat(raw)
    return date.today() - timedelta(days=1)


@pytest.fixture(scope="module")
def meal_key() -> str:
    return _env("FITATU_LIVE_MEAL_KEY", "snack") or "snack"


@pytest.fixture(scope="module")
def known_product_id(client: FitatuApiClient) -> str:
    explicit = _env("FITATU_LIVE_PRODUCT_ID")
    if explicit:
        return explicit
    results = client.search_food(SEARCH_FALLBACK_PHRASE, limit=1)
    if not results:
        pytest.skip("search_food returned no results; cannot determine known_product_id")
    food_id = results[0].get("foodId") or results[0].get("id")
    if not food_id:
        pytest.skip("Search result missing foodId/id field")
    return str(food_id)


@pytest.fixture(scope="module")
def known_recipe_id(client: FitatuApiClient) -> str:
    from_env = _env("FITATU_LIVE_RECIPE_ID")
    if from_env:
        return from_env
    catalog = client.get_recipes_catalog()
    for category in (catalog or {}).get("categories", []):
        for recipe in category.get("recipes", []):
            if not recipe.get("premiumInRecipesCatalog"):
                return str(recipe["id"])
    pytest.skip("No non-premium recipe found in catalog")


@pytest.fixture(scope="module")
def facade(session_data: dict[str, Any]) -> FitatuLibrary:
    return FitatuLibrary(session_data=session_data)


# ---------------------------------------------------------------------------
# Function-scoped write+cleanup fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def added_product_item(
    client: FitatuApiClient,
    user_id: str,
    test_date: date,
    meal_key: str,
    known_product_id: str,
) -> Generator[dict[str, Any], None, None]:
    result = client.planner.add_product_to_day_meal(
        user_id,
        test_date,
        meal_type=meal_key,
        product_id=known_product_id,
        measure_id=1,
        measure_quantity=50,
        source="API",
    )
    assert result.get("ok"), f"Setup: add_product_to_day_meal failed: {result}"
    time.sleep(1)  # Allow API cache to settle before test reads the item
    yield result
    # Use remove_day_items_by_kind — more reliable than per-item removal when
    # multiple items with the same productId accumulate in the meal.
    try:
        client.planner.remove_day_items_by_kind(
            user_id, test_date, item_kind="normal_item", meal_key=meal_key
        )
    except FitatuApiError:
        pass
    time.sleep(0.8)  # Allow API cache to expire before next test reads the day


@pytest.fixture()
def added_custom_item(
    client: FitatuApiClient,
    user_id: str,
    test_date: date,
    meal_key: str,
) -> Generator[dict[str, Any], None, None]:
    result = client.planner.add_custom_item_to_day_meal(
        user_id,
        test_date,
        meal_type=meal_key,
        name="__live_test_custom_item__",
        calories=10,
        protein_g=1,
        fat_g=0.5,
        carbs_g=1,
        source="API",
    )
    assert result.get("ok"), f"Setup: add_custom_item_to_day_meal failed: {result}"
    time.sleep(1)
    yield result
    try:
        client.planner.remove_day_items_by_kind(
            user_id, test_date, item_kind="custom_recipe_item", meal_key=meal_key
        )
        client.planner.remove_day_items_by_kind(
            user_id, test_date, item_kind="custom_add_item", meal_key=meal_key
        )
    except FitatuApiError:
        pass
    time.sleep(0.8)


@pytest.fixture()
def added_recipe_item(
    client: FitatuApiClient,
    user_id: str,
    test_date: date,
    meal_key: str,
    known_recipe_id: str,
) -> Generator[dict[str, Any], None, None]:
    result = client.planner.add_recipe_to_day_meal(
        user_id,
        test_date,
        meal_type=meal_key,
        recipe_id=known_recipe_id,
        hydrate_from_recipe_details=True,
    )
    assert result.get("ok"), f"Setup: add_recipe_to_day_meal failed: {result}"
    yield result
    try:
        client.planner.remove_day_items_by_kind(
            user_id, test_date, item_kind="custom_recipe_item", meal_key=meal_key
        )
    except FitatuApiError:
        pass


# ---------------------------------------------------------------------------
# TestClientReadOnly
# ---------------------------------------------------------------------------


@skip_unless_live
class TestClientReadOnly:
    def test_search_food_returns_list(self, client: FitatuApiClient) -> None:
        results = client.search_food("kurczak", limit=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_food_items_have_required_fields(self, client: FitatuApiClient) -> None:
        results = client.search_food("jajko", limit=5)
        assert results
        first = results[0]
        assert first.get("foodId") or first.get("id"), f"Search result missing foodId/id: {first}"
        assert "name" in first, f"Search result missing 'name': {first}"

    def test_search_food_pagination(self, client: FitatuApiClient) -> None:
        page1 = client.search_food("ryż", limit=2, page=1)
        page2 = client.search_food("ryż", limit=2, page=2)
        assert isinstance(page1, list)
        assert isinstance(page2, list)

    def test_get_product_details(self, client: FitatuApiClient, known_product_id: str) -> None:
        details = client.get_product_details(known_product_id)
        assert isinstance(details, dict)
        assert details

    def test_get_product_details_has_measures(
        self, client: FitatuApiClient, known_product_id: str
    ) -> None:
        details = client.get_product_details(known_product_id)
        measures = details.get("measures")
        assert isinstance(measures, list), f"Expected 'measures' list, got: {type(measures)}"

    def test_get_recipes_catalog(self, client: FitatuApiClient) -> None:
        catalog = client.get_recipes_catalog()
        assert catalog is not None
        assert isinstance(catalog, (dict, list))

    def test_get_recipes_catalog_category(self, client: FitatuApiClient) -> None:
        catalog = client.get_recipes_catalog()
        categories = (catalog or {}).get("categories") if isinstance(catalog, dict) else []
        if not categories:
            pytest.skip("No categories in recipes catalog; cannot test category endpoint")
        category_id = categories[0].get("id")
        result = client.get_recipes_catalog_category(category_id)
        assert result is not None

    def test_get_recipe(self, client: FitatuApiClient, known_recipe_id: str) -> None:
        recipe = client.get_recipe(known_recipe_id)
        assert isinstance(recipe, dict)
        has_id = recipe.get("id") or recipe.get("recipeId")
        has_name = recipe.get("name") or recipe.get("title")
        assert has_id or has_name, f"Recipe missing id and name: {recipe}"

    def test_describe_auth_state(self, client: FitatuApiClient) -> None:
        state = client.describe_auth_state()
        assert isinstance(state, dict)
        assert "lifecycle_state" in state
        assert state.get("lifecycle_state") in {
            "healthy",
            "token_only",
            "refresh_only",
            "relogin_required",
            "reauth_failed",
        }

    def test_management_report(self, client: FitatuApiClient) -> None:
        report = client.management_report()
        assert isinstance(report, dict)
        assert "management_report_schema_version" in report
        assert "lifecycle_state" in report
        assert "modules" in report
        assert isinstance(report["modules"], list)
        assert len(report["modules"]) > 0


# ---------------------------------------------------------------------------
# TestClientUserAndSettings
# ---------------------------------------------------------------------------


@skip_unless_live
class TestClientUserAndSettings:
    def test_get_user(self, client: FitatuApiClient, user_id: str) -> None:
        profile = client.get_user(user_id)
        assert isinstance(profile, dict)
        assert profile

    def test_get_user_settings(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        settings = client.get_user_settings(user_id, day=test_date)
        assert isinstance(settings, dict)
        assert settings

    def test_get_user_settings_for_day(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        settings = client.get_user_settings_for_day(user_id, test_date)
        assert isinstance(settings, dict)
        assert settings

    @pytest.mark.xfail(reason="settings-new endpoint returns 405 for some accounts", strict=False)
    def test_get_user_settings_new(self, client: FitatuApiClient, user_id: str) -> None:
        settings_new = client.get_user_settings_new(user_id)
        assert isinstance(settings_new, dict)
        assert settings_new

    def test_get_diet_plan_settings(self, client: FitatuApiClient, user_id: str) -> None:
        settings = client.get_diet_plan_settings(user_id)
        assert isinstance(settings, dict)
        assert settings

    def test_get_water(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        water = client.get_water(user_id, test_date)
        assert water is None or isinstance(water, (dict, list))

    def test_get_activities_catalog(self, client: FitatuApiClient) -> None:
        catalog = client.get_activities_catalog()
        assert catalog is not None
        assert isinstance(catalog, (dict, list))

    def test_get_food_tags_recipe(self, client: FitatuApiClient) -> None:
        tags = client.get_food_tags_recipe()
        assert tags is not None


# ---------------------------------------------------------------------------
# TestProbeEndpoints
# ---------------------------------------------------------------------------


@skip_unless_live
class TestProbeEndpoints:
    def test_probe_known_endpoints_returns_list(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        results = client.probe_known_endpoints(user_id, test_date)
        assert isinstance(results, list)
        assert len(results) > 0
        for entry in results:
            assert "method" in entry
            assert "path" in entry
            assert "ok" in entry

    def test_probe_majority_succeed(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        results = client.probe_known_endpoints(user_id, test_date)
        ok_count = sum(1 for r in results if r.get("ok"))
        total = len(results)
        failed = [r for r in results if not r.get("ok")]
        assert ok_count >= total * 0.75, (
            f"Only {ok_count}/{total} probes succeeded. Failed: {failed}"
        )


# ---------------------------------------------------------------------------
# TestPlannerReadOnly
# ---------------------------------------------------------------------------


@skip_unless_live
class TestPlannerReadOnly:
    def test_get_day_returns_dict(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        day = client.planner.get_day(user_id, test_date)
        assert isinstance(day, dict)

    def test_get_day_has_diet_plan(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        day = client.planner.get_day(user_id, test_date)
        assert "dietPlan" in day, f"'dietPlan' not in day keys: {list(day.keys())}"
        assert isinstance(day["dietPlan"], dict)

    def test_get_meal_returns_dict(
        self, client: FitatuApiClient, user_id: str, test_date: date, meal_key: str
    ) -> None:
        meal = client.planner.get_meal(user_id, test_date, meal_key)
        assert isinstance(meal, dict)

    def test_list_meal_items_returns_list(
        self, client: FitatuApiClient, user_id: str, test_date: date, meal_key: str
    ) -> None:
        items = client.planner.list_meal_items(user_id, test_date, meal_key)
        assert isinstance(items, list)

    def test_find_meal_item_returns_none_for_nonexistent(
        self, client: FitatuApiClient, user_id: str, test_date: date, meal_key: str
    ) -> None:
        result = client.planner.find_meal_item(
            user_id, test_date, meal_key, "__nonexistent_xyz_9999__"
        )
        assert result is None

    def test_list_day_items_for_removal_returns_list(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        rows = client.planner.list_day_items_for_removal(user_id, test_date)
        assert isinstance(rows, list)
        for row in rows:
            assert "meal" in row
            assert "itemId" in row
            assert "resolvedKind" in row
            assert row["resolvedKind"] in {
                "normal_item",
                "custom_add_item",
                "custom_recipe_item",
            }

    def test_client_get_day_plan(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        day = client.get_day_plan(user_id, test_date)
        assert isinstance(day, dict)
        assert "dietPlan" in day


# ---------------------------------------------------------------------------
# TestPlannerResolveProductMeasure
# ---------------------------------------------------------------------------


@skip_unless_live
class TestPlannerResolveProductMeasure:
    def test_resolve_grams(
        self, client: FitatuApiClient, known_product_id: str
    ) -> None:
        result = client.planner.resolve_product_measure(
            product_id=known_product_id,
            requested_amount=100,
            requested_unit="g",
            strict_measure=False,
        )
        assert isinstance(result, dict)
        assert result.get("measureId") is not None
        assert result.get("measureQuantity") is not None
        assert result.get("strategy") is not None

    def test_resolve_includes_product_id(
        self, client: FitatuApiClient, known_product_id: str
    ) -> None:
        result = client.planner.resolve_product_measure(
            product_id=known_product_id,
            requested_amount=50,
            requested_unit="g",
            strict_measure=False,
        )
        assert str(result.get("productId")) == str(known_product_id)


# ---------------------------------------------------------------------------
# TestPlannerAddProduct
# ---------------------------------------------------------------------------


@skip_unless_live
class TestPlannerAddProduct:
    def test_add_product_returns_ok(self, added_product_item: dict[str, Any]) -> None:
        assert added_product_item.get("ok") is True

    def test_add_product_has_added_item(self, added_product_item: dict[str, Any]) -> None:
        added = added_product_item.get("addedItem")
        assert isinstance(added, dict)
        assert added.get("planDayDietItemId"), "addedItem must have planDayDietItemId"
        assert added.get("productId") is not None

    def test_add_product_appears_in_meal(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        added_id = str((added_product_item.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert added_id
        items = client.planner.list_meal_items(user_id, test_date, meal_key)
        found = any(str(i.get("planDayDietItemId")) == added_id for i in items)
        assert found, (
            f"Added item {added_id} not found in meal. "
            f"IDs: {[i.get('planDayDietItemId') for i in items]}"
        )

    def test_add_search_result_to_day_meal(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        result = client.planner.add_search_result_to_day_meal(
            user_id,
            test_date,
            meal_type=meal_key,
            phrase=SEARCH_FALLBACK_PHRASE,
            index=0,
            measure_quantity=1,
        )
        assert result.get("ok") is True
        item_id = str((result.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert item_id
        try:
            client.planner.remove_day_items_by_kind(
                user_id, test_date, item_kind="normal_item", meal_key=meal_key
            )
        except FitatuApiError:
            pass


# ---------------------------------------------------------------------------
# TestPlannerAddCustomItem
# ---------------------------------------------------------------------------


@skip_unless_live
class TestPlannerAddCustomItem:
    def test_add_custom_item_returns_ok(self, added_custom_item: dict[str, Any]) -> None:
        assert added_custom_item.get("ok") is True

    def test_add_custom_item_has_added_item(self, added_custom_item: dict[str, Any]) -> None:
        added = added_custom_item.get("addedItem")
        assert isinstance(added, dict)
        assert added.get("planDayDietItemId")
        assert added.get("name") == "__live_test_custom_item__"

    def test_add_custom_item_appears_in_meal(
        self,
        added_custom_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        added_id = str((added_custom_item.get("addedItem") or {}).get("planDayDietItemId") or "")
        time.sleep(0.5)  # Allow eventual consistency to settle
        items = client.planner.list_meal_items(user_id, test_date, meal_key)
        found = any(str(i.get("planDayDietItemId")) == added_id for i in items)
        assert found, f"Custom item {added_id} not found in meal"


# ---------------------------------------------------------------------------
# TestPlannerAddRecipe
# ---------------------------------------------------------------------------


@skip_unless_live
class TestPlannerAddRecipe:
    def test_add_recipe_returns_ok(self, added_recipe_item: dict[str, Any]) -> None:
        assert added_recipe_item.get("ok") is True

    def test_add_recipe_has_added_item(self, added_recipe_item: dict[str, Any]) -> None:
        added = added_recipe_item.get("addedItem")
        assert isinstance(added, dict)
        assert added.get("planDayDietItemId")

    def test_add_recipe_item_has_recipe_id(
        self, added_recipe_item: dict[str, Any], known_recipe_id: str
    ) -> None:
        added = added_recipe_item.get("addedItem") or {}
        assert str(added.get("recipeId")) == str(known_recipe_id)

    def test_add_recipe_appears_in_meal(
        self,
        added_recipe_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_recipe_id: str,
    ) -> None:
        items = client.planner.list_meal_items(user_id, test_date, meal_key)
        # Match by recipeId — planDayDietItemId may be reassigned by server
        matched = next(
            (i for i in items if str(i.get("recipeId")) == str(known_recipe_id) and i.get("deletedAt") is None),
            None,
        )
        assert matched is not None, f"Recipe {known_recipe_id} not found in meal items: {[i.get('recipeId') for i in items]}"


# ---------------------------------------------------------------------------
# TestPlannerUpdateItem
# ---------------------------------------------------------------------------


@skip_unless_live
class TestPlannerUpdateItem:
    def test_update_measure_quantity(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        item_id = str((added_product_item.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert item_id
        time.sleep(0.5)  # Allow API cache to settle before update reads back
        result = client.planner.update_day_item(
            user_id,
            test_date,
            meal_type=meal_key,
            item_id=item_id,
            measure_quantity=75,
        )
        assert isinstance(result, dict)
        assert "ok" in result and "after" in result

    def test_update_eaten_flag(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        item_id = str((added_product_item.get("addedItem") or {}).get("planDayDietItemId") or "")
        result = client.planner.update_day_item(
            user_id,
            test_date,
            meal_type=meal_key,
            item_id=item_id,
            eaten=True,
        )
        assert isinstance(result, dict)
        assert "ok" in result and "after" in result


# ---------------------------------------------------------------------------
# TestPlannerRemoveItem
# ---------------------------------------------------------------------------


@skip_unless_live
class TestPlannerRemoveItem:
    def test_classify_day_item_for_removal(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        tentative_id = str((added_product_item.get("addedItem") or {}).get("planDayDietItemId") or "")
        # Server may reassign the client-generated UUID — resolve the real ID from the meal.
        time.sleep(0.5)
        live_items = client.planner.list_meal_items(user_id, test_date, meal_key)
        item_id = tentative_id
        if live_items and not any(str(i.get("planDayDietItemId")) == tentative_id for i in live_items):
            # UUID was reassigned; use any active item in the meal
            active = [i for i in live_items if i.get("deletedAt") is None and float(i.get("measureQuantity") or 0) > 0]
            if active:
                item_id = str(active[0].get("planDayDietItemId") or "")
        if not item_id:
            pytest.skip("No item found in meal to classify")
        classification = client.planner.classify_day_item_for_removal(
            user_id, test_date, meal_key, item_id
        )
        assert isinstance(classification, dict)
        assert classification.get("found") is True, f"classify returned found=False for id={item_id}: {classification}"
        assert classification.get("resolvedKind") in {
            "normal_item",
            "custom_add_item",
            "custom_recipe_item",
        }
        assert classification.get("item") is not None

    def test_remove_product_with_strategy(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_product_id: str,
    ) -> None:
        add_result = client.planner.add_product_to_day_meal(
            user_id,
            test_date,
            meal_type=meal_key,
            product_id=known_product_id,
            measure_id=1,
            measure_quantity=10,
            source="API",
        )
        assert add_result.get("ok")
        item_id = str((add_result.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert item_id
        time.sleep(1)

        remove_result = client.planner.remove_day_item_with_strategy(
            user_id, test_date, meal_key, item_id, item_kind="auto"
        )
        assert isinstance(remove_result, dict)
        # Note: ok=False is expected for normal_item when snapshot removal is unreliable.
        # We verify the item is actually gone from the meal rather than relying on ok flag.
        time.sleep(0.5)
        items_after = client.planner.list_meal_items(user_id, test_date, meal_key)
        item_still_present = any(
            str(i.get("planDayDietItemId")) == item_id
            and float(i.get("measureQuantity") or 1) > 0.05
            and i.get("deletedAt") is None
            for i in items_after
        )
        assert not item_still_present, (
            f"Item {item_id} still present after remove_day_item_with_strategy: {remove_result}"
        )
        # Cleanup any residual items
        try:
            client.planner.remove_day_items_by_kind(
                user_id, test_date, item_kind="normal_item", meal_key=meal_key
            )
        except FitatuApiError:
            pass
        time.sleep(0.8)  # Allow API cache to settle before next test

    def test_remove_custom_item_with_strategy(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        add_result = client.planner.add_custom_item_to_day_meal(
            user_id,
            test_date,
            meal_type=meal_key,
            name="__remove_test_item__",
            calories=5,
            protein_g=0,
            fat_g=0,
            carbs_g=1,
        )
        assert add_result.get("ok")
        item_id = str((add_result.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert item_id
        time.sleep(1)

        remove_result = client.planner.remove_day_item_with_strategy(
            user_id, test_date, meal_key, item_id, item_kind="custom_recipe_item"
        )
        assert isinstance(remove_result, dict)
        # Verify removal by checking item is gone from meal (ok flag may be unreliable)
        time.sleep(0.5)
        items_after = client.planner.list_meal_items(user_id, test_date, meal_key)
        item_still_present = any(
            str(i.get("planDayDietItemId")) == item_id
            and i.get("deletedAt") is None
            for i in items_after
        )
        assert not item_still_present, f"Custom item {item_id} still present after removal: {remove_result}"
        # Cleanup any residual custom items
        try:
            client.planner.remove_day_items_by_kind(
                user_id, test_date, item_kind="custom_recipe_item", meal_key=meal_key
            )
            client.planner.remove_day_items_by_kind(
                user_id, test_date, item_kind="custom_add_item", meal_key=meal_key
            )
        except FitatuApiError:
            pass
        time.sleep(0.8)  # Allow API cache to settle before next test

    def test_remove_day_item(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_product_id: str,
    ) -> None:
        add_result = client.planner.add_product_to_day_meal(
            user_id,
            test_date,
            meal_type=meal_key,
            product_id=known_product_id,
            measure_id=1,
            measure_quantity=5,
            source="API",
        )
        assert add_result.get("ok")
        tentative_id = str((add_result.get("addedItem") or {}).get("planDayDietItemId") or "")
        time.sleep(1)
        # Resolve real item ID in case server reassigned the UUID
        live_items = client.planner.list_meal_items(user_id, test_date, meal_key)
        item_id = tentative_id
        if live_items and not any(str(i.get("planDayDietItemId")) == tentative_id for i in live_items):
            active = [i for i in live_items if i.get("deletedAt") is None and float(i.get("measureQuantity") or 0) > 0]
            if active:
                item_id = str(active[0].get("planDayDietItemId") or "")
        result = client.planner.remove_day_item(user_id, test_date, meal_key, item_id)
        assert isinstance(result, dict)
        # Verify removal by checking item is gone (ok flag unreliable when UUID was reassigned)
        time.sleep(0.5)
        items_after = client.planner.list_meal_items(user_id, test_date, meal_key)
        item_still_present = any(
            str(i.get("planDayDietItemId")) == item_id
            and i.get("deletedAt") is None
            and float(i.get("measureQuantity") or 0) > 0.05
            for i in items_after
        )
        assert not item_still_present, f"Item {item_id} still present after remove_day_item: {result}"
        # Ensure full cleanup
        try:
            client.planner.remove_day_items_by_kind(
                user_id, test_date, item_kind="normal_item", meal_key=meal_key
            )
        except FitatuApiError:
            pass


# ---------------------------------------------------------------------------
# TestServiceModulesReadOnly
# ---------------------------------------------------------------------------


@skip_unless_live
class TestServiceModulesReadOnly:
    def test_user_settings_get_profile(
        self, client: FitatuApiClient, user_id: str
    ) -> None:
        profile = client.user_settings.get_profile(user_id)
        assert isinstance(profile, dict)
        assert profile

    def test_user_settings_get(self, client: FitatuApiClient, user_id: str) -> None:
        settings = client.user_settings.get(user_id)
        assert isinstance(settings, dict)

    def test_user_settings_get_for_day(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        settings = client.user_settings.get_for_day(user_id, test_date)
        assert isinstance(settings, dict)

    @pytest.mark.xfail(reason="settings-new endpoint returns 405 for some accounts", strict=False)
    def test_user_settings_get_new(self, client: FitatuApiClient, user_id: str) -> None:
        settings_new = client.user_settings.get_new(user_id)
        assert isinstance(settings_new, dict)

    def test_user_settings_get_firebase_token(
        self, client: FitatuApiClient, user_id: str
    ) -> None:
        # Result may be None or a token value; verify the call doesn't raise
        client.user_settings.get_firebase_token(user_id)

    def test_diet_plan_get_settings(self, client: FitatuApiClient, user_id: str) -> None:
        settings = client.diet_plan.get_settings(user_id)
        assert isinstance(settings, dict)
        assert settings

    def test_diet_plan_get_meal_schema(self, client: FitatuApiClient, user_id: str) -> None:
        schema = client.diet_plan.get_meal_schema(user_id)
        assert schema is not None

    def test_diet_plan_get_default_meal_schema(
        self, client: FitatuApiClient, user_id: str
    ) -> None:
        schema = client.diet_plan.get_default_meal_schema(user_id)
        assert schema is not None

    def test_water_get_day(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        result = client.water.get_day(user_id, test_date)
        assert result is None or isinstance(result, (dict, list))

    def test_water_set_day(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        before = client.water.get_day(user_id, test_date)
        original = int(((before or {}).get("water") or {}).get("waterConsumption") or 0)
        result = client.water.set_day(user_id, test_date, original)
        assert isinstance(result, dict)
        after = client.water.get_day(user_id, test_date)
        assert int(((after or {}).get("water") or {}).get("waterConsumption") or 0) == original

    def test_water_add_intake(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        before = client.water.get_day(user_id, test_date)
        original = int(((before or {}).get("water") or {}).get("waterConsumption") or 0)
        client.water.add_intake(user_id, test_date, 250)
        after = client.water.get_day(user_id, test_date)
        added = int(((after or {}).get("water") or {}).get("waterConsumption") or 0)
        assert added == original + 250
        # Restore
        client.water.set_day(user_id, test_date, original)

    def test_activities_get_catalog(self, client: FitatuApiClient) -> None:
        catalog = client.activities.get_catalog()
        assert catalog is not None
        assert isinstance(catalog, (dict, list))

    def test_resources_get_food_tags_recipe(self, client: FitatuApiClient) -> None:
        tags = client.resources.get_food_tags_recipe()
        assert tags is not None

    def test_cms_graphql_simple_query(self, client: FitatuApiClient) -> None:
        result = client.cms.graphql("{ __typename }")
        assert result is not None


# ---------------------------------------------------------------------------
# TestServiceModulesWrite
# ---------------------------------------------------------------------------


@skip_unless_live
class TestServiceModulesWrite:
    def test_update_system_info_roundtrip(
        self, client: FitatuApiClient, user_id: str
    ) -> None:
        result = client.user_settings.update_system_info(
            user_id,
            app_version="4.13.1",
            system_info="FITATU-WEB",
        )
        assert isinstance(result, dict)

    def test_update_water_settings_roundtrip(
        self, client: FitatuApiClient, user_id: str
    ) -> None:
        try:
            settings_new = client.user_settings.get_new(user_id)
            current_unit_capacity = int((settings_new.get("waterSettings") or {}).get("unitCapacity") or 250)
        except FitatuApiError:
            current_unit_capacity = 250

        result = client.user_settings.update_water_settings(
            user_id, unit_capacity=current_unit_capacity
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestAuthRefresh
# ---------------------------------------------------------------------------


@skip_unless_live
class TestAuthRefresh:
    def test_refresh_access_token(self, client: FitatuApiClient) -> None:
        if not client.auth.refresh_token:
            pytest.skip("No refresh_token in session; skipping refresh test")
        result = client.refresh_access_token()
        assert isinstance(result, dict)
        assert result.get("status") == "ok", f"Token refresh failed: {result}"
        assert result.get("token"), "refresh result missing 'token'"
        assert client.auth.bearer_token == result.get("token")

    def test_describe_auth_state_after_refresh(self, client: FitatuApiClient) -> None:
        state = client.describe_auth_state()
        assert state.get("has_bearer_token") is True


# ---------------------------------------------------------------------------
# TestClientWriteRecipe (opt-in)
# ---------------------------------------------------------------------------


@skip_unless_live
class TestClientWriteRecipe:
    @pytest.fixture(autouse=True)
    def require_write_recipe(self) -> None:
        if os.getenv("FITATU_LIVE_RUN_WRITE_RECIPE") != "1":
            pytest.skip("Set FITATU_LIVE_RUN_WRITE_RECIPE=1 to run recipe-creation tests")

    def test_create_recipe_returns_dict(
        self,
        client: FitatuApiClient,
        known_product_id: str,
    ) -> None:
        product_details = client.get_product_details(known_product_id)
        measures = product_details.get("measures") or []
        measure_id = measures[0].get("id") if measures else 1

        result = client.create_recipe(
            name="__live_test_recipe_DO_NOT_USE__",
            items=[
                {
                    "itemId": known_product_id,
                    "measureId": measure_id,
                    "measureQuantity": 1,
                    "type": "PRODUCT",
                }
            ],
            recipe_description="1. Live test - safe to delete",
            serving="1",
            shared=False,
        )
        assert isinstance(result, dict)
        assert result


# ---------------------------------------------------------------------------
# TestClientWriteProduct (opt-in)
# ---------------------------------------------------------------------------


@skip_unless_live
class TestClientWriteProduct:
    @pytest.fixture(autouse=True)
    def require_write_product(self) -> None:
        if os.getenv("FITATU_LIVE_RUN_WRITE_PRODUCT") != "1":
            pytest.skip("Set FITATU_LIVE_RUN_WRITE_PRODUCT=1 to run product-creation tests")

    def test_create_product_returns_dict(self, client: FitatuApiClient) -> None:
        result = client.create_product(
            name="__live_test_product_DO_NOT_USE__",
            brand="TestBrand",
            energy=10,
            protein=1,
            fat=0,
            carbohydrate=1,
        )
        assert isinstance(result, dict)
        assert result


# ---------------------------------------------------------------------------
# TestFacadeReadOnly
# ---------------------------------------------------------------------------


@skip_unless_live
class TestFacadeReadOnly:
    def test_describe_session(self, facade: FitatuLibrary) -> None:
        state = facade.describe_session()
        assert isinstance(state, dict)
        assert "lifecycle_state" in state

    def test_export_session_context(self, facade: FitatuLibrary) -> None:
        ctx = facade.export_session_context()
        assert isinstance(ctx, dict)
        assert "api_cluster" in ctx

    def test_management_report(self, facade: FitatuLibrary) -> None:
        report = facade.management_report()
        assert isinstance(report, dict)
        assert "management_report_schema_version" in report

    def test_search_food(self, facade: FitatuLibrary) -> None:
        results = facade.search_food("jajko", limit=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_get_recipes_catalog_via_api(self, facade: FitatuLibrary) -> None:
        result = facade.get_recipes_catalog_via_api()
        assert isinstance(result, dict)
        assert result.get("status") == "ok"

    def test_get_recipe_via_api(self, facade: FitatuLibrary, known_recipe_id: str) -> None:
        result = facade.get_recipe_via_api(recipe_id=known_recipe_id)
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        assert result.get("result") is not None


# ---------------------------------------------------------------------------
# TestFacadeWritePlanner
# ---------------------------------------------------------------------------


@skip_unless_live
class TestFacadeWritePlanner:
    def test_add_custom_item_and_remove(
        self,
        facade: FitatuLibrary,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        add_result = facade.add_custom_item_to_day_meal_via_api(
            target_date=test_date,
            meal_key=meal_key,
            name="__facade_live_test__",
            calories=5,
            protein_g=0,
            fat_g=0,
            carbs_g=1,
            user_id=user_id,
        )
        assert add_result.get("status") == "ok", f"Facade add_custom_item failed: {add_result}"
        inner = add_result.get("result") or {}
        item_id = str((inner.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert item_id

        time.sleep(2)  # allow backend sync to propagate before remove
        remove_result = facade.remove_day_item_via_api(
            target_date=test_date,
            meal_key=meal_key,
            item_id=item_id,
            user_id=user_id,
        )
        assert remove_result.get("status") == "ok", f"Facade remove failed: {remove_result}"

    def test_add_product_and_remove(
        self,
        facade: FitatuLibrary,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_product_id: str,
    ) -> None:
        add_result = facade.add_product_to_day_meal_via_api(
            target_date=test_date,
            meal_key=meal_key,
            product_id=known_product_id,
            measure_id=1,
            measure_quantity=10,
            user_id=user_id,
        )
        assert add_result.get("status") == "ok", f"Facade add_product failed: {add_result}"
        inner = add_result.get("result") or {}
        item_id = str((inner.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert item_id

        time.sleep(2)  # allow backend sync to propagate before remove
        remove_result = facade.remove_day_item_via_api(
            target_date=test_date,
            meal_key=meal_key,
            item_id=item_id,
            user_id=user_id,
        )
        assert remove_result.get("status") == "ok", f"Facade remove failed: {remove_result}"

    def test_add_recipe_and_remove(
        self,
        facade: FitatuLibrary,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_recipe_id: str,
    ) -> None:
        add_result = facade.add_recipe_to_day_meal_via_api(
            target_date=test_date,
            meal_key=meal_key,
            recipe_id=known_recipe_id,
            user_id=user_id,
        )
        assert add_result.get("status") == "ok", f"Facade add_recipe failed: {add_result}"
        inner = add_result.get("result") or {}
        item_id = str((inner.get("addedItem") or {}).get("planDayDietItemId") or "")
        assert item_id

        time.sleep(2)  # allow backend sync to propagate before remove
        remove_result = facade.remove_day_item_via_api(
            target_date=test_date,
            meal_key=meal_key,
            item_id=item_id,
            user_id=user_id,
        )
        assert remove_result.get("status") == "ok", f"Facade remove recipe failed: {remove_result}"


# ---------------------------------------------------------------------------
# TestFullCycleAddRemove
# ---------------------------------------------------------------------------


@skip_unless_live
class TestFullCycleAddRemove:
    """Add product + custom item + recipe, then remove all and verify meal is empty."""

    def test_full_cycle(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_product_id: str,
        known_recipe_id: str,
    ) -> None:
        # --- Add product ---
        r_product = client.planner.add_product_to_day_meal(
            user_id, test_date, meal_type=meal_key,
            product_id=known_product_id, measure_id=1, measure_quantity=100, source="API",
        )
        assert r_product.get("ok"), f"add product failed: {r_product}"
        time.sleep(2)  # Let snapshot propagate before next add

        # --- Add custom item ---
        r_custom = client.planner.add_custom_item_to_day_meal(
            user_id, test_date, meal_type=meal_key,
            name="__full_cycle_test__", calories=10, protein_g=1, fat_g=0, carbs_g=1,
        )
        assert r_custom.get("ok"), f"add custom failed: {r_custom}"
        time.sleep(2)  # Let snapshot propagate before next add

        # --- Add recipe ---
        r_recipe = client.planner.add_recipe_to_day_meal(
            user_id, test_date, meal_type=meal_key,
            recipe_id=known_recipe_id, hydrate_from_recipe_details=True,
        )
        assert r_recipe.get("ok"), f"add recipe failed: {r_recipe}"

        # Let snapshot propagate before verify - polling instead of hard sleep
        for _ in range(5):
            time.sleep(2)
            items_before = client.planner.list_meal_items(user_id, test_date, meal_key)
            active_before = [i for i in items_before if i.get("deletedAt") is None and float(i.get("measureQuantity") or 0) > 0]
            if len(active_before) >= 3:
                break
        assert len(active_before) >= 3, f"Expected >=3 items before removal, got {len(active_before)}"

        # --- Remove product (normal_item strategy) ---
        client.planner.remove_day_items_by_kind(user_id, test_date, item_kind="normal_item", meal_key=meal_key)
        time.sleep(0.8)

        # --- Remove custom item (custom_recipe_item + custom_add_item strategy) ---
        for kind in ("custom_recipe_item", "custom_add_item"):
            try:
                client.planner.remove_day_items_by_kind(user_id, test_date, item_kind=kind, meal_key=meal_key)
            except FitatuApiError:
                pass
        time.sleep(0.8)

        # --- Verify meal is empty ---
        items_after = client.planner.list_meal_items(user_id, test_date, meal_key)
        active_after = [
            i for i in items_after
            if i.get("deletedAt") is None and float(i.get("measureQuantity") or 0) > 0.05
        ]
        assert len(active_after) == 0, (
            f"Meal not empty after removing all items. Remaining: "
            f"{[(i.get('planDayDietItemId'), i.get('productId'), i.get('recipeId')) for i in active_after]}"
        )
