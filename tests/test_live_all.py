"""Live tests against the real Fitatu API, for the account in your session file.

Skipped unless FITATU_RUN_LIVE_TESTS=1. The session is the one `fitatu-api login` writes
(~/.fitatu/session.json, or the file in $FITATU_SESSION / $FITATU_SESSION_PATH), loaded
with FitatuApiClient.from_session(), so refreshed tokens are written back to it; with
$FITATU_SESSION_JSON (`fitatu-api login --print`) they stay in memory.

Classes whose name ends in ReadOnly only read: `-k ReadOnly` runs just them, as the weekly
Live contract workflow does with $FITATU_SESSION_JSON. Assertion messages name what failed,
never what the account holds, because that workflow's logs are public (it also runs pytest
with --assert=plain, as its assertion rewriting would print the compared values).

What a run changes on the account, and how it is put back:

- Food rows and one activity on FITATU_LIVE_DAY. Each test deletes exactly the rows it
  added (by row id) and fails when one is left behind; other rows on that day are not
  touched. A day on or before today is refused.
- Water on FITATU_LIVE_DAY: changed, then set back to the value read before (a day that
  had no water entry is left with 0 ml).
- The note of FITATU_LIVE_DAY: written, then set back to the one read before.
- A body weight and measurements on FITATU_LIVE_MEASUREMENT_DAY, only when that date has
  none: written, then deleted.
- The bearer token: test_refresh_access_token gets a new one, which is saved to the
  session file. Fitatu does not rotate refresh tokens, so the refresh token and older
  bearer tokens keep working.
- Recently used foods: logging the catalog product and recipe puts them in the account's
  recent foods (and first in its own search hits) and raises their use counts. This is
  not put back.

With FITATU_LIVE_RUN_WRITE_ACCOUNT=1 also:

- the water glass size in settings: changed, then set back to the value read before;
- a product in Fitatu's shared food catalog and a private recipe: created, then deleted;
- a private recipe made with the tools: created, logged on FITATU_LIVE_DAY, removed and
  deleted. Recipes are deleted softly: Fitatu keeps them, marked deleted, out of your foods.

Optional env vars:
    FITATU_LIVE_DAY              YYYY-MM-DD after today       (default: a year from today)
    FITATU_LIVE_MEAL_KEY         meal the write tests use      (default: snack)
    FITATU_LIVE_PRODUCT_ID       catalog product to log        (default: first PRODUCT hit for "jajko kurze")
    FITATU_LIVE_RECIPE_ID        catalog recipe to log         (default: first free recipe in the catalog)
    FITATU_LIVE_MEASUREMENT_DAY  YYYY-MM-DD with no measurement (default: 2020-01-01)
    FITATU_LIVE_RUN_WRITE_ACCOUNT=1  also run the account writes listed above
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import uuid
from collections.abc import Generator
from datetime import date, timedelta
from typing import Any

import pytest

from fitatu_api import FitatuApiClient, FitatuApiError, FitatuAuthError, FitatuLibrary, FitatuTools
from fitatu_api.agent import TOOLS
from fitatu_api.planner import _derived_row_id, _new_row_id

pytestmark = pytest.mark.skipif(
    os.getenv("FITATU_RUN_LIVE_TESTS") != "1", reason="Set FITATU_RUN_LIVE_TESTS=1 to run"
)

SEARCH_FALLBACK_PHRASE = "jajko kurze"
TEST_PRODUCT_NAME = "__fitatu_api_live_test_product__"
TEST_RECIPE_NAME = "__fitatu_api_live_test_recipe__"
TOOLS_RECIPE_NAME = "fitatu api live test recipe"  # words, for get_my_foods to find it by


def _env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, "").strip() or default


def _require_account_writes() -> None:
    if os.getenv("FITATU_LIVE_RUN_WRITE_ACCOUNT") != "1":
        pytest.skip("Set FITATU_LIVE_RUN_WRITE_ACCOUNT=1 to change settings and the shared catalog")


def _new_row(added_rows: list[str]) -> str:
    """A row id for an add, registered for cleanup before the add is sent."""
    row_id = _new_row_id()
    added_rows.append(row_id)
    return row_id


def _row_ids(client: FitatuApiClient, user_id: str, day: date) -> set[str]:
    return {str(row.get("planDayDietItemId")) for row in _all_rows(client, user_id, day)}


def _all_rows(client: FitatuApiClient, user_id: str, day: date) -> list[dict[str, Any]]:
    diet_plan = client.planner.get_day(user_id, day).get("dietPlan") or {}
    return [row for bucket in diet_plan.values() for row in (bucket or {}).get("items") or []]


def _water_ml(client: FitatuApiClient, user_id: str, day: date) -> int:
    return int(((client.water.get_day(user_id, day) or {}).get("water") or {}).get("waterConsumption") or 0)


def _assert_deleted(client: FitatuApiClient, path: str, what: str) -> None:
    """Fail unless ``GET path`` now answers 404 or, for a soft delete, ``"deleted": true``.

    Fitatu deletes recipes softly (checked live): the GET still answers 200, marked deleted.
    Product details carry ``deleted`` as well, so a product may go the same way.
    """
    try:
        reply = client.request("GET", path)
    except FitatuApiError as exc:
        if exc.status_code == 404:
            return
        pytest.fail(f"cleanup check failed (status {exc.status_code}); make sure {what} is deleted", pytrace=False)
    if isinstance(reply, dict) and reply.get("deleted") is True:
        return
    pytest.fail(f"cleanup failed: {what} is still there after DELETE; delete it by hand", pytrace=False)


def _why(result: Any) -> str:
    """Why a result is not ok, for an assertion message: its error type or reason, never its values.

    A run's log may be shared (or be a public CI log), so no message here quotes what the account holds.
    """
    if not isinstance(result, dict):
        return f"a {type(result).__name__}"
    error = result["error"] if isinstance(result.get("error"), dict) else result
    return str(error.get("error_type") or error.get("reason") or f"a result with keys {sorted(result)}")


def _is_iso_date(text: Any) -> bool:
    """Whether ``text`` is YYYY-MM-DD; unlike date.fromisoformat, a failure does not quote it."""
    return isinstance(text, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is not None


def _ok(result: dict[str, Any]) -> Any:
    """The data of a successful FitatuTools.call() result."""
    json.dumps(result)  # what an agent gets back must be JSON
    assert result["ok"], _why(result)
    return result["data"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> Generator[FitatuApiClient, None, None]:
    try:
        api_client = FitatuApiClient.from_session()
    except FitatuAuthError as exc:
        pytest.skip(str(exc))
    with api_client:
        yield api_client


@pytest.fixture(scope="module")
def user_id(client: FitatuApiClient) -> str:
    uid = client.auth.fitatu_user_id
    if not uid:
        pytest.skip("The session has no Fitatu user id; run `fitatu-api login`")
    return uid


@pytest.fixture(scope="module")
def test_date() -> date:
    raw = _env("FITATU_LIVE_DAY")
    day = date.fromisoformat(raw) if raw else date.today() + timedelta(days=365)
    if day <= date.today():
        pytest.fail(f"FITATU_LIVE_DAY={raw} is not after today; the write tests would change your diary", pytrace=False)
    return day


@pytest.fixture(scope="module")
def meal_key() -> str:
    return _env("FITATU_LIVE_MEAL_KEY", "snack") or "snack"


@pytest.fixture(scope="module")
def known_product_id(client: FitatuApiClient) -> str:
    explicit = _env("FITATU_LIVE_PRODUCT_ID")
    if explicit:
        return explicit
    hits = client.search_food(SEARCH_FALLBACK_PHRASE, limit=5)
    hit = next((row for row in hits if row.get("type") == "PRODUCT" and row.get("foodId")), None)
    if hit is None:
        pytest.skip(f"No PRODUCT hit for {SEARCH_FALLBACK_PHRASE!r}; set FITATU_LIVE_PRODUCT_ID")
    return str(hit["foodId"])


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
def facade() -> Generator[FitatuLibrary, None, None]:
    # The skip sits inside pytest.warns: it is a BaseException, which warns lets through, while a
    # FitatuAuthError raised before the warning would turn into "DID NOT WARN".
    with pytest.warns(DeprecationWarning, match="FitatuTools"):  # deprecated, still tested until 1.0
        try:
            library = FitatuLibrary.from_session()
        except FitatuAuthError as exc:
            pytest.skip(str(exc))
    with library:
        yield library


@pytest.fixture(scope="module")
def tools(client: FitatuApiClient) -> FitatuTools:
    return FitatuTools(client)


@pytest.fixture()
def added_rows(client: FitatuApiClient, user_id: str, test_date: date) -> Generator[list[str], None, None]:
    """Row ids a test adds on the live day; teardown deletes exactly those and fails if one stays."""
    row_ids: list[str] = []
    yield row_ids
    if row_ids:
        # Ids the test already removed are reported as missing, which is fine; failed ones are still there.
        result = client.planner.remove_day_items_by_kind(
            user_id, test_date, item_kind="auto", all_meals=True, row_ids=row_ids
        )
        left = [attempt["item_id"] for attempt in result["attempts"] if not attempt["ok"]]
        assert not left, f"cleanup failed: delete rows {left} on {test_date} by hand"


@pytest.fixture()
def added_product_item(
    client: FitatuApiClient,
    user_id: str,
    test_date: date,
    meal_key: str,
    known_product_id: str,
    added_rows: list[str],
) -> dict[str, Any]:
    result = client.planner.add_product_to_day_meal(
        user_id,
        test_date,
        meal_type=meal_key,
        product_id=known_product_id,
        measure_id=1,
        measure_quantity=50,
        row_id=_new_row(added_rows),
    )
    assert result["ok"], f"Setup: add_product_to_day_meal failed: {_why(result)}"
    return result


@pytest.fixture()
def added_custom_item(
    client: FitatuApiClient,
    user_id: str,
    test_date: date,
    meal_key: str,
    added_rows: list[str],
) -> dict[str, Any]:
    result = client.planner.add_custom_item_to_day_meal(
        user_id,
        test_date,
        meal_type=meal_key,
        name="__live_test_custom_item__",
        calories=10,
        protein_g=1,
        fat_g=0.5,
        carbs_g=1,
        row_id=_new_row(added_rows),
    )
    assert result["ok"], f"Setup: add_custom_item_to_day_meal failed: {_why(result)}"
    return result


@pytest.fixture()
def added_recipe_item(
    client: FitatuApiClient,
    user_id: str,
    test_date: date,
    meal_key: str,
    known_recipe_id: str,
    added_rows: list[str],
) -> dict[str, Any]:
    result = client.planner.add_recipe_to_day_meal(
        user_id,
        test_date,
        meal_type=meal_key,
        recipe_id=known_recipe_id,
        row_id=_new_row(added_rows),
    )
    assert result["ok"], f"Setup: add_recipe_to_day_meal failed: {_why(result)}"
    return result


# ---------------------------------------------------------------------------
# Client reads
# ---------------------------------------------------------------------------


class TestClientReadOnly:
    def test_search_food_returns_list(self, client: FitatuApiClient) -> None:
        results = client.search_food("kurczak", limit=3)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_food_items_have_required_fields(self, client: FitatuApiClient) -> None:
        results = client.search_food("jajko", limit=5)
        assert results
        first = results[0]
        assert first.get("foodId"), "a search hit has no foodId"
        assert "name" in first, "a search hit has no name"

    def test_search_food_pagination(self, client: FitatuApiClient) -> None:
        page1 = client.search_food("ryż", limit=2, page=1)
        page2 = client.search_food("ryż", limit=2, page=2)
        assert isinstance(page1, list)
        assert isinstance(page2, list)

    def test_get_product_details(self, client: FitatuApiClient, known_product_id: str) -> None:
        details = client.get_product_details(known_product_id)
        assert isinstance(details, dict)
        assert details

    def test_get_product_details_has_measures(self, client: FitatuApiClient, known_product_id: str) -> None:
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
        assert has_id or has_name, "the recipe has neither an id nor a name"

    def test_describe_auth_state(self, client: FitatuApiClient) -> None:
        state = client.describe_auth_state()
        assert isinstance(state, dict)
        assert state["has_bearer_token"] is True


# ---------------------------------------------------------------------------
# Planner reads
# ---------------------------------------------------------------------------


class TestPlannerReadOnly:
    def test_get_day_returns_dict(self, client: FitatuApiClient, user_id: str, test_date: date) -> None:
        day = client.planner.get_day(user_id, test_date)
        assert isinstance(day, dict)

    def test_get_day_has_diet_plan(self, client: FitatuApiClient, user_id: str, test_date: date) -> None:
        day = client.planner.get_day(user_id, test_date)
        assert "dietPlan" in day, f"'dietPlan' not in day keys: {list(day.keys())}"
        assert isinstance(day["dietPlan"], dict)

    def test_get_meal_returns_dict(
        self, client: FitatuApiClient, user_id: str, test_date: date, meal_key: str
    ) -> None:
        meal = client.planner.get_meal(user_id, test_date, meal_type=meal_key)
        assert isinstance(meal, dict)

    def test_list_meal_items_returns_list(
        self, client: FitatuApiClient, user_id: str, test_date: date, meal_key: str
    ) -> None:
        items = client.planner.list_meal_items(user_id, test_date, meal_type=meal_key)
        assert isinstance(items, list)

    def test_find_meal_item_returns_none_for_nonexistent(
        self, client: FitatuApiClient, user_id: str, test_date: date, meal_key: str
    ) -> None:
        result = client.planner.find_meal_item(
            user_id, test_date, meal_type=meal_key, query="__nonexistent_xyz_9999__"
        )
        assert result is None

    def test_list_day_items_for_removal_returns_list(
        self, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        rows = client.planner.list_day_items_for_removal(user_id, test_date)
        assert isinstance(rows, list)
        for row in rows:
            assert "meal" in row
            assert "item_id" in row
            assert row["resolved_kind"] in {"normal_item", "custom_add_item", "custom_recipe_item"}


class TestPlannerResolveProductMeasureReadOnly:
    def test_resolve_grams(self, client: FitatuApiClient, known_product_id: str) -> None:
        result = client.planner.resolve_product_measure(
            product_id=known_product_id,
            requested_amount=100,
            requested_unit="g",
            strict_measure=False,
        )
        assert isinstance(result, dict)
        assert result.get("measure_id") is not None
        assert result.get("measure_quantity") is not None
        assert result.get("strategy") is not None

    def test_resolve_includes_product_id(self, client: FitatuApiClient, known_product_id: str) -> None:
        result = client.planner.resolve_product_measure(
            product_id=known_product_id,
            requested_amount=50,
            requested_unit="g",
            strict_measure=False,
        )
        assert str(result.get("product_id")) == str(known_product_id)


# ---------------------------------------------------------------------------
# Planner writes on the live day (every added row is deleted by added_rows)
# ---------------------------------------------------------------------------


class TestPlannerAddProduct:
    def test_add_product_has_added_item(self, added_product_item: dict[str, Any]) -> None:
        added = added_product_item["added_item"]
        assert added["planDayDietItemId"] == added_product_item["row_id"]
        assert added.get("productId") is not None

    def test_add_product_appears_in_meal(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        items = client.planner.list_meal_items(user_id, test_date, meal_type=meal_key)
        ids = [str(i.get("planDayDietItemId")) for i in items]
        assert added_product_item["row_id"] in ids, "the added row is not in the meal"

    def test_add_search_result_to_day_meal(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        added_rows: list[str],
    ) -> None:
        result = client.planner.add_search_result_to_day_meal(
            user_id,
            test_date,
            meal_type=meal_key,
            phrase=SEARCH_FALLBACK_PHRASE,
            index=0,
            measure_quantity=1,
            row_id=_new_row(added_rows),
        )
        assert result["ok"] is True
        assert result["row_id"] in _row_ids(client, user_id, test_date)


class TestPlannerAddCustomItem:
    def test_add_custom_item_has_added_item(self, added_custom_item: dict[str, Any]) -> None:
        added = added_custom_item["added_item"]
        assert added["planDayDietItemId"] == added_custom_item["row_id"]
        assert added.get("name") == "__live_test_custom_item__"

    def test_add_custom_item_appears_in_meal(
        self,
        added_custom_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        items = client.planner.list_meal_items(user_id, test_date, meal_type=meal_key)
        assert any(str(i.get("planDayDietItemId")) == added_custom_item["row_id"] for i in items)


class TestPlannerAddRecipe:
    def test_add_recipe_item_has_recipe_id(self, added_recipe_item: dict[str, Any], known_recipe_id: str) -> None:
        assert str(added_recipe_item["added_item"].get("recipeId")) == str(known_recipe_id)

    def test_add_recipe_appears_in_meal(
        self,
        added_recipe_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_recipe_id: str,
    ) -> None:
        items = client.planner.list_meal_items(user_id, test_date, meal_type=meal_key)
        matched = next((i for i in items if str(i.get("planDayDietItemId")) == added_recipe_item["row_id"]), None)
        assert matched is not None, "the recipe row is not in the meal"
        assert str(matched.get("recipeId")) == str(known_recipe_id)


class TestPlannerUpdateItem:
    def test_update_measure_quantity(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        result = client.planner.update_day_item(
            user_id, test_date, meal_type=meal_key, item_id=added_product_item["row_id"], measure_quantity=75
        )
        assert result["ok"], _why(result)

    def test_update_eaten_flag(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        result = client.planner.update_day_item(
            user_id, test_date, meal_type=meal_key, item_id=added_product_item["row_id"], eaten=True
        )
        assert result["ok"], _why(result)


class TestPlannerRemoveItem:
    def test_classify_day_item_for_removal(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        classification = client.planner.classify_day_item_for_removal(
            user_id, test_date, meal_type=meal_key, item_id=added_product_item["row_id"]
        )
        assert classification["found"] is True
        assert classification["resolved_kind"] == "normal_item"
        assert classification["item"] is not None

    def test_remove_custom_item(
        self,
        added_custom_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        row_id = added_custom_item["row_id"]
        result = client.planner.remove_day_item(user_id, test_date, meal_type=meal_key, item_id=row_id)
        assert result["ok"], _why(result)
        assert row_id not in _row_ids(client, user_id, test_date)

    def test_remove_day_item(
        self,
        added_product_item: dict[str, Any],
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
    ) -> None:
        row_id = added_product_item["row_id"]
        result = client.planner.remove_day_item(user_id, test_date, meal_type=meal_key, item_id=row_id)
        assert result["ok"], _why(result)
        assert row_id not in _row_ids(client, user_id, test_date)


class TestFullCycleAddRemove:
    """Add a product, a custom row and a recipe, then remove the three by row id in one call."""

    def test_full_cycle(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_product_id: str,
        known_recipe_id: str,
        added_rows: list[str],
    ) -> None:
        planner = client.planner
        added = [
            planner.add_product_to_day_meal(
                user_id, test_date, meal_type=meal_key, product_id=known_product_id,
                measure_id=1, measure_quantity=100, row_id=_new_row(added_rows),
            ),
            planner.add_custom_item_to_day_meal(
                user_id, test_date, meal_type=meal_key, name="__full_cycle_test__",
                calories=10, protein_g=1, fat_g=0, carbs_g=1, row_id=_new_row(added_rows),
            ),
            planner.add_recipe_to_day_meal(
                user_id, test_date, meal_type=meal_key, recipe_id=known_recipe_id, row_id=_new_row(added_rows),
            ),
        ]
        assert all(result["ok"] for result in added), [_why(result) for result in added]
        assert set(added_rows) <= _row_ids(client, user_id, test_date)

        removed = planner.remove_day_items_by_kind(
            user_id, test_date, item_kind="auto", meal_type=meal_key, row_ids=list(added_rows)
        )
        assert removed["ok"], _why(removed)
        assert not set(added_rows) & _row_ids(client, user_id, test_date)


class TestEatenFlagSurvivesSync:
    def test_adding_an_item_keeps_eaten_on_existing_items(
        self,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        added_product_item: dict[str, Any],
        added_rows: list[str],
    ) -> None:
        row_id = added_product_item["row_id"]
        client.planner.update_day_item(user_id, test_date, meal_type=meal_key, item_id=row_id, eaten=True)
        client.planner.add_custom_item_to_day_meal(
            user_id, test_date, meal_type=meal_key,
            name="__eaten_regression__", calories=1, protein_g=0, fat_g=0, carbs_g=0,
            row_id=_new_row(added_rows),
        )
        target = next(i for i in _all_rows(client, user_id, test_date) if i.get("planDayDietItemId") == row_id)
        assert target.get("eaten") is True


# ---------------------------------------------------------------------------
# Service modules
# ---------------------------------------------------------------------------


class TestServiceModulesReadOnly:
    def test_user_settings_get_profile(self, client: FitatuApiClient, user_id: str) -> None:
        profile = client.user_settings.get_profile(user_id)
        assert isinstance(profile, dict)
        assert profile

    def test_user_settings_get(self, client: FitatuApiClient, user_id: str) -> None:
        with pytest.warns(DeprecationWarning):
            settings = client.user_settings.get(user_id)
        assert isinstance(settings, dict)
        assert settings

    def test_user_settings_get_for_day(self, client: FitatuApiClient, user_id: str, test_date: date) -> None:
        with pytest.warns(DeprecationWarning):
            settings = client.user_settings.get_for_day(user_id, test_date)
        assert isinstance(settings, dict)
        assert settings

    def test_user_settings_get_new(self, client: FitatuApiClient, user_id: str) -> None:
        settings_new = client.user_settings.get_new(user_id)
        assert isinstance(settings_new, dict)
        assert settings_new

    def test_diet_plan_get_settings(self, client: FitatuApiClient, user_id: str) -> None:
        with pytest.warns(DeprecationWarning):
            settings = client.diet_plan.get_settings(user_id)
        assert isinstance(settings, dict)
        assert settings

    def test_diet_plan_get_meal_schema(self, client: FitatuApiClient, user_id: str) -> None:
        with pytest.warns(DeprecationWarning):
            schema = client.diet_plan.get_meal_schema(user_id)
        assert schema is not None

    def test_diet_plan_get_default_meal_schema(self, client: FitatuApiClient, user_id: str) -> None:
        with pytest.warns(DeprecationWarning):
            schema = client.diet_plan.get_default_meal_schema(user_id)
        assert schema is not None

    def test_water_get_day(self, client: FitatuApiClient, user_id: str, test_date: date) -> None:
        result = client.water.get_day(user_id, test_date)
        assert result is None or isinstance(result, (dict, list))

    def test_activities_get_catalog(self, client: FitatuApiClient) -> None:
        catalog = client.activities.get_catalog()
        assert catalog is not None
        assert isinstance(catalog, (dict, list))

    def test_resources_get_food_tags_recipe(self, client: FitatuApiClient) -> None:
        tags = client.resources.get_food_tags_recipe()
        assert tags is not None


class TestWaterOnTheLiveDay:
    def test_water_set_day(self, client: FitatuApiClient, user_id: str, test_date: date) -> None:
        original = _water_ml(client, user_id, test_date)
        try:
            client.water.set_day(user_id, test_date, original + 100)
            assert _water_ml(client, user_id, test_date) == original + 100
        finally:
            client.water.set_day(user_id, test_date, original)
            restored = _water_ml(client, user_id, test_date)
            assert restored == original, f"cleanup failed: set water on {test_date} back to {original} ml by hand"

    def test_water_add_intake(self, client: FitatuApiClient, user_id: str, test_date: date) -> None:
        original = _water_ml(client, user_id, test_date)
        try:
            client.water.add_intake(user_id, test_date, 250)
            assert _water_ml(client, user_id, test_date) == original + 250
        finally:
            client.water.set_day(user_id, test_date, original)
            restored = _water_ml(client, user_id, test_date)
            assert restored == original, f"cleanup failed: set water on {test_date} back to {original} ml by hand"


class TestAuthRefresh:
    def test_refresh_access_token(self, client: FitatuApiClient) -> None:
        if not client.auth.refresh_token:
            pytest.skip("No refresh_token in session; skipping refresh test")
        before = client.auth.bearer_token
        result = client.refresh_access_token()  # raises when the refresh fails
        assert result["has_bearer_token"] is True
        assert client.auth.bearer_token and client.auth.bearer_token != before
        assert client.auth.bearer_token not in json.dumps(result)

    def test_describe_auth_state_after_refresh(self, client: FitatuApiClient) -> None:
        state = client.describe_auth_state()
        assert state.get("has_bearer_token") is True


# ---------------------------------------------------------------------------
# Account writes (FITATU_LIVE_RUN_WRITE_ACCOUNT=1): settings and the food catalog
# ---------------------------------------------------------------------------


class TestAccountWrites:
    @pytest.fixture(autouse=True)
    def require_account_writes(self) -> None:
        _require_account_writes()

    def test_water_settings_round_trip(self, client: FitatuApiClient, user_id: str) -> None:
        def unit_capacity() -> Any:
            return (client.user_settings.get_new(user_id).get("waterSettings") or {}).get("unitCapacity")

        original = unit_capacity()
        if not isinstance(original, int) or isinstance(original, bool):
            pytest.skip(f"settings-new has no integer waterSettings.unitCapacity (a {type(original).__name__})")
        changed = 300 if original != 300 else 250
        try:
            client.user_settings.update_water_settings(user_id, unit_capacity=changed)
            assert unit_capacity() == changed
        finally:
            client.user_settings.update_water_settings(user_id, unit_capacity=original)
            assert unit_capacity() == original, f"cleanup failed: set the water glass size back to {original} ml"

    def test_create_and_delete_product(self, client: FitatuApiClient) -> None:
        created = client.create_product(
            name=TEST_PRODUCT_NAME, brand="fitatu-api live test", energy=10, protein=1, fat=0, carbohydrate=1
        )
        product_id = created.get("id")
        assert product_id, f"POST /products returned no id; delete {TEST_PRODUCT_NAME!r} by hand"
        try:
            assert client.get_product_details(product_id)
        finally:
            client.delete_product(product_id)
            _assert_deleted(client, f"/products/{int(product_id)}", f"product {product_id} {TEST_PRODUCT_NAME!r}")

    def test_create_and_delete_recipe(self, client: FitatuApiClient, known_product_id: str) -> None:
        measures = client.get_product_details(known_product_id).get("measures") or []
        created = client.create_recipe(
            name=TEST_RECIPE_NAME,
            items=[
                {
                    "itemId": known_product_id,
                    "measureId": measures[0].get("id") if measures else 1,
                    "measureQuantity": 1,
                    "type": "PRODUCT",
                }
            ],
            recipe_description="1. Live test - safe to delete",
            serving="1",
            shared=False,
        )
        recipe_id = created.get("id")
        assert recipe_id, f"POST /recipes returned no id; delete {TEST_RECIPE_NAME!r} by hand"
        try:
            assert client.get_recipe(recipe_id)
        finally:
            client.delete_recipe(recipe_id)
            _assert_deleted(client, f"/recipes/{int(recipe_id)}", f"recipe {recipe_id} {TEST_RECIPE_NAME!r}")


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class TestFacadeReadOnly:
    def test_describe_session(self, facade: FitatuLibrary) -> None:
        state = facade.describe_session()
        assert isinstance(state, dict)
        assert state["has_bearer_token"] is True

    def test_export_session_context(self, facade: FitatuLibrary) -> None:
        ctx = facade.export_session_context()
        assert isinstance(ctx, dict)
        assert "app_searchlocale" in ctx
        assert "bearer_token" not in ctx

    def test_search_food(self, facade: FitatuLibrary) -> None:
        result = facade.search_food_via_api(phrase="jajko", limit=3)
        assert result["status"] == "ok", _why(result)
        assert len(result["result"]) > 0

    def test_get_recipes_catalog_via_api(self, facade: FitatuLibrary) -> None:
        result = facade.get_recipes_catalog_via_api()
        assert result.get("status") == "ok", _why(result)

    def test_get_recipe_via_api(self, facade: FitatuLibrary, known_recipe_id: str) -> None:
        result = facade.get_recipe_via_api(recipe_id=known_recipe_id)
        assert result.get("status") == "ok", _why(result)
        assert result.get("result") is not None


class TestFacadeWritePlanner:
    def test_add_custom_item_and_remove(
        self, facade: FitatuLibrary, test_date: date, meal_key: str, added_rows: list[str]
    ) -> None:
        add_result = facade.add_custom_item_to_day_meal_via_api(
            target_date=test_date,
            meal_type=meal_key,
            name="__facade_live_test__",
            calories=5,
            protein_g=0,
            fat_g=0,
            carbs_g=1,
            row_id=_new_row(added_rows),
        )
        assert add_result["status"] == "ok", _why(add_result)
        remove_result = facade.remove_day_item_via_api(
            target_date=test_date, meal_type=meal_key, item_id=add_result["result"]["row_id"]
        )
        assert remove_result["status"] == "ok", _why(remove_result)

    def test_add_product_and_remove(
        self,
        facade: FitatuLibrary,
        test_date: date,
        meal_key: str,
        known_product_id: str,
        added_rows: list[str],
    ) -> None:
        add_result = facade.add_product_to_day_meal_via_api(
            target_date=test_date,
            meal_type=meal_key,
            product_id=known_product_id,
            measure_id=1,
            measure_quantity=10,
            row_id=_new_row(added_rows),
        )
        assert add_result["status"] == "ok", _why(add_result)
        remove_result = facade.remove_day_item_via_api(
            target_date=test_date, meal_type=meal_key, item_id=add_result["result"]["row_id"]
        )
        assert remove_result["status"] == "ok", _why(remove_result)

    def test_add_recipe_and_remove(
        self,
        facade: FitatuLibrary,
        test_date: date,
        meal_key: str,
        known_recipe_id: str,
        added_rows: list[str],
    ) -> None:
        add_result = facade.add_recipe_to_day_meal_via_api(
            target_date=test_date, meal_type=meal_key, recipe_id=known_recipe_id, row_id=_new_row(added_rows)
        )
        assert add_result["status"] == "ok", _why(add_result)
        remove_result = facade.remove_day_item_via_api(
            target_date=test_date, meal_type=meal_key, item_id=add_result["result"]["row_id"]
        )
        assert remove_result["status"] == "ok", _why(remove_result)


# ---------------------------------------------------------------------------
# Agent tools (FitatuTools.call, as an agent calls them)
# ---------------------------------------------------------------------------


class TestToolsReadOnly:
    def test_every_read_tool(self, tools: FitatuTools, client: FitatuApiClient, test_date: date, known_product_id: str) -> None:
        today = date.today()
        barcode = client.get_product_details(known_product_id).get("barcode")
        arguments: dict[str, dict[str, Any]] = {
            "get_today": {},
            "get_day": {"date": test_date.isoformat()},
            "get_targets": {"date": today.isoformat()},
            "get_range_summary": {"from_date": (today - timedelta(days=6)).isoformat(), "to_date": today.isoformat()},
            "get_progress": {"from_date": (today - timedelta(days=6)).isoformat(), "to_date": today.isoformat()},
            "get_top_foods": {"from_date": (today - timedelta(days=6)).isoformat(), "to_date": today.isoformat()},
            "get_report": {"from_date": (today - timedelta(days=6)).isoformat(), "to_date": today.isoformat()},
            "get_shopping_list": {"from_date": test_date.isoformat(), "to_date": test_date.isoformat()},
            "search_food": {"phrase": "jajko", "limit": 3},
            "search_recipes": {"phrase": "kurczak", "max_energy_kcal": 500, "limit": 3},
            "find_by_barcode": {"barcode": str(barcode or "5901234123457")},
            "get_food_measures": {"food_id": int(known_product_id), "food_type": "PRODUCT"},
            "get_recent_foods": {"meal": "breakfast"},
            "get_favorite_foods": {},
            "get_my_foods": {},
            "get_weight_history": {"limit": 5},
            "get_body_measurements": {"from_date": (today - timedelta(days=365)).isoformat(), "to_date": today.isoformat()},
            "get_water": {"date": test_date.isoformat()},
            "search_activity": {"phrase": "bieganie", "limit": 3},
            "get_fasting": {"from_date": (today - timedelta(days=6)).isoformat(), "to_date": today.isoformat()},
            "get_habits": {},
            "get_good_days": {"from_date": (today - timedelta(days=30)).isoformat()},
            "preview_removal": {"date": test_date.isoformat(), "row_ids": ["not-a-row"]},
        }
        assert set(arguments) == {tool["name"] for tool in TOOLS if tool["annotations"]["readOnlyHint"]}
        results = {name: tools.call(name, args) for name, args in arguments.items()}
        json.dumps(results)
        if not barcode:  # the product has none; the catalog holds junk codes, so the example EAN may be someone's
            found = results.pop("find_by_barcode")
            assert found["ok"] or found["error"]["error_type"] == "not_found", _why(found)
        failed = {name: _why(result) for name, result in results.items() if not result["ok"]}
        assert not failed, failed
        if importlib.util.find_spec("mcp"):  # MCP hosts check results against these schemas; name where one does not fit, never the value
            from jsonschema import Draft202012Validator

            from fitatu_api.mcp_server import output_schema

            misfits = [
                f"{name}: /{'/'.join(map(str, error.absolute_path))} ({error.validator})"
                for name, result in results.items()
                for error in Draft202012Validator(output_schema(name)).iter_errors(result)
            ]
            assert not misfits, misfits


class TestTools:
    def test_add_update_move_and_remove_food(
        self,
        tools: FitatuTools,
        client: FitatuApiClient,
        user_id: str,
        test_date: date,
        meal_key: str,
        known_product_id: str,
        added_rows: list[str],
    ) -> None:
        day = test_date.isoformat()
        food = {"food_id": int(known_product_id), "food_type": "PRODUCT"}
        measure_id = _ok(tools.call("get_food_measures", food))["measures"][0]["measure_id"]
        row_id = _new_row(added_rows)
        added = _ok(tools.call("add_food", {"date": day, "meal": meal_key, **food, "quantity": 1, "measure_id": measure_id, "row_id": row_id}))
        assert (added["meal"], added["row_id"], added["measure_id"], added["ok"]) == (meal_key, row_id, measure_id, True)

        updated = _ok(tools.call("update_item", {"date": day, "row_id": row_id, "eaten": True}))
        assert updated["ok"] and updated["previous"] == {"quantity": 1, "eaten": False}

        other_meal = "breakfast" if meal_key != "breakfast" else "snack"
        before = _row_ids(client, user_id, test_date)
        try:
            moved = _ok(tools.call("move_item", {"date": day, "row_id": row_id, "to_meal": other_meal}))
        finally:
            # The copy gets its id inside the planner; register it even when the call fails.
            added_rows.extend(_row_ids(client, user_id, test_date) - before)
        assert moved["ok"] and moved["meal"] == other_meal

        removed = _ok(tools.call("remove_items", {"date": day, "row_ids": [moved["row_id"]]}))
        assert removed["ok"] and [(row["row_id"], row["food_id"]) for row in removed["removed"]] == [(moved["row_id"], int(known_product_id))]
        day_rows = {item["row_id"] for meal in _ok(tools.call("get_day", {"date": day}))["meals"] for item in meal["items"]}
        assert not {row_id, moved["row_id"]} & day_rows

    def test_add_and_remove_custom_item(
        self, tools: FitatuTools, test_date: date, meal_key: str, added_rows: list[str]
    ) -> None:
        day = test_date.isoformat()
        row_id = _new_row(added_rows)
        added = _ok(tools.call("add_custom_item", {
            "date": day, "meal": meal_key, "name": "__tools_live_test__",
            "energy_kcal": 10, "protein_g": 1, "fat_g": 0, "carbohydrate_g": 1, "eaten": True, "row_id": row_id,
        }))
        assert added["ok"], _why(added)  # also marked eaten
        removed = _ok(tools.call("remove_items", {"date": day, "row_ids": [row_id]}))["removed"]
        assert [(row["row_id"], row["name"], row["energy"]) for row in removed] == [(row_id, "__tools_live_test__", 10)]

    def test_add_foods_then_copy_them_to_another_meal(
        self, tools: FitatuTools, test_date: date, meal_key: str, known_product_id: str, known_recipe_id: str, added_rows: list[str]
    ) -> None:
        day = test_date.isoformat()
        measures = _ok(tools.call("get_food_measures", {"food_id": int(known_product_id), "food_type": "PRODUCT"}))["measures"]
        items = [
            {"food_id": int(known_product_id), "food_type": "PRODUCT", "quantity": 2, "measure_id": measures[0]["measure_id"]},
            {"food_id": int(known_recipe_id), "food_type": "RECIPE", "quantity": 1},
        ]
        # Both calls derive their rows' ids from row_id, so the teardown knows them before anything is sent.
        seed, copy_seed = _new_row_id(), _new_row_id()
        rows = [_derived_row_id(uuid.UUID(seed), index) for index in range(len(items))]
        copies = [_derived_row_id(uuid.UUID(copy_seed), index) for index in range(len(items))]
        added_rows.extend(rows + copies)

        added = _ok(tools.call("add_foods", {"date": day, "meal": meal_key, "items": items, "eaten": False, "row_id": seed}))
        assert added["ok"] and [row["row_id"] for row in added["rows"]] == rows, "add_foods did not return the seeded row ids"
        other_meal = "breakfast" if meal_key != "breakfast" else "snack"
        copied = _ok(tools.call("copy_meal", {
            "from_date": day, "from_meal": meal_key, "to_date": day, "to_meal": other_meal, "row_ids": rows, "row_id": copy_seed,
        }))
        assert copied["ok"] and sorted(copied["row_ids"]) == sorted(copies) and not copied["skipped"], "copy_meal did not copy both rows"

        meals = {meal["meal_key"]: meal["items"] for meal in _ok(tools.call("get_day", {"date": day}))["meals"]}

        def logged(meal: str, ids: list[str]) -> list[tuple[Any, ...]]:
            fields = ("food_type", "product_id", "recipe_id", "measure_id", "measure_quantity", "eaten")
            return sorted(tuple(item[field] for field in fields) for item in meals[meal] if item["row_id"] in ids)

        assert len(logged(meal_key, rows)) == 2, "add_foods did not log both foods"
        assert logged(other_meal, copies) == logged(meal_key, rows), "the copies differ from the rows they copy"
        removed = _ok(tools.call("remove_items", {"date": day, "row_ids": rows + copies}))
        assert removed["ok"] and len(removed["removed"]) == 4, "remove_items did not remove the four rows"

    def test_log_weight_and_measurements_on_an_empty_day(self, tools: FitatuTools, client: FitatuApiClient, user_id: str) -> None:
        day = date.fromisoformat(_env("FITATU_LIVE_MEASUREMENT_DAY", "2020-01-01") or "2020-01-01")
        if client.measurements.get_day(user_id, day) is not None:
            pytest.skip(f"{day} already has a measurement; set FITATU_LIVE_MEASUREMENT_DAY")
        try:
            assert _ok(tools.call("log_weight", {"date": day.isoformat(), "weight_kg": 70.5}))["ok"]
            logged = _ok(tools.call("log_measurements", {"date": day.isoformat(), "waist": 80, "fat_percentage": 20}))
            assert logged["ok"] and logged["size_unit"] == client.user_settings.get_profile(user_id).get("sizeUnit"), "log_measurements used another size unit"
            stored = client.measurements.get_day(user_id, day) or {}
            # log_measurements keeps the day's weight, as its description says.
            values = tuple(None if stored.get(key) is None else float(stored[key]) for key in ("weight", "waist", "fatPercentage"))
            assert values == (70.5, 80.0, 20.0), "the day does not hold the logged weight, waist and body fat"
        finally:
            client.measurements.delete_day(user_id, day)
            assert client.measurements.get_day(user_id, day) is None, f"cleanup failed: delete the measurement on {day} by hand"

    def test_note_written_and_put_back(self, tools: FitatuTools, test_date: date) -> None:
        day = test_date.isoformat()
        before = _ok(tools.call("get_day", {"date": day}))
        try:
            written = _ok(tools.call("set_note", {"date": day, "note": "__tools_live_test__"}))
            assert written["ok"] and written["previous"] == (before["note"] or ""), "the note was not written, or previous is not the old note"
            after = _ok(tools.call("get_day", {"date": day}))
            assert (after["note"], after["tags_ids"], after["meals"]) == ("__tools_live_test__", before["tags_ids"], before["meals"])
        finally:
            tools.set_note(day, before["note"])
        assert _ok(tools.call("get_day", {"date": day}))["note"] == before["note"]

    def test_private_recipe_created_logged_and_deleted(
        self, tools: FitatuTools, client: FitatuApiClient, test_date: date, meal_key: str, known_product_id: str, added_rows: list[str]
    ) -> None:
        _require_account_writes()
        day = test_date.isoformat()
        ingredient = {"food_id": int(known_product_id), "food_type": "PRODUCT", "quantity": 100, "unit": "g"}
        created = _ok(tools.call("create_recipe", {"name": TOOLS_RECIPE_NAME, "ingredients": [ingredient], "servings": 2}))
        recipe_id = created["recipe_id"]
        try:
            assert created["ok"] and created["per_serving"]["measure_id"] == 39, "create_recipe did not confirm, or a serving is not measure 39"
            # update_recipe is beta: the PUT is the app's; twice the servings halve a serving's energy
            updated = _ok(tools.call("update_recipe", {"recipe_id": recipe_id, "servings": 4, "description": "__tools_live_test__"}))
            assert updated["previous"]["servings"] == 2 and updated["previous"]["ingredients"][0]["food_id"] == int(known_product_id), "previous does not undo the update"
            assert abs(updated["per_serving"]["energy_kcal"] - created["per_serving"]["energy_kcal"] / 2) < 1, "twice the servings did not halve a serving's energy"
            assert client.get_recipe(recipe_id).get("recipeDescription") == "__tools_live_test__"
            mine = _ok(tools.call("get_my_foods", {"phrase": TOOLS_RECIPE_NAME}))
            assert (recipe_id, "RECIPE") in {(hit["food_id"], hit["type"]) for hit in mine}, "the recipe is not in get_my_foods"
            row_id = _new_row(added_rows)
            added = _ok(tools.call("add_food", {
                "date": day, "meal": meal_key, "food_id": recipe_id, "food_type": "RECIPE", "quantity": 1, "row_id": row_id,
            }))
            assert added["ok"], _why(added)
            removed = _ok(tools.call("remove_items", {"date": day, "row_ids": [row_id]}))["removed"]
            assert [(row["row_id"], row["food_id"]) for row in removed] == [(row_id, recipe_id)]
        finally:
            deleted = tools.call("delete_recipe", {"recipe_id": recipe_id})
            _assert_deleted(client, f"/recipes/{int(recipe_id)}", f"recipe {recipe_id} {TOOLS_RECIPE_NAME!r}")
        assert _ok(deleted) == {"recipe_id": recipe_id, "name": TOOLS_RECIPE_NAME, "already_deleted": False, "ok": True}
        assert _ok(tools.call("delete_recipe", {"recipe_id": recipe_id}))["already_deleted"] is True
        assert recipe_id not in {hit["food_id"] for hit in _ok(tools.call("get_my_foods", {"phrase": TOOLS_RECIPE_NAME}))}

    def test_add_and_remove_activity(
        self, tools: FitatuTools, client: FitatuApiClient, user_id: str, test_date: date
    ) -> None:
        day = test_date.isoformat()
        hits = _ok(tools.call("search_activity", {"phrase": "bieganie", "limit": 1}))
        if not hits:
            pytest.skip("No activity found for 'bieganie'")
        row_id = _new_row_id()
        try:
            added = _ok(tools.call("add_activity", {
                "date": day, "activity_id": hits[0]["activity_id"], "minutes": 30, "energy_kcal": 123, "row_id": row_id,
            }))
            assert added["ok"], _why(added)
            assert _ok(tools.call("remove_activity", {"date": day, "row_id": row_id}))["ok"]
        finally:
            client.planner.remove_activity(user_id, test_date, item_id=row_id)  # item_not_found once removed
            left = {activity["row_id"] for activity in tools.get_day(day)["activities"]}
            assert row_id not in left, f"cleanup failed: delete activity {row_id} on {day} by hand"


# ---------------------------------------------------------------------------
# Newer read-only endpoints and the measurement round trip
# ---------------------------------------------------------------------------


class TestNewReadOnlyEndpoints:
    def test_settings_new_for_a_day(self, client: FitatuApiClient, user_id: str) -> None:
        settings = client.user_settings.get_new(user_id, date.today())
        assert "userDietSettings" in settings

    def test_day_revisions(self, client: FitatuApiClient, user_id: str) -> None:
        assert isinstance(client.planner.get_day_revisions(user_id, date.today()), list)

    def test_measurement_reads(self, client: FitatuApiClient, user_id: str) -> None:
        start, end = date.today() - timedelta(days=365), date.today()
        assert isinstance(client.measurements.get_weight_history(user_id, from_date=start, to_date=end), list)
        assert "weights" in client.measurements.get_weight_chart(user_id, from_date=start, to_date=end)
        assert isinstance(client.measurements.get_size_summary(user_id, from_date=start, to_date=end), dict)

    def test_range_summaries(self, client: FitatuApiClient, user_id: str) -> None:
        start, end = date.today() - timedelta(days=6), date.today()
        assert "energy" in client.summary.get_nutrition(user_id, start, end)
        assert isinstance(client.summary.get_nutrient_by_day(user_id, "protein", start, end), dict)
        top = client.summary.get_top_products(user_id, "protein", start, end, limit=3)
        assert isinstance(top.get("list"), list) and isinstance(top.get("count"), int), "the top foods are not {list, count}"
        assert isinstance(client.summary.get_activities(user_id, start, end), dict)

    def test_export_diary(self, client: FitatuApiClient, user_id: str) -> None:
        content = client.summary.export_diary(user_id, date.today() - timedelta(days=6), date.today())
        assert content is None or content.startswith(b"Date")

    def test_recent_and_favourite_foods(self, client: FitatuApiClient, user_id: str) -> None:
        assert isinstance(client.foods.get_recent(user_id, "breakfast"), list)
        assert isinstance(client.foods.get_recent_own(user_id, limit=5), list)
        assert isinstance(client.foods.get_favorites(user_id), list)

    def test_activity_search(self, client: FitatuApiClient, user_id: str) -> None:
        assert isinstance(client.activities.search("bieganie", limit=2), list)
        assert isinstance(client.activities.get_recent(user_id), list)

    def test_premium_status_is_a_flag_and_a_date(self, client: FitatuApiClient, user_id: str) -> None:
        # Never print the raw reply: it has prices and transaction ids. The dates are read as YYYY-MM-DD...
        premium = client.user_settings.get_premium(user_id)
        assert set(premium) == {"active", "end_date"} and isinstance(premium["active"], bool)
        assert premium["end_date"] is None or _is_iso_date(premium["end_date"]), "end_date is not YYYY-MM-DD"
        dates = [plan.get(key) for plan in client.request("GET", f"/users/{user_id}/subscriptions")["subscriptions"] for key in ("startDate", "endDate")]
        assert all(day is None or _is_iso_date(str(day)[:10]) for day in dates), "the subscription dates are not YYYY-MM-DD..."

    def test_fasting_and_habits(self, client: FitatuApiClient, user_id: str) -> None:
        symbols = {scheme.get("symbol") for scheme in client.fasting.get_types(user_id)}
        assert {"16_08_hours", "5_2_days", "custom"} <= symbols, "the fasting schemes lack a symbol seen live"
        assert isinstance(client.fasting.get_summary(user_id, date.today() - timedelta(days=6), date.today()), list)
        habits = client.habits.get_catalog(user_id)
        assert habits and all({"id", "name", "length", "premium"} <= set(habit) for habit in habits), "a habit lacks a field get_habits reads"
        assert isinstance(client.habits.get_user_habits(user_id), list)
        assert all(_is_iso_date(day) for day in client.habits.get_good_days(user_id)), "a good day is not YYYY-MM-DD"

    def test_recipe_search_keeps_to_the_energy_per_portion(self, client: FitatuApiClient) -> None:
        hits = client.search_recipes("kurczak", max_energy=500, limit=10)
        assert hits, "no recipe for 'kurczak' up to 500 kcal"
        assert all(hit.get("type") == "RECIPE" and float(hit["energy"]) <= 500 for hit in hits), "a hit is not a recipe of at most 500 kcal a portion"

    def test_daily_targets_via_facade(self, facade: FitatuLibrary) -> None:
        result = facade.get_daily_targets_via_api()
        assert result["status"] == "ok", _why(result)
        assert result["result"]["energy"]


class TestMeasurementWriteRoundTrip:
    def test_set_and_delete_on_an_empty_day(self, client: FitatuApiClient, user_id: str) -> None:
        day = date.fromisoformat(_env("FITATU_LIVE_MEASUREMENT_DAY", "2020-01-01") or "2020-01-01")
        if client.measurements.get_day(user_id, day) is not None:
            pytest.skip(f"{day} already has a measurement; set FITATU_LIVE_MEASUREMENT_DAY")
        try:
            saved = client.measurements.set_day(user_id, day, weight=70.0, waist=80.0)
            assert float(saved["weight"]) == 70.0
            assert float(saved["waist"]) == 80.0
        finally:
            client.measurements.delete_day(user_id, day)
            assert client.measurements.get_day(user_id, day) is None, f"cleanup failed: delete the measurement on {day} by hand"
