"""High-level convenience facade for common Fitatu flows; deprecated, removed in 1.0."""

from __future__ import annotations

import base64
import logging
import sys
import warnings
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from ._files import write_private_file
from ._summaries import daily_targets, day_summary
from ._validation import as_date
from .auth import FitatuAuthContext
from .client import FitatuApiClient
from .exceptions import (
    FitatuApiError,
    FitatuAuthError,
    FitatuValidationError,
    describe_error,
    log_caught,
)

logger = logging.getLogger(__name__)

_BINARY_EXPORT_TYPES = {"pdf": "application/pdf", "xls": "application/vnd.ms-excel"}
_DEPRECATED = (
    "FitatuLibrary is deprecated and will be removed in fitatu-api 1.0. Use FitatuTools for results in an "
    "envelope ({'ok': True, 'data': ...} / {'ok': False, 'error': ...}), or FitatuApiClient for data and "
    "typed exceptions."
)


def _warn_deprecated() -> None:
    """Warn once, at the first caller outside this module (the user's line, also through from_session())."""
    frame, stacklevel = sys._getframe(1), 2
    while frame.f_back is not None and frame.f_code.co_filename == __file__:
        frame, stacklevel = frame.f_back, stacklevel + 1
    warnings.warn(_DEPRECATED, DeprecationWarning, stacklevel=stacklevel)


class FitatuLibrary:
    """High-level API-only facade for Fitatu endpoints.

    .. deprecated:: 0.5.0
        Constructing it (or :meth:`from_session`) emits a ``DeprecationWarning``; it will be
        removed in 1.0. Use :class:`FitatuTools` for results in an envelope (``{"ok": True,
        "data": ...}`` / ``{"ok": False, "error": describe_error(exc)}``), or
        :class:`FitatuApiClient` for data and typed exceptions. Its behaviour and its
        envelope stay as they are until then.

    Every ``*_via_api`` method returns a dict and never raises: ``{"status": "ok",
    "result": ...}``; ``"partial"`` instead of ``"ok"`` whenever ``result["ok"]`` is False
    (a write the re-read did not confirm, a row that was not there, a cleanup with
    errors); and for any exception
    ``{"status": "error", "operation": <method name>, **describe_error(exc)}``.

    All calls share one :class:`FitatuApiClient`, built on first use, so a token that one
    call refreshes is used by the next. After each call the client's current token pair
    is copied into ``session_data``; with ``session_path``, a refreshed pair is also
    merged into that file.
    Calls act for the user the session belongs to.

    Arguments the methods share:

    - Dates (``target_date``, ``from_date``, ``to_date``) are ``date`` objects or
      ``'YYYY-MM-DD'`` strings.
    - ``meal_type`` is a meal key of the day: ``breakfast``, ``second_breakfast``,
      ``lunch``, ``dinner``, ``snack`` or ``supper``.
    - ``item_id`` names a row already on the day: ``row_id`` in day summaries and in add
      results.
    - ``row_id`` is an optional UUID v1 for a row being added. Sending the same one again
      updates that row instead of adding a second, so a retried add is safe. The id the
      row got comes back as ``row_id``.
    - Energy is in kcal and macronutrients in grams; ``eaten=False`` logs a row as planned.
    - ``source`` is a row's source: ``API``, ``GENERATOR`` or ``FOOD_PROPOSAL``.

    Args:
        session_data: A session dict (``fitatu-api login`` output or
            :meth:`export_session_context`).
        base_url: API host; defaults to the session's.
        timeout_seconds: Timeout of each HTTP request, in seconds.
        session_path: Session file used as the token store. A token pair already in it
            replaces the one in ``session_data``, and refreshed tokens are merged into it.
    """

    def __init__(
        self,
        session_data: dict[str, Any],
        *,
        base_url: str | None = None,
        timeout_seconds: int = 25,
        session_path: str | Path | None = None,
    ) -> None:
        _warn_deprecated()
        self.session_data = session_data
        self._client_options: dict[str, Any] = {
            "base_url": base_url,
            "timeout_seconds": timeout_seconds,
            "token_store_path": session_path,
        }
        self._client: FitatuApiClient | None = None

    @classmethod
    def from_session(cls, path: str | Path | None = None) -> FitatuLibrary:
        """Load the facade from a session file and keep that file's tokens up to date.

        Args:
            path: Session file; without one, ``$FITATU_SESSION_JSON`` when set (refreshed
                tokens are then not saved), else ``$FITATU_SESSION``, then
                ``~/.fitatu/session.json`` (see :meth:`FitatuApiClient.from_session`).

        Raises:
            FitatuAuthError: When the file is missing or unreadable.
        """
        client = FitatuApiClient.from_session(path)
        library = cls(client.auth.to_session_data(include_tokens=True))
        library._client = client
        return library

    def close(self) -> None:
        """Close the shared client's HTTP connections; the facade is also a context manager."""
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> FitatuLibrary:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _get_client(self) -> FitatuApiClient:
        if self._client is None:
            auth = FitatuAuthContext.from_session_data(self.session_data)
            self._client = FitatuApiClient(auth, **self._client_options)
        return self._client

    def _mirror_tokens(self) -> None:
        if self._client is None:
            return
        # from_session_data prefers the camelCase keys, so a stale one would shadow the new pair.
        self.session_data.pop("bearerToken", None)
        self.session_data.pop("refreshToken", None)
        self.session_data.update(
            bearer_token=self._client.auth.bearer_token,
            refresh_token=self._client.auth.refresh_token,
        )

    def _run(self, operation: str, fn: Callable[[FitatuApiClient, str], Any]) -> dict[str, Any]:
        """Call ``fn(client, user_id)`` on the shared client and wrap its outcome (see the class docstring)."""
        try:
            client = self._get_client()
            user_id = client.auth.fitatu_user_id
            if not user_id:
                raise FitatuAuthError("the session has no Fitatu user id; log in again to get a complete session")
            result = fn(client, user_id)
        except Exception as exc:
            error = describe_error(exc)
            log_caught(logger, operation, exc, error)
            return {"status": "error", "operation": operation, **error}
        finally:
            self._mirror_tokens()
        partial = isinstance(result, dict) and result.get("ok") is False
        return {"status": "partial" if partial else "ok", "result": result}

    def describe_session(self) -> dict[str, Any]:
        """Return a snapshot of the session without secrets: user id, locales, host, token expiry."""
        return self._get_client().describe_auth_state()

    def clear_session(self, *, clear_token_store: bool = True) -> dict[str, Any]:
        """Forget the tokens; later calls fail until a new session is loaded.

        Args:
            clear_token_store: Also delete the token file (``session_path``, or the file
                :meth:`from_session` loaded).
        """
        client = self._get_client()
        client.clear_auth(clear_token_store=clear_token_store)
        self._mirror_tokens()
        return {"status": "ok", "session": client.describe_auth_state()}

    def export_session_context(self, *, include_tokens: bool = False) -> dict[str, Any]:
        """Return the session as a dict that :class:`FitatuLibrary` accepts again.

        Args:
            include_tokens: Add the current bearer and refresh token (secrets), i.e. the
                pair the last refresh issued.
        """
        return self._get_client().auth.to_session_data(include_tokens=include_tokens)

    def add_user_dish_via_api(
        self,
        *,
        name: str,
        items: list[dict[str, Any]],
        meal_schema: list[str] | None = None,
        categories: list[Any] | None = None,
        cooking_time: int | None = 2,
        preparation_time: str = "",
        recipe_description: str = "1. automation",
        serving: int | str = 1,
        shared: bool = False,
        tags: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Create a recipe (dish) in the user's own catalog.

        Args:
            name: Recipe name.
            items: Ingredients as ``{"foodId", "measureId", "measureQuantity"}`` (or
                ``itemId`` instead of ``foodId``); others are skipped.
            meal_schema: Meal keys the recipe is meant for, e.g. ``["breakfast"]``.
            categories: Recipe category ids.
            cooking_time: The app's integer ``cookingTime``.
            preparation_time: Preparation time text.
            recipe_description: Preparation steps.
            serving: Number of servings the ingredients make.
            shared: Share the recipe with other users.
            tags: Recipe tags.
        """
        return self._run(
            "add_user_dish_via_api",
            lambda client, _: client.create_recipe(
                name=name, items=items, meal_schema=meal_schema, categories=categories, cooking_time=cooking_time,
                preparation_time=preparation_time, recipe_description=recipe_description, serving=str(serving),
                shared=shared, tags=tags,
            ),
        )

    def create_product_via_api(
        self,
        *,
        name: str,
        brand: str,
        energy: float | int,
        protein: float | int,
        fat: float | int,
        carbohydrate: float | int,
        producer: str | None = None,
        portion_weight: float | int | None = None,
        fiber: float | int | None = None,
        sugars: float | int | None = None,
        sodium: float | int | None = None,
        saturated_fat: float | int | None = None,
        salt: float | int | None = None,
    ) -> dict[str, Any]:
        """Create a product in the Fitatu catalog. Nutrition values are per 100 g.

        Args:
            name: Product name.
            brand: Brand name.
            energy: Energy in kcal.
            protein: Protein in grams.
            fat: Fat in grams.
            carbohydrate: Carbohydrate in grams.
            producer: Producer name.
            portion_weight: Weight of one portion in grams.
            fiber: Fiber in grams.
            sugars: Sugars in grams.
            sodium: Sodium in milligrams.
            saturated_fat: Saturated fat in grams.
            salt: Salt in grams.
        """
        return self._run(
            "create_product_via_api",
            lambda client, _: client.create_product(
                name=name, brand=brand, energy=energy, protein=protein, fat=fat, carbohydrate=carbohydrate,
                producer=producer, portion_weight=portion_weight, fiber=fiber, sugars=sugars, sodium=sodium,
                saturated_fat=saturated_fat, salt=salt,
            ),
        )

    def search_user_food_via_api(
        self, *, phrase: str, target_date: date | str, page: int = 1, limit: int = 50
    ) -> dict[str, Any]:
        """Search the user's own products and recipes; rows carry the id as ``foodId``.

        Args:
            phrase: Search text.
            target_date: Day the search is made for.
            page: Page number, from 1.
            limit: Rows per page.
        """
        return self._run(
            "search_user_food_via_api",
            lambda client, user_id: client.search_user_food(
                user_id, phrase, as_date(target_date, "target_date"), page=page, limit=limit
            ),
        )

    def delete_product_via_api(self, *, product_id: int | str) -> dict[str, Any]:
        """Delete a product the user created.

        Args:
            product_id: Numeric product id.
        """
        return self._run("delete_product_via_api", lambda client, _: client.delete_product(product_id))

    def set_product_proposal_via_api(
        self, *, product_id: int | str, property_name: str, property_value: str
    ) -> dict[str, Any]:
        """Propose a change to a catalog product.

        Args:
            product_id: Numeric product id.
            property_name: Property to change; the API only accepts ``rawIngredients``.
            property_value: Proposed value.
        """
        return self._run(
            "set_product_proposal_via_api",
            lambda client, _: client.set_product_proposal(
                product_id, property_name=property_name, property_value=property_value
            ),
        )

    def set_product_raw_ingredients_via_api(
        self, *, product_id: int | str, raw_ingredients: str | list[str]
    ) -> dict[str, Any]:
        """Propose a product's ingredient list.

        Args:
            product_id: Numeric product id.
            raw_ingredients: Ingredient text, or a list joined with ``", "``.
        """
        return self._run(
            "set_product_raw_ingredients_via_api",
            lambda client, _: client.set_product_raw_ingredients(product_id, raw_ingredients),
        )

    def find_matching_user_product_via_api(
        self,
        *,
        phrase: str,
        target_date: date | str,
        nutrition: dict[str, Any],
        brand: str | None = None,
        fields: tuple[str, ...] = ("energy", "protein", "fat", "carbohydrate"),
        tolerance: float = 0.001,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Find the user's own product by phrase, brand and nutrition values.

        ``result`` is the first matching product, or ``None`` when nothing matches.

        Args:
            phrase: Search text.
            target_date: Day the search is made for.
            nutrition: Expected nutrition values, e.g. ``{"energy": 89, "protein": 1.1}``.
            brand: Exact brand the product must have.
            fields: Keys of ``nutrition`` to compare.
            tolerance: Allowed relative difference (0.001 = 0.1 %).
            page: Page number of the search, from 1.
            limit: Rows per page.
        """
        return self._run(
            "find_matching_user_product_via_api",
            lambda client, user_id: client.find_matching_user_product(
                user_id, phrase, as_date(target_date, "target_date"),
                nutrition=nutrition, brand=brand, fields=fields, tolerance=tolerance,
                page=page, limit=limit,
            ),
        )

    def cleanup_duplicate_user_products_via_api(
        self,
        *,
        phrase: str,
        target_date: date | str,
        brand: str | None = None,
        keep_product_id: int | str | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        page: int = 1,
        limit: int = 50,
        dry_run: bool = True,
        max_deletions: int = 10,
    ) -> dict[str, Any]:
        """Delete duplicates of the user's own product (see ``FitatuApiClient.cleanup_duplicate_user_products``).

        Args:
            phrase: Search text that finds the duplicates.
            target_date: Day the search is made for.
            brand: Only products of this exact brand; this or ``predicate`` is required.
            keep_product_id: Product to keep; required when more than one matches.
            predicate: Only products for which it returns True.
            page: Page number of the search, from 1.
            limit: Rows per page.
            dry_run: Only report ``to_delete_product_ids`` (default); False deletes them.
            max_deletions: Refuse to delete more than this many products.
        """
        return self._run(
            "cleanup_duplicate_user_products_via_api",
            lambda client, user_id: client.cleanup_duplicate_user_products(
                user_id, phrase, as_date(target_date, "target_date"),
                brand=brand, keep_product_id=keep_product_id, predicate=predicate,
                page=page, limit=limit, dry_run=dry_run, max_deletions=max_deletions,
            ),
        )

    def get_recipes_catalog_via_api(self, *, category_id: int | str | None = None) -> dict[str, Any]:
        """Return the recipes catalog, or one category of it.

        Args:
            category_id: Category id or slug from the catalog; None for the whole catalog.
        """
        return self._run(
            "get_recipes_catalog_via_api",
            lambda client, _: (
                client.get_recipes_catalog()
                if category_id is None
                else client.get_recipes_catalog_category(category_id)
            ),
        )

    def get_recipe_via_api(self, *, recipe_id: int | str) -> dict[str, Any]:
        """Return a recipe with its ingredients and nutrition.

        Args:
            recipe_id: Numeric recipe id (not a catalog slug).
        """
        return self._run("get_recipe_via_api", lambda client, _: client.get_recipe(recipe_id))

    def add_custom_item_to_day_meal_via_api(
        self,
        *,
        target_date: date | str,
        meal_type: str,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        eaten: bool = False,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Log a custom row (a name with kcal and macros) in a meal.

        Args:
            target_date: Day to log on.
            meal_type: Meal key.
            name: Row name.
            calories: Energy in kcal.
            protein_g: Protein in grams.
            fat_g: Fat in grams.
            carbs_g: Carbohydrate in grams.
            eaten: Log it as eaten rather than planned.
            row_id: Retry-safe id for the new row (see the class docstring).
        """
        return self._run(
            "add_custom_item_to_day_meal_via_api",
            lambda client, user_id: client.planner.add_custom_item_to_day_meal(
                user_id, as_date(target_date, "target_date"),
                meal_type=meal_type, name=name, calories=calories, protein_g=protein_g,
                fat_g=fat_g, carbs_g=carbs_g, eaten=eaten, row_id=row_id,
            ),
        )

    def add_product_to_day_meal_via_api(
        self,
        *,
        target_date: date | str,
        meal_type: str,
        product_id: int | str,
        measure_id: int | str | None = None,
        measure_quantity: float | int = 1,
        measure_unit: str | None = None,
        measure_amount: float | int | None = None,
        strict_measure: bool = True,
        eaten: bool = False,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Log a catalog product in a meal, by measure id or by unit.

        Args:
            target_date: Day to log on.
            meal_type: Meal key.
            product_id: Numeric product id (a search row's ``foodId``).
            measure_id: One of the product's ``measures[].id``; required without ``measure_unit``.
            measure_quantity: How many of that measure.
            measure_unit: ``g``, ``ml`` or a measure name such as ``sztuka``, converted to
                one of the product's measures instead of ``measure_id``.
            measure_amount: Amount in ``measure_unit``, or of the ``measure_id`` measure;
                defaults to ``measure_quantity``.
            strict_measure: Fail when the unit cannot be converted; with False, ``g`` and
                ``ml`` fall back to one of the product's measures, unconverted, and
                ``warnings`` says so.
            eaten: Log the row as eaten instead of planned.
            source: Row source.
            row_id: Retry-safe id for the new row (see the class docstring).
        """

        def add(client: FitatuApiClient, user_id: str) -> dict[str, Any]:
            row: dict[str, Any] = {
                "meal_type": meal_type, "product_id": product_id, "eaten": eaten, "source": source, "row_id": row_id
            }
            amount = measure_quantity if measure_amount is None else measure_amount
            if measure_unit is not None:
                return client.planner.add_product_to_day_meal_with_unit(
                    user_id, as_date(target_date, "target_date"),
                    amount=amount, unit=measure_unit, strict_measure=strict_measure, **row
                )
            if measure_id is None:
                raise FitatuValidationError("pass measure_id, or measure_unit with measure_amount")
            return client.planner.add_product_to_day_meal(
                user_id, as_date(target_date, "target_date"), measure_id=measure_id, measure_quantity=amount, **row
            )

        return self._run("add_product_to_day_meal_via_api", add)

    def add_recipe_to_day_meal_via_api(
        self,
        *,
        target_date: date | str,
        meal_type: str,
        recipe_id: int | str,
        food_type: str = "RECIPE",
        measure_id: int | str = 39,
        measure_quantity: float | int = 1,
        ingredients_serving: float | int | None = 1,
        eaten: bool = False,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Log a recipe in a meal; the server fills in its name and nutrition.

        Args:
            target_date: Day to log on.
            meal_type: Meal key.
            recipe_id: Numeric recipe id.
            food_type: ``RECIPE`` or ``RECIPE_AI``.
            measure_id: Recipe measure id (the planner's default is 39).
            measure_quantity: How many of that measure.
            ingredients_serving: Sent as the row's ``ingredientsServing``.
            eaten: Log the row as eaten instead of planned.
            source: Row source.
            row_id: Retry-safe id for the new row (see the class docstring).
        """
        return self._run(
            "add_recipe_to_day_meal_via_api",
            lambda client, user_id: client.planner.add_recipe_to_day_meal(
                user_id, as_date(target_date, "target_date"),
                meal_type=meal_type, recipe_id=recipe_id, food_type=food_type,
                measure_id=measure_id, measure_quantity=measure_quantity, ingredients_serving=ingredients_serving,
                eaten=eaten, source=source, row_id=row_id,
            ),
        )

    def add_search_result_to_day_meal_via_api(
        self,
        *,
        target_date: date | str,
        meal_type: str,
        phrase: str,
        index: int = 0,
        measure_quantity: float | int = 1,
        measure_amount: float | int | None = None,
        measure_unit: str | None = None,
        strict_measure: bool = True,
        eaten: bool = False,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Search food and log one hit (a product or a recipe) in a meal.

        Args:
            target_date: Day to log on.
            meal_type: Meal key.
            phrase: Search text.
            index: Which hit to log, from 0.
            measure_quantity: How many of the hit's own measure.
            measure_amount: Amount in ``measure_unit``, or of the hit's measure.
            measure_unit: ``g``, ``ml`` or a measure name; products only.
            strict_measure: As in :meth:`add_product_to_day_meal_via_api`.
            eaten: Log the row as eaten instead of planned.
            row_id: Retry-safe id for the new row (see the class docstring).
        """
        return self._run(
            "add_search_result_to_day_meal_via_api",
            lambda client, user_id: client.planner.add_search_result_to_day_meal(
                user_id, as_date(target_date, "target_date"), meal_type=meal_type, phrase=phrase, index=index,
                measure_quantity=measure_quantity, measure_amount=measure_amount, measure_unit=measure_unit,
                strict_measure=strict_measure, eaten=eaten, row_id=row_id,
            ),
        )

    def update_day_item_via_api(
        self,
        *,
        target_date: date | str,
        meal_type: str,
        item_id: str | int,
        measure_quantity: float | int | None = None,
        measure_id: str | int | None = None,
        eaten: bool | None = None,
        name: str | None = None,
        source: str | None = None,
        patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Change a logged row; values left as None stay as they are.

        ``status`` is ``"partial"`` when the re-read row does not show the change.

        Args:
            target_date: Day of the row.
            meal_type: Meal key of the row.
            item_id: Id of the row.
            measure_quantity: New quantity of the row's measure, > 0.
            measure_id: New measure id.
            eaten: Mark the row eaten (True) or planned (False).
            name: New name (custom rows).
            source: New row source.
            patch: Other row fields in the API's camelCase; not its id or food.
        """
        return self._run(
            "update_day_item_via_api",
            lambda client, user_id: client.planner.update_day_item(
                user_id, as_date(target_date, "target_date"),
                meal_type=meal_type, item_id=item_id, measure_quantity=measure_quantity,
                measure_id=measure_id, eaten=eaten, name=name, source=source, patch=patch,
            ),
        )

    def remove_day_item_via_api(
        self, *, target_date: date | str, meal_type: str, item_id: str | int
    ) -> dict[str, Any]:
        """Delete a logged row and confirm it is gone.

        ``status`` is ``"partial"`` when the row was not there (``reason``
        ``item_not_found``, e.g. already removed) or is still there after the sync.

        Args:
            target_date: Day of the row.
            meal_type: Meal key of the row.
            item_id: Id of the row.
        """
        return self._run(
            "remove_day_item_via_api",
            lambda client, user_id: client.planner.remove_day_item(
                user_id, as_date(target_date, "target_date"), meal_type=meal_type, item_id=item_id
            ),
        )

    def move_day_item_via_api(
        self,
        *,
        from_date: date | str,
        from_meal_type: str,
        item_id: str | int,
        to_date: date | str | None = None,
        to_meal_type: str | None = None,
    ) -> dict[str, Any]:
        """Move a logged row to another meal and/or day; the copy gets a new ``row_id``.

        Args:
            from_date: Day of the row.
            from_meal_type: Meal key of the row.
            item_id: Id of the row.
            to_date: Target day; defaults to ``from_date``.
            to_meal_type: Target meal key; defaults to the row's meal.
        """
        return self._run(
            "move_day_item_via_api",
            lambda client, user_id: client.planner.move_day_item(
                user_id, as_date(from_date, "from_date"),
                from_meal_type=from_meal_type, item_id=item_id,
                to_day=None if to_date is None else as_date(to_date, "to_date"), to_meal_type=to_meal_type,
            ),
        )

    def replace_day_item_with_custom_item_via_api(
        self,
        *,
        target_date: date | str,
        meal_type: str,
        item_id: str | int,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        source: str = "API",
    ) -> dict[str, Any]:
        """Replace a logged row with a custom row in one sync.

        Args:
            target_date: Day of the row.
            meal_type: Meal key of the row.
            item_id: Id of the row.
            name: Name of the custom row.
            calories: Energy in kcal.
            protein_g: Protein in grams.
            fat_g: Fat in grams.
            carbs_g: Carbohydrate in grams.
            source: Row source.
        """
        return self._run(
            "replace_day_item_with_custom_item_via_api",
            lambda client, user_id: client.planner.replace_day_item_with_custom_item(
                user_id, as_date(target_date, "target_date"),
                meal_type=meal_type, item_id=item_id, name=name, calories=calories,
                protein_g=protein_g, fat_g=fat_g, carbs_g=carbs_g, source=source,
            ),
        )

    def remove_day_items_by_kind_via_api(
        self,
        *,
        target_date: date | str,
        item_kind: str,
        meal_type: str | None = None,
        all_meals: bool = False,
        row_ids: list[str] | None = None,
        max_items: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Delete every row of one kind in one meal, or with ``all_meals=True`` in the whole day.

        Args:
            target_date: Day of the rows.
            item_kind: ``auto`` (any row), ``normal_item``, ``custom_add_item`` or
                ``custom_recipe_item``.
            meal_type: Meal key; this or ``all_meals``.
            all_meals: Every meal of the day.
            row_ids: Only these row ids; one that is not a target makes the result partial.
            max_items: Delete at most this many rows.
            dry_run: Only list the ``targets``; nothing is deleted.
        """
        return self._run(
            "remove_day_items_by_kind_via_api",
            lambda client, user_id: client.planner.remove_day_items_by_kind(
                user_id, as_date(target_date, "target_date"),
                item_kind=item_kind, meal_type=meal_type, all_meals=all_meals,
                row_ids=row_ids, max_items=max_items, dry_run=dry_run,
            ),
        )

    def get_day_macros_via_api(
        self, *, target_date: date | str, include_meal_breakdown: bool = False
    ) -> dict[str, Any]:
        """Return a day's kcal and grams of protein, fat, carbohydrate, fiber, sugars and salt.

        ``totals`` counts every row, planned or eaten; ``eaten_totals`` only rows marked eaten.

        Args:
            target_date: Day to sum.
            include_meal_breakdown: Also return per-meal ``totals`` under ``meals``.
        """

        def macros(client: FitatuApiClient, user_id: str) -> dict[str, Any]:
            day = as_date(target_date, "target_date")
            summary = day_summary(client.planner.get_day(user_id, day), date=day.isoformat())
            result = {key: summary[key] for key in ("date", "totals", "eaten_totals")}
            if include_meal_breakdown:
                result["meals"] = {meal["meal_key"]: meal["totals"] for meal in summary["meals"]}
            return result

        return self._run("get_day_macros_via_api", macros)

    def get_day_summary_via_api(self, *, target_date: date | str) -> dict[str, Any]:
        """Return a day per meal with its rows, ``totals`` (every row) and ``eaten_totals``.

        Args:
            target_date: Day to summarise.
        """

        def summary(client: FitatuApiClient, user_id: str) -> dict[str, Any]:
            day = as_date(target_date, "target_date")
            return day_summary(client.planner.get_day(user_id, day), date=day.isoformat(), user_id=user_id)

        return self._run("get_day_summary_via_api", summary)

    def get_daily_targets_via_api(self, *, target_date: date | str | None = None) -> dict[str, Any]:
        """Return the energy (kcal) and macro targets (grams and percent) for a day, plus BMR/TMR.

        The full ``settings-new`` payload is under ``raw``.

        Args:
            target_date: Day the targets apply to; defaults to today.
        """

        def targets(client: FitatuApiClient, user_id: str) -> dict[str, Any]:
            day = date.today() if target_date is None else as_date(target_date, "target_date")
            settings = client.user_settings.get_new(user_id, day)
            return {**daily_targets(settings, day.isoformat()), "raw": settings}

        return self._run("get_daily_targets_via_api", targets)

    def log_weight_via_api(
        self, *, weight: float, target_date: date | str | None = None, weight_unit: str | None = None
    ) -> dict[str, Any]:
        """Log body weight for a day, keeping that day's other measurements.

        Args:
            weight: Body weight in ``weight_unit``.
            target_date: Day to log on; defaults to today.
            weight_unit: ``KG``, ``LB`` or ``ST_LB``; defaults to the day's stored unit, else ``KG``.
        """
        return self._run(
            "log_weight_via_api",
            lambda client, user_id: client.measurements.set_day(
                user_id, date.today() if target_date is None else as_date(target_date, "target_date"),
                weight=weight, weight_unit=weight_unit
            ),
        )

    def get_weight_history_via_api(
        self, *, from_date: date | str | None = None, to_date: date | str | None = None
    ) -> dict[str, Any]:
        """Return every weight entry in a range as ``[{date, value, difference, direction}]``.

        Pages are read until a short one. A history that has not ended after 1000 pages
        (41,000 entries) is an error rather than a silently cut list.

        Args:
            from_date: First day; open-ended when None.
            to_date: Last day; open-ended when None.
        """

        def history(client: FitatuApiClient, user_id: str) -> list[dict[str, Any]]:
            page_size, max_pages = 41, 1000  # the app asks for 41; the cap only stops a runaway loop
            entries: list[dict[str, Any]] = []
            for page in range(1, max_pages + 1):
                rows = client.measurements.get_weight_history(
                    user_id, from_date=from_date, to_date=to_date, page=page, limit=page_size
                )
                entries.extend(rows)
                if len(rows) < page_size:
                    return entries
            raise FitatuApiError(f"the weight history did not end after {max_pages} pages; pass a shorter range")

        return self._run("get_weight_history_via_api", history)

    def get_range_summary_via_api(self, *, from_date: date | str, to_date: date | str) -> dict[str, Any]:
        """Return the server's nutrition and activity totals for a date range.

        Args:
            from_date: First day.
            to_date: Last day.
        """
        return self._run(
            "get_range_summary_via_api",
            lambda client, user_id: {
                "from_date": as_date(from_date, "from_date").isoformat(),
                "to_date": as_date(to_date, "to_date").isoformat(),
                "nutrition": client.summary.get_nutrition(user_id, from_date, to_date),
                "activities": client.summary.get_activities(user_id, from_date, to_date),
            },
        )

    def export_diary_via_api(
        self,
        *,
        from_date: date | str,
        to_date: date | str,
        fmt: str = "csv",
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Export the food diary to a file, or return it in a JSON-safe form.

        Without ``path``, ``csv`` and ``txt`` come back as text in ``content``; ``pdf`` and
        ``xls`` as base64 in ``content_b64``, with their ``mime_type``. A file at ``path``
        is created readable by its owner only (0600), and an existing one is an error
        unless ``overwrite=True``. An empty range returns ``result`` ``None`` and writes
        nothing. The export needs Fitatu Premium: without it this is a ``forbidden`` error whose
        message says so.

        Args:
            from_date: First day.
            to_date: Last day.
            fmt: ``csv``, ``xls``, ``pdf`` or ``txt``.
            path: File to write the export to.
            overwrite: Replace an existing file at ``path``.
        """

        def export(client: FitatuApiClient, user_id: str) -> dict[str, Any] | None:
            content = client.summary.export_diary(user_id, from_date, to_date, fmt=fmt)
            if content is None:
                return None
            result: dict[str, Any] = {"format": fmt, "size_bytes": len(content)}
            if path is not None:
                write_private_file(path, content, overwrite=overwrite)
                result["path"] = str(path)
            elif fmt in ("csv", "txt"):
                result["content"] = content.decode("utf-8")
            else:
                result["content_b64"] = base64.b64encode(content).decode("ascii")
                result["mime_type"] = _BINARY_EXPORT_TYPES[fmt]
            return result

        return self._run("export_diary_via_api", export)

    def get_favorite_foods_via_api(self, *, phrase: str | None = None) -> dict[str, Any]:
        """Return the user's favourite products and recipes.

        Args:
            phrase: Only favourites matching this text.
        """
        return self._run(
            "get_favorite_foods_via_api", lambda client, user_id: client.foods.get_favorites(user_id, phrase=phrase)
        )

    def get_recent_foods_via_api(self, *, meal_type: str) -> dict[str, Any]:
        """Return the foods recently logged in a meal, e.g. for "the usual breakfast".

        Args:
            meal_type: Meal key.
        """
        return self._run(
            "get_recent_foods_via_api", lambda client, user_id: client.foods.get_recent(user_id, meal_type)
        )

    def search_food_via_api(
        self, *, phrase: str, limit: int = 10, page: int = 1, locale: str | None = None
    ) -> dict[str, Any]:
        """Search the food catalog (products and recipes); rows carry the id as ``foodId``.

        Args:
            phrase: Search text.
            limit: Rows per page.
            page: Page number, from 1.
            locale: Catalog locale such as ``pl_PL``; defaults to the session's search locale.
        """
        return self._run(
            "search_food_via_api",
            lambda client, _: client.search_food(phrase, locale=locale, limit=limit, page=page),
        )
