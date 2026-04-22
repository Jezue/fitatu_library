"""High-level convenience facade for common Fitatu flows."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .auth import FitatuAuthContext
from .client import FitatuApiClient
from .exceptions import FitatuApiError

logger = logging.getLogger(__name__)


class FitatuLibrary:
    """High-level API-only facade for Fitatu endpoints."""

    _DAY_MACRO_FIELDS = ("energy", "protein", "fat", "carbohydrate", "fiber", "sugars", "salt")

    def __init__(self, session_data: dict[str, Any], headless: bool = True) -> None:
        self.session_data = session_data
        self.headless = headless

    def _build_auth(self, **kwargs: Any) -> FitatuAuthContext:
        """Build a FitatuAuthContext from stored session data, applying any call-time overrides."""
        auth = FitatuAuthContext.from_session_data(self.session_data)
        bearer_override = kwargs.get("bearer_token")
        if bearer_override:
            auth.bearer_token = bearer_override
        if kwargs.get("api_cluster"):
            auth.api_cluster = kwargs["api_cluster"]
        if kwargs.get("app_uuid"):
            auth.app_uuid = kwargs["app_uuid"]
        return auth

    def _build_client(self, **kwargs: Any) -> FitatuApiClient:
        """Instantiate a FitatuApiClient with call-time keyword overrides forwarded to the constructor."""
        client_kwargs: dict[str, Any] = {}
        for key in (
            "base_url",
            "timeout_seconds",
            "retry_max_attempts",
            "retry_base_delay_seconds",
            "token_store_path",
            "operational_store_path",
            "persist_tokens",
        ):
            if key in kwargs:
                client_kwargs[key] = kwargs[key]
        return FitatuApiClient(auth=self._build_auth(**kwargs), **client_kwargs)

    @staticmethod
    def _error_result(operation: str, exc: FitatuApiError) -> dict[str, Any]:
        """Serialize a FitatuApiError into a standard error result dict."""
        return {
            "status": "error",
            "operation": operation,
            "message": str(exc),
            "status_code": exc.status_code,
            "body": exc.body,
        }

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Coerce API numeric values into floats while treating missing/invalid values as zero."""
        if isinstance(value, bool) or value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _empty_macro_totals(cls) -> dict[str, float]:
        return {field: 0.0 for field in cls._DAY_MACRO_FIELDS}

    @classmethod
    def _meal_item_summary(cls, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_day_diet_item_id": item.get("planDayDietItemId"),
            "product_id": item.get("productId"),
            "name": item.get("name") or "Unknown",
            "brand": item.get("brand"),
            "measure_name": item.get("measureName"),
            "measure_quantity": cls._safe_float(item.get("measureQuantity")),
            "weight": cls._safe_float(item.get("weight")),
            "energy": cls._safe_float(item.get("energy")),
            "protein": cls._safe_float(item.get("protein")),
            "fat": cls._safe_float(item.get("fat")),
            "carbohydrate": cls._safe_float(item.get("carbohydrate")),
            "fiber": cls._safe_float(item.get("fiber")),
            "sugars": cls._safe_float(item.get("sugars")),
            "salt": cls._safe_float(item.get("salt")),
            "eaten": bool(item.get("eaten", False)),
        }

    @classmethod
    def _aggregate_day_summary(cls, *, user_id: str, target_date: date, day: dict[str, Any]) -> dict[str, Any]:
        diet_plan = day.get("dietPlan") or {}
        totals = cls._empty_macro_totals()
        meals: list[dict[str, Any]] = []

        if not isinstance(diet_plan, dict):
            diet_plan = {}

        for meal_key, meal_data in diet_plan.items():
            if not isinstance(meal_data, dict):
                continue

            meal_totals = cls._empty_macro_totals()
            meal_items: list[dict[str, Any]] = []
            for raw_item in meal_data.get("items") or []:
                if not isinstance(raw_item, dict):
                    continue
                item = cls._meal_item_summary(raw_item)
                meal_items.append(item)
                for macro in cls._DAY_MACRO_FIELDS:
                    value = item[macro]
                    meal_totals[macro] += value
                    totals[macro] += value

            meals.append(
                {
                    "meal_key": meal_key,
                    "meal_name": meal_data.get("mealName") or meal_key,
                    "meal_time": meal_data.get("mealTime"),
                    "recommended_percent": meal_data.get("recommendedPercent"),
                    "item_count": len(meal_items),
                    "totals": meal_totals,
                    "items": meal_items,
                }
            )

        return {
            "user_id": user_id,
            "date": target_date.isoformat(),
            "totals": totals,
            "meals": meals,
        }

    def _resolve_user_id(self, auth: FitatuAuthContext, user_id: str | None) -> str | None:
        """Return the explicit user_id, falling back to the user id embedded in the auth context."""
        value = user_id or auth.fitatu_user_id
        return str(value) if value else None

    def describe_session(self, **kwargs: Any) -> dict[str, Any]:
        """Describe the current session state using a temporary client instance."""
        return self._build_client(**kwargs).describe_auth_state()

    def clear_session(self, *, clear_token_store: bool = True, **kwargs: Any) -> dict[str, Any]:
        """Clear auth for the current session context."""
        client = self._build_client(**kwargs)
        client.clear_auth(clear_token_store=clear_token_store)
        return {"status": "ok", "session": client.describe_auth_state()}

    def reauthenticate_session(
        self,
        *,
        relogin_callback: Any | None = None,
        clear_token_store: bool = False,
        rollback_on_failure: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Reauthenticate the session and return an operational result payload."""
        client = self._build_client(**kwargs)
        result = client.reauthenticate(
            relogin_callback=relogin_callback,
            clear_token_store=clear_token_store,
            rollback_on_failure=rollback_on_failure,
        )
        if result.get("status") == "ok":
            logger.info("Session reauthenticated via API: %s", result.get("mode"))
        else:
            logger.error("Session reauthentication failed: %s", result.get("mode"))
        return result

    def management_report(self, *, include_tokens: bool = False, **kwargs: Any) -> dict[str, Any]:
        """Return a management-style report for the current session."""
        return self._build_client(**kwargs).management_report(include_tokens=include_tokens)

    def export_session_context(self, *, include_tokens: bool = False, **kwargs: Any) -> dict[str, Any]:
        """Export the current session context in a reusable JSON-friendly format."""
        return self._build_client(**kwargs).auth.to_session_data(include_tokens=include_tokens)

    def add_user_dish_via_api(
        self,
        name: str,
        items: list[dict[str, Any]],
        meal_schema: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a recipe (dish) through the high-level facade.

        Args:
            name: Recipe name.
            items: List of ingredient dicts, e.g.
                ``[{"name": "banan", "amount": 100}, {"name": "mleko", "amount": 200}]``.
            meal_schema: Optional list of meal keys the recipe should appear in.
        """
        client = self._build_client(**kwargs)
        try:
            result = client.create_recipe(
                name=name,
                items=items,
                meal_schema=meal_schema,
                categories=kwargs.get("categories"),
                cooking_time=kwargs.get("cookingTime", 2),
                preparation_time=kwargs.get("preparationTime", ""),
                recipe_description=kwargs.get("recipeDescription", "1. test"),
                serving=str(kwargs.get("serving", 1)),
                shared=kwargs.get("shared", False),
                tags=kwargs.get("tags", []),
            )
            return {"status": "ok", "result": result}
        except FitatuApiError as exc:
            return self._error_result("add_user_dish_via_api", exc)

    def create_product_via_api(self, **kwargs: Any) -> dict[str, Any]:
        """Create a product through the high-level facade."""
        client = self._build_client(**kwargs)
        try:
            result = client.create_product(
                name=kwargs["name"],
                brand=kwargs["brand"],
                energy=kwargs["energy"],
                protein=kwargs["protein"],
                fat=kwargs["fat"],
                carbohydrate=kwargs["carbohydrate"],
                producer=kwargs.get("producer"),
                portion_weight=kwargs.get("portion_weight"),
                fiber=kwargs.get("fiber"),
                sugars=kwargs.get("sugars"),
                sodium=kwargs.get("sodium"),
                saturated_fat=kwargs.get("saturated_fat"),
                salt=kwargs.get("salt"),
            )
            return {"status": "ok", "result": result}
        except FitatuApiError as exc:
            return self._error_result("create_product_via_api", exc)

    def search_user_food_via_api(
        self,
        *,
        phrase: str,
        target_date: date,
        user_id: str | None = None,
        page: int = 1,
        limit: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Search user-created food catalog entries through the high-level facade.

        Result rows use ``foodId`` as the product identifier (not ``id``).
        Pass it to ``get_product_details`` for full details.
        """
        client, fitatu_user_id, error = self._planner_result(
            "search_user_food_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.search_user_food(
                    fitatu_user_id,
                    phrase,
                    target_date,
                    page=page,
                    limit=limit,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("search_user_food_via_api", exc)

    def delete_product_via_api(self, *, product_id: int | str, **kwargs: Any) -> dict[str, Any]:
        """Delete a user-created product through the high-level facade."""
        client = self._build_client(**kwargs)
        try:
            return {"status": "ok", "result": client.delete_product(product_id)}
        except FitatuApiError as exc:
            return self._error_result("delete_product_via_api", exc)

    def set_product_proposal_via_api(
        self,
        *,
        product_id: int | str,
        property_name: str,
        property_value: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Set a product proposal property through the high-level facade."""
        client = self._build_client(**kwargs)
        try:
            return {
                "status": "ok",
                "result": client.set_product_proposal(
                    product_id,
                    property_name=property_name,
                    property_value=property_value,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("set_product_proposal_via_api", exc)

    def set_product_raw_ingredients_via_api(
        self,
        *,
        product_id: int | str,
        raw_ingredients: str | list[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Set a product rawIngredients proposal through the high-level facade."""
        client = self._build_client(**kwargs)
        try:
            return {
                "status": "ok",
                "result": client.set_product_raw_ingredients(product_id, raw_ingredients),
            }
        except FitatuApiError as exc:
            return self._error_result("set_product_raw_ingredients_via_api", exc)

    def find_matching_user_product_via_api(
        self,
        *,
        phrase: str,
        target_date: date,
        nutrition: dict[str, Any],
        brand: str | None = None,
        fields: tuple[str, ...] = ("energy", "protein", "fat", "carbohydrate"),
        tolerance: float = 0.001,
        page: int = 1,
        limit: int = 50,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Find a user product by phrase, optional brand and nutrition tolerance.

        ``nutrition`` is a required dict of macro field names to expected values::

            lib.find_matching_user_product_via_api(
                phrase="banan",
                target_date=date.today(),
                nutrition={"energy": 89, "protein": 1.1, "fat": 0.3, "carbohydrate": 23},
            )

        Returns ``{"status": "ok", "found": bool, "result": product_dict | None}``.
        Always inspect the top-level ``found`` key — ``result`` is ``None`` when no
        match is found, so ``result["..."]`` will raise ``TypeError`` on no-match.
        """
        client, fitatu_user_id, error = self._planner_result(
            "find_matching_user_product_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            match = client.find_matching_user_product(
                fitatu_user_id,
                phrase,
                target_date,
                nutrition=nutrition,
                brand=brand,
                fields=fields,
                tolerance=tolerance,
                page=page,
                limit=limit,
            )
            return {
                "status": "ok",
                "found": match is not None,
                "result": match,
            }
        except FitatuApiError as exc:
            return self._error_result("find_matching_user_product_via_api", exc)

    def cleanup_duplicate_user_products_via_api(
        self,
        *,
        phrase: str,
        target_date: date,
        brand: str | None = None,
        keep_product_id: int | str | None = None,
        predicate: Any | None = None,
        page: int = 1,
        limit: int = 50,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delete duplicate user products selected by brand or custom predicate."""
        client, fitatu_user_id, error = self._planner_result(
            "cleanup_duplicate_user_products_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.cleanup_duplicate_user_products(
                    fitatu_user_id,
                    phrase,
                    target_date,
                    brand=brand,
                    keep_product_id=keep_product_id,
                    predicate=predicate,
                    page=page,
                    limit=limit,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("cleanup_duplicate_user_products_via_api", exc)

    def get_recipes_catalog_via_api(
        self,
        *,
        category_id: int | str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch the recipes catalog or a single category through the facade.

        Note: the /recipes-catalog endpoint does not accept query parameters.
        """
        client = self._build_client(**kwargs)
        try:
            result = (
                client.get_recipes_catalog()
                if category_id is None
                else client.get_recipes_catalog_category(category_id)
            )
            return {"status": "ok", "result": result}
        except FitatuApiError as exc:
            return self._error_result("get_recipes_catalog_via_api", exc)

    def get_recipe_via_api(self, *, recipe_id: int | str, **kwargs: Any) -> dict[str, Any]:
        """Fetch a recipe through the high-level facade."""
        client = self._build_client(**kwargs)
        try:
            return {"status": "ok", "result": client.get_recipe(recipe_id)}
        except FitatuApiError as exc:
            return self._error_result("get_recipe_via_api", exc)

    def _planner_result(self, operation: str, *, user_id: str | None, **kwargs: Any) -> tuple[FitatuApiClient | None, str | None, dict[str, Any] | None]:
        """Resolve a client + user_id pair, or return an error dict when the user id cannot be determined."""
        auth = self._build_auth(**kwargs)
        fitatu_user_id = self._resolve_user_id(auth, user_id)
        if not fitatu_user_id:
            return None, None, {
                "status": "error",
                "operation": operation,
                "message": "Missing fitatu user id. Provide user_id or ensure session_data includes fitatu user payload.",
                "status_code": None,
                "body": None,
            }
        return FitatuApiClient(auth=auth), fitatu_user_id, None

    def add_custom_item_to_day_meal_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add a custom planner item via the high-level facade."""
        client, fitatu_user_id, error = self._planner_result(
            "add_custom_item_to_day_meal_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.planner.add_custom_item_to_day_meal(
                    fitatu_user_id,
                    target_date,
                    meal_type=meal_key,
                    name=name,
                    calories=calories,
                    protein_g=protein_g,
                    fat_g=fat_g,
                    carbs_g=carbs_g,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("add_custom_item_to_day_meal_via_api", exc)

    def add_product_to_day_meal_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        product_id: int | str,
        measure_id: int | str | None = None,
        measure_quantity: float | int = 1,
        measure_unit: str | None = None,
        measure_amount: float | int | None = None,
        strict_measure: bool = True,
        eaten: bool = False,
        source: str = "API",
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add a product to a planner meal via the high-level facade."""
        client, fitatu_user_id, error = self._planner_result(
            "add_product_to_day_meal_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            if measure_unit is not None:
                amount = measure_quantity if measure_amount is None else measure_amount
                result = client.planner.add_product_to_day_meal_with_unit(
                    fitatu_user_id,
                    target_date,
                    meal_type=meal_key,
                    product_id=product_id,
                    amount=amount,
                    unit=measure_unit,
                    eaten=eaten,
                    source=source,
                    strict_measure=strict_measure,
                )
            else:
                if measure_id is None:
                    raise FitatuApiError("measure_id is required when measure_unit is not provided")
                result = client.planner.add_product_to_day_meal(
                    fitatu_user_id,
                    target_date,
                    meal_type=meal_key,
                    product_id=product_id,
                    measure_id=measure_id,
                    measure_quantity=measure_quantity,
                    eaten=eaten,
                    source=source,
                )
            return {
                "status": "ok",
                "date": target_date.isoformat(),
                "meal_key": meal_key,
                "result": result,
            }
        except FitatuApiError as exc:
            return self._error_result("add_product_to_day_meal_via_api", exc)

    def add_recipe_to_day_meal_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        recipe_id: int | str,
        food_type: str = "RECIPE",
        measure_id: int | str = 39,
        measure_quantity: float | int = 1,
        ingredients_serving: float | int | None = 1,
        eaten: bool = False,
        source: str = "API",
        hydrate_from_recipe_details: bool = True,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add a recipe entry to a planner meal via the high-level facade."""
        client, fitatu_user_id, error = self._planner_result(
            "add_recipe_to_day_meal_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            result = client.planner.add_recipe_to_day_meal(
                fitatu_user_id,
                target_date,
                meal_type=meal_key,
                recipe_id=recipe_id,
                food_type=food_type,
                measure_id=measure_id,
                measure_quantity=measure_quantity,
                ingredients_serving=ingredients_serving,
                eaten=eaten,
                source=source,
                hydrate_from_recipe_details=hydrate_from_recipe_details,
            )
            return {
                "status": "ok",
                "date": target_date.isoformat(),
                "meal_key": meal_key,
                "result": result,
            }
        except FitatuApiError as exc:
            return self._error_result("add_recipe_to_day_meal_via_api", exc)

    def add_search_result_to_day_meal_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        phrase: str,
        index: int = 0,
        measure_quantity: float | int = 1,
        measure_amount: float | int | None = None,
        measure_unit: str | None = None,
        strict_measure: bool = True,
        eaten: bool = False,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Search food and add the selected result to a planner meal."""
        client, fitatu_user_id, error = self._planner_result(
            "add_search_result_to_day_meal_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "date": target_date.isoformat(),
                "meal_key": meal_key,
                "result": client.planner.add_search_result_to_day_meal(
                    fitatu_user_id,
                    target_date,
                    meal_type=meal_key,
                    phrase=phrase,
                    index=index,
                    measure_quantity=measure_quantity,
                    measure_amount=measure_amount,
                    measure_unit=measure_unit,
                    strict_measure=strict_measure,
                    eaten=eaten,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("add_search_result_to_day_meal_via_api", exc)

    def update_day_item_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        item_id: str | int,
        measure_quantity: float | int | None = None,
        measure_id: str | int | None = None,
        eaten: bool | None = None,
        name: str | None = None,
        source: str | None = None,
        patch: dict[str, Any] | None = None,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update a planner item via the high-level facade."""
        client, fitatu_user_id, error = self._planner_result(
            "update_day_item_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.planner.update_day_item(
                    fitatu_user_id,
                    target_date,
                    meal_type=meal_key,
                    item_id=item_id,
                    measure_quantity=measure_quantity,
                    measure_id=measure_id,
                    eaten=eaten,
                    name=name,
                    source=source,
                    patch=patch,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("update_day_item_via_api", exc)

    def remove_day_item_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        item_id: str | int,
        delete_all_related_meals: bool = False,
        use_aggressive_soft_delete: bool = True,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Remove a planner item via the high-level facade.

        Uses smart failover:
        1. Hard DELETE endpoint (likely 404)
        2. Soft-delete via snapshot (item quantity = 0.01, marked invisible)
        3. Aggressive soft-delete via snapshot if enabled (item quantity = 0.0)

        Note: API-created CUSTOM_ITEMs (source="API") cannot be fully deleted.
        Backend protects them to maintain sync state with external integrations.
        Only soft-delete is possible for these items.

        Returns:
            ``{"status": "ok", "result": ...}`` when the nested ``result["ok"]``
            is truthy — the item was confirmed removed.
            ``{"status": "partial", "result": ...}`` when ``result["ok"]`` is
            falsy — all strategies were attempted but the backend did not confirm
            removal (e.g. the item was already handled by a prior move/replace,
            or soft-delete reached its retry limit).  Always inspect *both* the
            top-level ``status`` and ``result["ok"]`` to distinguish these cases:

                r = lib.remove_day_item_via_api(...)
                if r["status"] != "ok" or not r["result"].get("ok"):
                    # removal not confirmed

        Args:
            target_date: Date to remove item from
            meal_key: Meal identifier (breakfast, lunch, etc.)
            item_id: Item ID
            delete_all_related_meals: Whether to include related meals (for hard DELETE)
            use_aggressive_soft_delete: If True, try quantity=0 as final fallback.
                Useful when normal soft-delete (qty=0.01) fails.
            user_id: Explicit user ID (defaults to session user)
        """
        client, fitatu_user_id, error = self._planner_result(
            "remove_day_item_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            result = client.planner.remove_day_item(
                fitatu_user_id,
                target_date,
                meal_key,
                item_id,
                delete_all_related_meals=delete_all_related_meals,
                use_aggressive_soft_delete=use_aggressive_soft_delete,
            )
            ok = result.get("ok") if isinstance(result, dict) else bool(result)
            return {
                "status": "ok" if ok else "partial",
                "result": result,
            }
        except FitatuApiError as exc:
            return self._error_result("remove_day_item_via_api", exc)

    def move_day_item_via_api(
        self,
        *,
        from_date: date,
        from_meal_key: str,
        item_id: str | int,
        to_date: date | None = None,
        to_meal_key: str | None = None,
        synchronous: bool = True,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Move a planner item with deletedAt + copied item sync. Live-tested 2026-04-22."""
        client, fitatu_user_id, error = self._planner_result(
            "move_day_item_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.planner.move_day_item(
                    fitatu_user_id,
                    from_date,
                    from_meal_type=from_meal_key,
                    item_id=item_id,
                    to_day=to_date,
                    to_meal_type=to_meal_key,
                    synchronous=synchronous,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("move_day_item_via_api", exc)

    def replace_day_item_with_custom_item_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        item_id: str | int,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        source: str = "API",
        synchronous: bool = True,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Replace a planner item with a custom item in one sync. Live-tested 2026-04-22."""
        client, fitatu_user_id, error = self._planner_result(
            "replace_day_item_with_custom_item_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.planner.replace_day_item_with_custom_item(
                    fitatu_user_id,
                    target_date,
                    meal_type=meal_key,
                    item_id=item_id,
                    name=name,
                    calories=calories,
                    protein_g=protein_g,
                    fat_g=fat_g,
                    carbs_g=carbs_g,
                    source=source,
                    synchronous=synchronous,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("replace_day_item_with_custom_item_via_api", exc)

    def remove_day_item_with_strategy_via_api(
        self,
        *,
        target_date: date,
        meal_key: str,
        item_id: str | int,
        item_kind: str = "auto",
        delete_all_related_meals: bool = False,
        use_aggressive_soft_delete: bool = True,
        max_soft_delete_retries: int = 2,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Remove planner item with explicit strategy by item kind.

        item_kind values:
        - auto
        - normal_item
        - custom_add_item
        - custom_recipe_item
        """
        client, fitatu_user_id, error = self._planner_result(
            "remove_day_item_with_strategy_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.planner.remove_day_item_with_strategy(
                    fitatu_user_id,
                    target_date,
                    meal_key,
                    item_id,
                    item_kind=item_kind,
                    delete_all_related_meals=delete_all_related_meals,
                    use_aggressive_soft_delete=use_aggressive_soft_delete,
                    max_soft_delete_retries=max_soft_delete_retries,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("remove_day_item_with_strategy_via_api", exc)

    def remove_day_items_by_kind_via_api(
        self,
        *,
        target_date: date,
        item_kind: str,
        meal_key: str | None = None,
        max_items: int | None = None,
        delete_all_related_meals: bool = False,
        use_aggressive_soft_delete: bool = True,
        max_soft_delete_retries: int = 2,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Remove day items by requested kind, optionally scoped to one meal."""
        client, fitatu_user_id, error = self._planner_result(
            "remove_day_items_by_kind_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            return {
                "status": "ok",
                "result": client.planner.remove_day_items_by_kind(
                    fitatu_user_id,
                    target_date,
                    item_kind=item_kind,
                    meal_key=meal_key,
                    max_items=max_items,
                    delete_all_related_meals=delete_all_related_meals,
                    use_aggressive_soft_delete=use_aggressive_soft_delete,
                    max_soft_delete_retries=max_soft_delete_retries,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("remove_day_items_by_kind_via_api", exc)

    def get_day_macros_via_api(
        self,
        *,
        target_date: date,
        include_meal_breakdown: bool = False,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return aggregated macro totals for a planner day.

        Sums energy, protein, fat, carbohydrate, fiber, sugars and salt across all meal
        items for the given date. Set *include_meal_breakdown* to also receive per-meal
        subtotals.

        Args:
            target_date: Date to fetch macros for.
            include_meal_breakdown: When True, include per-meal subtotals under ``meals``.
            user_id: Explicit user ID (defaults to session user).
        """
        client, fitatu_user_id, error = self._planner_result(
            "get_day_macros_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            day = client.planner.get_day(fitatu_user_id, target_date)
            summary = self._aggregate_day_summary(
                user_id=fitatu_user_id,
                target_date=target_date,
                day=day,
            )
            result: dict[str, Any] = {
                "date": target_date.isoformat(),
                "totals": summary["totals"],
            }
            if include_meal_breakdown:
                result["meals"] = {meal["meal_key"]: meal["totals"] for meal in summary["meals"]}
            return {"status": "ok", "result": result}
        except FitatuApiError as exc:
            return self._error_result("get_day_macros_via_api", exc)

    def get_day_summary_via_api(
        self,
        *,
        target_date: date,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return a normalized day nutrition summary with meal and item totals."""
        client, fitatu_user_id, error = self._planner_result(
            "get_day_summary_via_api",
            user_id=user_id,
            **kwargs,
        )
        if error is not None:
            return error
        assert client is not None and fitatu_user_id is not None
        try:
            day = client.planner.get_day(fitatu_user_id, target_date)
            return {
                "status": "ok",
                "result": self._aggregate_day_summary(
                    user_id=fitatu_user_id,
                    target_date=target_date,
                    day=day,
                ),
            }
        except FitatuApiError as exc:
            return self._error_result("get_day_summary_via_api", exc)

    def search_food(
        self,
        phrase: str,
        locale: str = "pl_PL",
        limit: int = 5,
        bearer_token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search food using the current session context.

        Each result row contains a ``foodId`` field — use this (not ``id``) when
        calling ``get_product_details`` for follow-up detail lookups.
        """
        auth = self._build_auth(bearer_token=bearer_token)
        client = FitatuApiClient(auth=auth)
        try:
            return client.search_food(phrase=phrase, locale=locale, limit=limit, page=1)
        except FitatuApiError:
            return []
