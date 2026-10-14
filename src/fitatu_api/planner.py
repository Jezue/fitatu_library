"""Planner-related Fitatu API helpers."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import date, datetime
from typing import Any, cast

from ._shapes import as_dict, as_dict_list, read_reply
from ._validation import (
    as_date,
    validate_bool,
    validate_non_negative_int,
    validate_numeric_id,
    validate_positive_int,
)
from .exceptions import (
    FitatuApiError,
    FitatuNotFoundError,
    FitatuPlannerSyncError,
    FitatuValidationError,
)

logger = logging.getLogger(__name__)

if False:  # pragma: no cover
    from .client import FitatuApiClient


def _new_row_id() -> str:
    """A UUID v1 for ``planDayDietItemId`` (the only version the day sync accepts).

    ``uuid.uuid1()`` would put this machine's MAC address into every row sent to Fitatu;
    a random node with the multicast bit set (RFC 4122, section 4.5) avoids that.
    """
    return str(uuid.uuid1(node=secrets.randbits(48) | 1 << 40))


def _row_id(row_id: str | None) -> str:
    """Return the caller's ``row_id`` (it must be a UUID v1) or a new one.

    The day sync upserts by row id, so a caller that picks the id can resend an add
    after a timeout without logging the food twice.
    """
    if row_id is None:
        return _new_row_id()
    try:
        parsed: uuid.UUID | None = uuid.UUID(str(row_id))
    except ValueError:
        parsed = None
    if parsed is None or parsed.version != 1:
        raise FitatuValidationError(f"row_id must be a UUID v1 (the only version the day sync accepts), got {row_id!r}")
    return str(parsed)


def _derived_row_id(seed: uuid.UUID, index: int) -> str:
    """The ``index``-th row id derived from ``seed`` (index 0 is ``seed`` itself).

    It differs from ``seed`` only in the clock sequence (bits 48-61), so the same seed and
    index always give the same id: a repeated multi-row write sends the same rows again.
    """
    return str(uuid.UUID(int=seed.int ^ (index << 48)))


class PlannerModule:
    """High-level planner API.

    This module contains the day snapshot sync flow used for planner reads and writes.
    It is the most domain-heavy part of the public API and is intentionally exposed as a
    first-class module on :class:`fitatu_api.FitatuApiClient`.

    Days are ``date`` objects or ``'YYYY-MM-DD'`` strings. Argument mistakes raise
    :class:`FitatuValidationError`; a meal or row that is not on the day raises
    :class:`FitatuNotFoundError`, whose message lists what the day has. Removals are
    the exception for rows: deleting one that is not there (any more) is not an error,
    so a repeated delete is safe; the result has ``ok`` False, ``reason``
    ``item_not_found`` and a ``message`` with the same listing.

    Row ids: ``item_id`` names a row that is already on the day (its
    ``planDayDietItemId``, or an activity's ``uuid``); ``row_id`` is the id to give a
    row being added, so a retried add cannot duplicate it. Adds, moves, replacements and
    activity writes return the id of the row they wrote as ``row_id``; updates report
    ``item_id`` and removals ``removed_id``.

    Results are dicts with snake_case keys; the values that are Fitatu payloads (the rows
    sent or read, ``sync_response``) keep the API's camelCase keys.
    """

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    @staticmethod
    def _values_match(expected: Any, actual: Any) -> bool:
        """Return True when expected and actual represent the same value, with float tolerance."""
        if expected is None:
            return actual is None
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            return abs(float(expected) - float(actual)) <= 1e-9
        if isinstance(expected, bool) or isinstance(actual, bool):
            return bool(expected) is bool(actual)
        return str(expected) == str(actual)

    @staticmethod
    def _now_timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def normalize_meal_key(meal_type: str) -> str:
        """Normalize external meal names to the planner payload shape."""
        if not isinstance(meal_type, str):
            raise FitatuValidationError(f"meal_type must be a meal name such as 'breakfast', got {meal_type!r}")
        meal_key = meal_type.strip().lower()
        aliases = {
            "second-breakfast": "second_breakfast",
            "second breakfast": "second_breakfast",
        }
        return aliases.get(meal_key, meal_key)

    # ------------------------------------------------------------------
    # Write model (checked against the live API, see tests/fixtures):
    # POST /diet-plan/{id}/days upserts rows by planDayDietItemId and keeps every
    # row that is not in the payload, so a write only needs the rows it changes.
    # A row is deleted by sending it with deletedAt; the day GET then hides it.
    # A row is replaced as a whole, so a changed row is re-sent in full.
    # ------------------------------------------------------------------

    @staticmethod
    def _diet_plan(day_data: dict[str, Any]) -> dict[str, Any]:
        diet_plan = as_dict(day_data.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")
        return diet_plan

    @classmethod
    def _meal(cls, day_data: dict[str, Any], meal_type: str) -> tuple[str, dict[str, Any]]:
        """Return ``(meal_key, meal bucket)``; a meal the day does not have is not found."""
        diet_plan = cls._diet_plan(day_data)
        meal_key = cls.normalize_meal_key(meal_type)
        bucket = as_dict(diet_plan.get(meal_key))
        if bucket is None:
            names = {key: (as_dict(meal) or {}).get("mealName") for key, meal in diet_plan.items()}
            listed = ", ".join(f"{key} ({name})" if name else key for key, name in sorted(names.items()))
            raise FitatuNotFoundError(f"meal not found: {meal_type!r}; this day has: {listed}")
        return meal_key, bucket

    @staticmethod
    def _iter_rows(day_data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Return ``(meal_key, row)`` for every row of a day GET response."""
        diet_plan = as_dict(day_data.get("dietPlan")) or {}
        return [
            (meal_key, row)
            for meal_key, bucket in diet_plan.items()
            for row in as_dict_list((as_dict(bucket) or {}).get("items"))
        ]

    @classmethod
    def _has_row(cls, day_data: dict[str, Any], row_id: str) -> bool:
        return any(str(row.get("planDayDietItemId")) == row_id for _, row in cls._iter_rows(day_data))

    @staticmethod
    def _describe_rows(rows: list[tuple[str, dict[str, Any]]]) -> str:
        """One line per row, enough for a caller to pick the right ``planDayDietItemId``."""
        return "; ".join(
            f"{row.get('planDayDietItemId')} ({meal_key}, {row.get('name')!r}, "
            f"measureQuantity={row.get('measureQuantity')})"
            for meal_key, row in rows
        ) or "none"

    @classmethod
    def _locate_row(
        cls,
        day_data: dict[str, Any],
        item_id: str | int,
        *,
        meal_type: str,
    ) -> tuple[str, dict[str, Any]] | None:
        """Find a row by ``planDayDietItemId`` in any meal, else by ``productId`` in ``meal_type``.

        A ``productId`` only counts when exactly one row of ``meal_type`` has it: with the
        same product logged twice, guessing would edit or delete the wrong one, so that
        raises FitatuValidationError listing the candidates. Raises when ``meal_type`` is
        not a meal of that day, so typos fail loudly.
        """
        meal_key = cls._meal(day_data, meal_type)[0]
        rows = cls._iter_rows(day_data)
        wanted = str(item_id)
        by_row_id = [entry for entry in rows if str(entry[1].get("planDayDietItemId")) == wanted]
        if by_row_id:
            return by_row_id[0]
        by_product = [(m, row) for m, row in rows if m == meal_key and str(row.get("productId")) == wanted]
        if len(by_product) > 1:
            raise FitatuValidationError(
                f"productId {wanted} matches {len(by_product)} rows in {meal_key}; pass the "
                f"planDayDietItemId of one: {cls._describe_rows(by_product)}"
            )
        return by_product[0] if by_product else None

    @classmethod
    def _row_not_found(cls, day_data: dict[str, Any], item_id: str | int) -> str:
        return f"item not found: {item_id!r}; rows on this day: {cls._describe_rows(cls._iter_rows(day_data))}"

    @classmethod
    def _find_row(cls, day_data: dict[str, Any], item_id: str | int, *, meal_type: str) -> tuple[str, dict[str, Any]]:
        """:meth:`_locate_row`, but a missing row is not found (the message lists the day's rows)."""
        found = cls._locate_row(day_data, item_id, meal_type=meal_type)
        if found is None:
            raise FitatuNotFoundError(cls._row_not_found(day_data, item_id))
        return found

    @staticmethod
    def _food_of(meal_key: str, row: dict[str, Any]) -> tuple[Any, ...]:
        """What a row logs: its meal, ``foodType``, food, measure, quantity and ``eaten``.

        A custom row is its name, nutrition and ``eaten`` instead: the server swaps its
        ``measureId`` (see the recording), so that cannot match.
        """
        food_type = str(row.get("foodType"))
        if food_type == "CUSTOM_ITEM":
            keys: tuple[str, ...] = ("name", "energy", "protein", "fat", "carbohydrate", "eaten")
        else:
            keys = ("productId", "recipeId", "measureId", "measureQuantity", "eaten")
        return (meal_key, food_type, *(row.get(key) for key in keys))

    def _meal_for_new_row(self, user_id: str, day: date, meal_type: str, new_row: dict[str, Any]) -> str:
        """Return the normalised meal key for an add, failing when the day has no such meal.

        The sync would replace a row that already has ``new_row``'s id, so that id is
        only taken as a retry when the row logs exactly what ``new_row`` does (see
        :meth:`_food_of`); a row that differs in any of that is refused, not overwritten.
        """
        day_data = self.get_day(user_id, day)
        meal_key = self._meal(day_data, meal_type)[0]
        self._refuse_taken_row_id(day_data, meal_key, new_row)
        return meal_key

    @classmethod
    def _refuse_taken_row_id(cls, day_data: dict[str, Any], meal_key: str, new_row: dict[str, Any]) -> None:
        """Raise when another row of ``day_data`` has ``new_row``'s id (see :meth:`_meal_for_new_row`)."""
        row_id = new_row["planDayDietItemId"]
        sent = cls._food_of(meal_key, new_row)
        for existing_meal, row in cls._iter_rows(day_data):
            if str(row.get("planDayDietItemId")) == row_id and not all(map(cls._values_match, sent, cls._food_of(existing_meal, row))):
                # Most often a retried add whose row was changed since (e.g. marked eaten on the phone).
                raise FitatuValidationError(
                    f"row_id {row_id} already belongs to another row on this day: "
                    f"{cls._describe_rows([(existing_meal, row)])}. If this add repeats an earlier one, that "
                    "attempt was saved and the row has changed since: do not add it again. Update that row "
                    "instead, or remove it and add it again"
                )

    def _upsert_rows(self, user_id: str, day: date, rows_by_meal: dict[str, list[dict[str, Any]]]) -> Any:
        """Send only these rows; synchronous so the next GET already sees them."""
        diet_plan = {meal_key: {"items": rows} for meal_key, rows in rows_by_meal.items() if rows}
        return self.sync_single_day(user_id, day, day_payload={"dietPlan": diet_plan})

    @classmethod
    def _mark_deleted(cls, row: dict[str, Any]) -> dict[str, Any]:
        """Return the row as the app deletes it: unchanged, plus ``deletedAt``."""
        return {**row, "deletedAt": cls._now_timestamp()}

    def _delete_rows(
        self,
        user_id: str,
        day: date,
        targets: list[tuple[str, dict[str, Any]]],
    ) -> tuple[Any, set[str]]:
        """Delete rows in one sync and return ``(sync_response, ids still visible)``."""
        rows_by_meal: dict[str, list[dict[str, Any]]] = {}
        for meal_key, row in targets:
            rows_by_meal.setdefault(meal_key, []).append(self._mark_deleted(row))
        sync_response = self._upsert_rows(user_id, day, rows_by_meal)
        visible = {str(row.get("planDayDietItemId")) for _, row in self._iter_rows(self.get_day(user_id, day))}
        wanted = {str(row.get("planDayDietItemId")) for _, row in targets}
        return sync_response, wanted & visible

    def _remove_row(
        self,
        user_id: str,
        day: date,
        day_data: dict[str, Any],
        found: tuple[str, dict[str, Any]] | None,
        *,
        meal_key: str,
        item_id: str | int,
    ) -> dict[str, Any]:
        """Delete the ``(meal_key, row)`` found in ``day_data`` and confirm it is gone."""
        if found is None:
            return {
                "ok": False,
                "meal": meal_key,
                "removed_id": None,
                "removed_absent_after_sync": False,
                "cleanup_mode": "none",
                "reason": "item_not_found",
                "message": self._row_not_found(day_data, item_id),
                "removed": None,
                "sync_response": None,
            }
        actual_meal, row = found
        sync_response, still_visible = self._delete_rows(user_id, day, [(actual_meal, row)])
        removed = not still_visible
        return {
            "ok": removed,
            "meal": actual_meal,
            "removed_id": str(row.get("planDayDietItemId")),
            "removed_absent_after_sync": removed,
            "cleanup_mode": "removed" if removed else "none",
            "removed": row,
            "sync_response": sync_response,
        }

    def get_day(self, user_id: str, day: date | str) -> dict[str, Any]:
        """Fetch a single planner day snapshot."""
        path = f"/diet-and-activity-plan/{validate_numeric_id(user_id, 'user_id')}/day/{as_date(day).isoformat()}"
        return read_reply(self._client.request("GET", path), f"GET {path}")

    def set_note(self, user_id: str, day: date | str, *, note: str | None) -> dict[str, Any]:
        """Replace the day's note with ``note``; ``None`` or ``""`` deletes it.

        The note goes in the day sync with the day's note tags as they are (``tagsIds``, read
        first); nothing else of the day is sent, and its rows, activities and water stay as they
        are (checked live 2026-10-14). The day is re-read: ``ok`` says it has the new note, and
        ``previous`` is the note it had before (None without one).
        """
        if note is not None and not isinstance(note, str):
            raise FitatuValidationError(f"note must be text or None, got {note!r}")
        day = as_date(day)
        before = self.get_day(user_id, day)
        tags = before.get("tagsIds") if isinstance(before.get("tagsIds"), list) else []
        text = note or None  # the app deletes a note by sending null
        sync_response = self.sync_single_day(user_id, day, day_payload={"note": text, "tagsIds": tags})
        return {
            "ok": (self.get_day(user_id, day).get("note") or None) == text,
            "note": text,
            "previous": before.get("note") or None,
            "sync_response": sync_response,
        }

    def get_meal(self, user_id: str, day: date | str, *, meal_type: str) -> dict[str, Any]:
        """Return a single meal bucket from a planner day."""
        return self._meal(self.get_day(user_id, as_date(day)), meal_type)[1]

    def list_meal_items(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
    ) -> list[dict[str, Any]]:
        """List items inside a meal bucket."""
        return as_dict_list(self.get_meal(user_id, as_date(day), meal_type=meal_type).get("items"))

    def find_meal_item(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        query: str,
    ) -> dict[str, Any] | None:
        """Find the first meal item by case-insensitive name match."""
        day = as_date(day)
        if not isinstance(query, str):
            raise FitatuValidationError(f"query must be a string, got {query!r}")
        needle = query.strip().lower()
        if not needle:
            return None
        for item in self.list_meal_items(user_id, day, meal_type=meal_type):
            if needle in str(item.get("name") or "").lower():
                return item
        return None

    @staticmethod
    def _extract_measure_id(product: dict[str, Any]) -> int | str | None:
        """The measure id a product or search-result dict names, or None when it names none.

        Not a guess: the day sync accepts any measure id and stores the quantity in it.
        """
        measure = as_dict(product.get("measure")) or {}
        return (
            measure.get("defaultMeasureId")
            or measure.get("measureId")
            or product.get("defaultMeasureId")
            or product.get("measureId")
        )

    @staticmethod
    def _normalize_measure_unit(unit: str | None) -> str | None:
        """Normalise a free-text unit alias (e.g. "grams", "ml") to a canonical form."""
        if unit is None:
            return None
        text = str(unit).strip().lower()
        if not text:
            return None
        aliases = {
            "gram": "g",
            "grams": "g",
            "gr": "g",
            "g": "g",
            "mililitr": "ml",
            "mililitry": "ml",
            "milliliter": "ml",
            "milliliters": "ml",
            "ml": "ml",
            "opak": "opakowanie",
            "opakowanie": "opakowanie",
            "package": "opakowanie",
            "pack": "opakowanie",
            "szt": "sztuka",
            "sztuka": "sztuka",
            "piece": "sztuka",
            "porcja": "porcja",
            "portion": "porcja",
            "serving": "porcja",
            "lyzeczka": "łyżeczka",
            "łyżeczka": "łyżeczka",
            "łyżeczki": "łyżeczka",
            "teaspoon": "łyżeczka",
            "tsp": "łyżeczka",
            "lyzka": "łyżka",
            "łyżka": "łyżka",
            "łyżki": "łyżka",
            "tablespoon": "łyżka",
            "tbsp": "łyżka",
            "szklanka": "szklanka",
            "szklanki": "szklanka",
            "glass": "szklanka",
            "cup": "szklanka",
            "plaster": "plaster",
            "plastry": "plaster",
            "slice": "plaster",
            "kostka": "kostka",
            "kostki": "kostka",
            "cube": "kostka",
        }
        return aliases.get(text, text)

    @staticmethod
    def _coerce_positive_float(value: float | int, *, field_name: str, allow_zero: bool = False) -> float:
        """Parse *value* to a positive (``allow_zero``: non-negative) float, or raise FitatuValidationError."""
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise FitatuValidationError(f"{field_name} must be numeric, got {value!r}") from exc
        if not ((parsed >= 0 if allow_zero else parsed > 0) and parsed < float("inf")):
            raise FitatuValidationError(f"{field_name} must be {'>=' if allow_zero else '>'} 0, got {value!r}")
        return parsed

    @staticmethod
    def _measure_name_matches_unit(measure_name: str, canonical_unit: str) -> bool:
        """Return True when a raw measure name corresponds to a canonical unit string."""
        name = measure_name.strip().lower()
        if not name:
            return False
        if canonical_unit == "g":
            return name in {"g", "gram", "gramy", "grams"}
        if canonical_unit == "ml":
            return name in {"ml", "mililitr", "mililitry", "milliliter", "milliliters"}
        if canonical_unit == "opakowanie":
            return "opak" in name or "pack" in name or "package" in name
        if canonical_unit == "sztuka":
            return "szt" in name or "piece" in name
        if canonical_unit == "porcja":
            return "porc" in name or "portion" in name or "serving" in name
        if canonical_unit == "łyżeczka":
            return name in {"łyżeczka", "lyzeczka", "łyżeczki", "teaspoon", "tsp"}
        if canonical_unit == "łyżka":
            return name in {"łyżka", "lyzka", "łyżki", "tablespoon", "tbsp"}
        if canonical_unit == "szklanka":
            return name in {"szklanka", "szklanki", "glass", "cup"}
        if canonical_unit == "plaster":
            return name in {"plaster", "plastry", "slice"}
        if canonical_unit == "kostka":
            return name in {"kostka", "kostki", "cube"}
        return name == canonical_unit

    @staticmethod
    def _optional_positive_float(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if parsed > 0 else 0.0

    def _extract_measure_candidates(
        self,
        product_details: dict[str, Any],
        *,
        search_product: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return all measure option dicts from a product details payload."""
        measures_raw = product_details.get("measures")
        simple_raw = product_details.get("simpleMeasures")

        candidates: list[dict[str, Any]] = []

        def add_candidate(
            *,
            measure_id: Any,
            name: Any,
            weight_per_unit: Any = 0,
            capacity_per_unit: Any = 0,
            source: str,
        ) -> None:
            if measure_id is None:
                return
            measure_name = str(name or "").strip()
            weight = self._optional_positive_float(weight_per_unit)
            capacity = self._optional_positive_float(capacity_per_unit)
            candidate = {
                "measureId": measure_id,
                "measureName": measure_name,
                "weightPerUnit": weight,
                "capacityPerUnit": capacity,
                "source": source,
            }

            for existing in candidates:
                if (
                    str(existing.get("measureId")) == str(measure_id)
                    and str(existing.get("measureName") or "").strip().lower()
                    == measure_name.lower()
                    and float(existing.get("weightPerUnit") or 0) == weight
                    and float(existing.get("capacityPerUnit") or 0) == capacity
                ):
                    return
            candidates.append(candidate)

        if isinstance(measures_raw, list):
            for measure_raw in measures_raw:
                measure = as_dict(measure_raw)
                if measure is None:
                    continue
                add_candidate(
                    measure_id=measure.get("id"),
                    name=measure.get("name"),
                    weight_per_unit=measure.get("weightPerUnit"),
                    capacity_per_unit=measure.get("capacityPerUnit"),
                    source="measures",
                )

        if isinstance(simple_raw, list):
            for row_raw in simple_raw:
                row = as_dict(row_raw)
                if row is None:
                    continue
                portion = self._optional_positive_float(row.get("portion")) or 1.0
                weight = self._optional_positive_float(row.get("weight")) / portion
                capacity = self._optional_positive_float(row.get("capacity")) / portion
                add_candidate(
                    measure_id=row.get("id"),
                    name=row.get("name"),
                    weight_per_unit=weight,
                    capacity_per_unit=capacity,
                    source="simpleMeasures",
                )

        if search_product is not None:
            search_measure = as_dict(search_product.get("measure")) or {}
            quantity = self._optional_positive_float(search_measure.get("measureQuantity")) or 1.0
            add_candidate(
                measure_id=search_measure.get("defaultMeasureId") or search_measure.get("measureId"),
                name=search_measure.get("measureName"),
                weight_per_unit=self._optional_positive_float(search_measure.get("measureWeight"))
                / quantity,
                capacity_per_unit=self._optional_positive_float(search_measure.get("measureCapacity"))
                / quantity,
                source="searchMeasure",
            )

        return candidates

    @staticmethod
    def _measure_names(candidates: list[dict[str, Any]]) -> str:
        return ", ".join(sorted({str(c["measureName"]) for c in candidates if c["measureName"]})) or "none"

    def resolve_product_measure(
        self,
        *,
        product_id: int | str,
        requested_amount: float | int,
        requested_unit: str,
        strict_measure: bool = True,
        search_product: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve measure id and quantity for a target unit (g/ml/opakowanie/etc.).

        Returns a dict with keys: ``measure_id``, ``measure_name``,
        ``measure_quantity``, ``requested_amount``, ``requested_unit``,
        ``strategy``, ``strict_measure``, ``warnings``, ``product_id``.

        ``g`` and ``ml`` are converted through any measure with a known weight or
        capacity per unit (``converted_from_weight``/``converted_from_capacity``).

        Fallback behaviour (``strict_measure=False``, ``g``/``ml`` only, when no measure
        of the product has a weight or capacity to convert with):

        - ``fallback_search_measure``: the default measure of the search result is
          used and ``measure_quantity`` is ``requested_amount`` as it is, not converted.
        - ``fallback_first_measure``: no search result provided; the first product
          measure is used, quantity likewise unchanged.

        When a fallback fires, ``warnings`` is non-empty.  Always check it::

            result = planner.resolve_product_measure(...)
            if result["warnings"]:
                # measure was substituted — verify quantities are sensible

        Any other unit (``sztuka``, ``łyżka``...) that the product has no measure for
        raises FitatuValidationError even with ``strict_measure=False``: its size is
        unknown, so no other measure can be converted to it. With ``strict_measure=True``
        (default) every unresolvable unit raises.
        """
        amount = self._coerce_positive_float(requested_amount, field_name="requested_amount")
        canonical_unit = self._normalize_measure_unit(requested_unit)
        if canonical_unit is None:
            raise FitatuValidationError("requested_unit is required")

        product_details = self._client.get_product_details(product_id)
        candidates = self._extract_measure_candidates(
            product_details,
            search_product=search_product,
        )

        direct_match: dict[str, Any] | None = None
        for candidate in candidates:
            if self._measure_name_matches_unit(str(candidate.get("measureName") or ""), canonical_unit):
                direct_match = candidate
                break

        strategy = "direct_unit_match"
        warnings: list[str] = []
        selected_measure_id: Any | None = None
        selected_measure_name = ""
        selected_measure_quantity = amount

        if direct_match is not None:
            selected_measure_id = direct_match.get("measureId")
            selected_measure_name = str(direct_match.get("measureName") or "")
            strategy = (
                "direct_unit_match"
                if direct_match.get("source") == "measures"
                else f"direct_unit_match_{direct_match.get('source')}"
            )
        else:
            if canonical_unit == "g":
                weight_candidates = [c for c in candidates if float(c.get("weightPerUnit") or 0) > 0]
                if weight_candidates:
                    best = min(weight_candidates, key=lambda c: abs(float(c.get("weightPerUnit") or 0) - 1.0))
                    weight_per_unit = float(best.get("weightPerUnit") or 0)
                    selected_measure_id = best.get("measureId")
                    selected_measure_name = str(best.get("measureName") or "")
                    selected_measure_quantity = amount / weight_per_unit
                    strategy = "converted_from_weight"
            elif canonical_unit == "ml":
                capacity_candidates = [c for c in candidates if float(c.get("capacityPerUnit") or 0) > 0]
                if capacity_candidates:
                    best = min(capacity_candidates, key=lambda c: abs(float(c.get("capacityPerUnit") or 0) - 1.0))
                    capacity_per_unit = float(best.get("capacityPerUnit") or 0)
                    selected_measure_id = best.get("measureId")
                    selected_measure_name = str(best.get("measureName") or "")
                    selected_measure_quantity = amount / capacity_per_unit
                    strategy = "converted_from_capacity"

        if selected_measure_id is None:
            if strict_measure or canonical_unit not in {"g", "ml"}:
                raise FitatuValidationError(
                    f"Cannot resolve unit '{requested_unit}' for product {product_id}; "
                    f"its measures: {self._measure_names(candidates)}"
                )
            search_measure_id = self._extract_measure_id(search_product) if search_product is not None else None
            if search_product is not None and search_measure_id is not None:
                selected_measure_id = search_measure_id
                search_measure = as_dict(search_product.get("measure")) or {}
                selected_measure_name = str(search_measure.get("measureName") or "")
                strategy = "fallback_search_measure"
                warnings.append(
                    "Requested unit not available; falling back to default search measure"
                )
            elif candidates:
                selected_measure_id = candidates[0].get("measureId")
                selected_measure_name = str(candidates[0].get("measureName") or "")
                strategy = "fallback_first_measure"
                warnings.append(
                    "Requested unit not available; falling back to first product measure"
                )
            else:
                raise FitatuValidationError(f"No measures available for product {product_id}")

        return {
            "measure_id": selected_measure_id,
            "measure_name": selected_measure_name,
            "measure_quantity": selected_measure_quantity,
            "requested_amount": amount,
            "requested_unit": canonical_unit,
            "strategy": strategy,
            "strict_measure": strict_measure,
            "warnings": warnings,
            "product_id": product_id,
        }

    def add_product_to_day_meal_with_unit(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        product_id: int | str,
        amount: float | int,
        unit: str,
        eaten: bool = False,
        source: str = "API",
        strict_measure: bool = True,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a product to a planner meal using user-facing unit semantics.

        ``row_id`` works as in :meth:`add_product_to_day_meal`.
        """
        day = as_date(day)
        row_id = _row_id(row_id)
        resolution = self.resolve_product_measure(
            product_id=product_id,
            requested_amount=amount,
            requested_unit=unit,
            strict_measure=strict_measure,
        )
        result = self.add_product_to_day_meal(
            user_id,
            day,
            meal_type=meal_type,
            product_id=product_id,
            measure_id=cast(int | str, resolution["measure_id"]),
            measure_quantity=cast(float | int, resolution["measure_quantity"]),
            eaten=eaten,
            source=source,
            row_id=row_id,
        )
        result["measure_resolution"] = resolution
        return result

    def add_product_to_day_meal(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        product_id: int | str,
        measure_id: int | str,
        measure_quantity: float | int = 1,
        eaten: bool = False,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a product to a planner meal. Only the new row is sent.

        ``row_id`` (a UUID v1) becomes the row's ``planDayDietItemId``; pass your own to
        make a retry safe, since resending the same id updates the row instead of adding
        a second one. An id that is already on the day with another food, meal, measure,
        quantity or ``eaten`` raises FitatuValidationError instead of overwriting that row
        (change a row with :meth:`update_day_item`). Without it a new id is generated.
        Either way it comes back as ``row_id``.
        """
        day = as_date(day)
        new_item = self._product_row(
            product_id=product_id, measure_id=measure_id, measure_quantity=measure_quantity, eaten=eaten, source=source, row_id=row_id
        )
        meal_key = self._meal_for_new_row(user_id, day, meal_type, new_item)
        logger.debug("add_product user=%s day=%s meal=%s product=%s qty=%s", user_id, day, meal_key, product_id, new_item["measureQuantity"])
        sync_response = self._upsert_rows(user_id, day, {meal_key: [new_item]})
        return {
            "ok": True,
            "row_id": new_item["planDayDietItemId"],
            "meal": meal_key,
            "product_id": product_id,
            "sync_response": sync_response,
            "added_item": new_item,
        }

    def add_recipe_to_day_meal(
        self,
        user_id: str,
        day: date | str,
        *,
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
        """Add a recipe to a planner meal. Only the new row is sent.

        The server fills name and nutrition from ``recipeId`` on its own (checked live).
        ``row_id`` works as in :meth:`add_product_to_day_meal`.
        """
        day = as_date(day)
        new_item = self._recipe_row(
            recipe_id=recipe_id,
            food_type=food_type,
            measure_id=measure_id,
            measure_quantity=measure_quantity,
            ingredients_serving=ingredients_serving,
            eaten=eaten,
            source=source,
            row_id=row_id,
        )
        meal_key = self._meal_for_new_row(user_id, day, meal_type, new_item)
        logger.debug("add_recipe user=%s day=%s meal=%s recipe=%s", user_id, day, meal_key, recipe_id)
        sync_response = self._upsert_rows(user_id, day, {meal_key: [new_item]})
        return {
            "ok": True,
            "row_id": new_item["planDayDietItemId"],
            "meal": meal_key,
            "recipe_id": recipe_id,
            "food_type": new_item["foodType"],
            "sync_response": sync_response,
            "added_item": new_item,
        }

    @classmethod
    def _product_row(
        cls,
        *,
        product_id: int | str,
        measure_id: int | str,
        measure_quantity: float | int = 1,
        eaten: bool = False,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """The sync row that adds a product, as :meth:`add_product_to_day_meal` sends it."""
        validate_numeric_id(product_id, "product_id")
        validate_numeric_id(measure_id, "measure_id")
        validate_bool(eaten, "eaten")
        quantity = cls._coerce_positive_float(measure_quantity, field_name="measure_quantity")
        return {
            "planDayDietItemId": _row_id(row_id),
            "foodType": "PRODUCT",
            "measureId": measure_id,
            "measureQuantity": quantity,
            "ingredientsServing": None,
            "productId": product_id,
            "source": source,
            "eaten": eaten,
        }

    @classmethod
    def _recipe_row(
        cls,
        *,
        recipe_id: int | str,
        food_type: str = "RECIPE",
        measure_id: int | str = 39,
        measure_quantity: float | int = 1,
        ingredients_serving: float | int | None = 1,
        eaten: bool = False,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """The sync row that adds a recipe, as :meth:`add_recipe_to_day_meal` sends it."""
        validate_numeric_id(recipe_id, "recipe_id")
        validate_numeric_id(measure_id, "measure_id")
        validate_bool(eaten, "eaten")
        quantity = cls._coerce_positive_float(measure_quantity, field_name="measure_quantity")
        normalized_food_type = str(food_type or "RECIPE").strip().upper()
        if normalized_food_type not in {"RECIPE", "RECIPE_AI"}:
            raise FitatuValidationError(f"food_type must be RECIPE or RECIPE_AI, got {food_type!r}")
        return {
            "planDayDietItemId": _row_id(row_id),
            "foodType": normalized_food_type,
            "recipeId": recipe_id,
            "measureId": measure_id,
            "measureQuantity": quantity,
            "ingredientsServing": ingredients_serving,
            "source": source,
            "eaten": eaten,
        }

    def add_foods_to_day_meal(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        items: list[dict[str, Any]],
        eaten: bool = False,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Add several products and recipes to a meal in one sync. Only the new rows are sent.

        Each item holds the keyword arguments of :meth:`add_product_to_day_meal`
        (``product_id``, ``measure_id``, ``measure_quantity``) or, with a ``recipe_id``, of
        :meth:`add_recipe_to_day_meal` (``measure_id`` 39 and ``measure_quantity`` 1 unless
        given); ``eaten`` and ``source`` apply to every row. Every item, the meal and the ids
        are checked before anything is sent, and an item's error names its index
        (``items[1]: ...``). ``row_id`` (a UUID v1, else a new one) seeds the rows' ids as in
        :meth:`copy_meal`: a repeat with the same ``row_id`` and items writes the same rows
        again, and an id another row of the day already has raises FitatuValidationError, as
        for the single adds. ``ok`` is True once the sync accepted the rows (they are not read
        back); ``row_ids`` follow the order of ``items``.
        """
        day = as_date(day)
        if not isinstance(items, list) or not items:
            raise FitatuValidationError(f"items must list at least one product or recipe, got {items!r:.100}")
        seed = uuid.UUID(_row_id(row_id))
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            common: dict[str, Any] = {"eaten": eaten, "source": source, "row_id": _derived_row_id(seed, index)}
            try:
                rows.append(self._recipe_row(**item, **common) if "recipe_id" in item else self._product_row(**item, **common))
            except (FitatuValidationError, TypeError) as exc:  # TypeError: a key that is unknown, missing or given twice
                raise FitatuValidationError(f"items[{index}]: {exc}") from None
        day_data = self.get_day(user_id, day)
        meal_key = self._meal(day_data, meal_type)[0]
        for row in rows:
            self._refuse_taken_row_id(day_data, meal_key, row)
        logger.debug("add_foods user=%s day=%s meal=%s rows=%s", user_id, day, meal_key, len(rows))
        sync_response = self._upsert_rows(user_id, day, {meal_key: rows})
        return {
            "ok": True,
            "row_ids": [row["planDayDietItemId"] for row in rows],
            "meal": meal_key,
            "added_items": rows,
            "sync_response": sync_response,
        }

    def update_day_item(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        item_id: str | int,
        measure_quantity: float | int | None = None,
        measure_id: str | int | None = None,
        eaten: bool | None = None,
        name: str | None = None,
        source: str | None = None,
        patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update one planner row and check the result after a reload.

        ``item_id`` is the row's ``planDayDietItemId`` (found in any meal); a ``productId``
        works only when exactly one row of ``meal_type`` has it. The row is re-sent in full
        (the server replaces rows as a whole), so fields you do not touch, ``eaten``
        included, keep their values. ``patch`` (a dict) sets other fields of the row, but
        not its identity or food: ``planDayDietItemId``, ``deletedAt``, ``foodType``,
        ``productId`` and ``recipeId`` are refused (a different food is a remove plus an
        add), and its ``measureQuantity`` must be > 0 like ``measure_quantity``.
        """
        day = as_date(day)
        if not isinstance(patch or {}, dict):
            raise FitatuValidationError(f"patch must be a dict of row fields, got {patch!r}")
        patch = dict(patch or {})
        protected = sorted({"planDayDietItemId", "deletedAt", "foodType", "productId", "recipeId"} & set(patch))
        if protected:
            raise FitatuValidationError(f"patch must not set {', '.join(protected)}")
        if "measureQuantity" in patch:
            self._coerce_positive_float(patch["measureQuantity"], field_name="patch['measureQuantity']")
        if measure_quantity is not None:
            measure_quantity = self._coerce_positive_float(measure_quantity, field_name="measure_quantity")
        if measure_id is not None:
            validate_numeric_id(measure_id, "measure_id")
        if eaten is not None:
            validate_bool(eaten, "eaten")
        meal_key = self.normalize_meal_key(meal_type)
        actual_meal_key, row = self._find_row(self.get_day(user_id, day), item_id, meal_type=meal_type)
        before_item = dict(row)

        updates_applied: dict[str, Any] = {
            key: value
            for key, value in (
                ("measureQuantity", measure_quantity),
                ("measureId", measure_id),
                ("eaten", eaten),
                ("name", name),
                ("source", source),
            )
            if value is not None
        }
        updates_applied.update(patch)
        sync_response = self._upsert_rows(user_id, day, {actual_meal_key: [{**row, **updates_applied}]})

        reloaded = self._locate_row(
            self.get_day(user_id, day), str(before_item.get("planDayDietItemId") or item_id), meal_type=meal_key
        )
        matched_after = reloaded[1] if reloaded else None
        validation = {
            key: self._values_match(expected, matched_after.get(key) if matched_after else None)
            for key, expected in updates_applied.items()
        }
        ok = bool(matched_after) and all(validation.values())
        return {
            "ok": ok,
            "meal": actual_meal_key,
            "requested_meal": meal_key,
            "item_id": str(before_item.get("planDayDietItemId") or item_id),
            "product_id": before_item.get("productId"),
            "before": before_item,
            "after": matched_after,
            "updates": updates_applied,
            "validation": validation,
            "sync_response": sync_response,
        }

    def add_custom_item_to_day_meal(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        eaten: bool = False,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a custom row (name + kcal/macros) to a planner meal. Only the new row is sent.

        ``row_id`` works as in :meth:`add_product_to_day_meal`.
        """
        day = as_date(day)
        validate_bool(eaten, "eaten")
        new_item = self._custom_item_from_values(
            name=name, calories=calories, protein_g=protein_g, fat_g=fat_g, carbs_g=carbs_g, source=source, row_id=row_id
        )
        new_item["eaten"] = eaten
        meal_key = self._meal_for_new_row(user_id, day, meal_type, new_item)
        logger.debug("add_custom_item user=%s day=%s meal=%s name=%r kcal=%s", user_id, day, meal_key, name, calories)
        sync_response = self._upsert_rows(user_id, day, {meal_key: [new_item]})
        return {
            "ok": True,
            "row_id": new_item["planDayDietItemId"],
            "meal": meal_key,
            "name": new_item["name"],
            "sync_response": sync_response,
            "added_item": new_item,
        }

    @staticmethod
    def _custom_item_from_values(
        *,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        source: str = "API",
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a CUSTOM_ITEM sync row. Shape confirmed in production (2026-04-22).

        kcal and grams must be >= 0; a negative value raises rather than being logged as 0.
        """
        item_name = name.strip() if isinstance(name, str) else ""
        if not item_name:
            raise FitatuValidationError(f"Custom item name is required, got {name!r}")
        nutrition = {
            key: PlannerModule._coerce_positive_float(value, field_name=arg, allow_zero=True)
            for key, arg, value in (
                ("energy", "calories", calories),
                ("protein", "protein_g", protein_g),
                ("fat", "fat_g", fat_g),
                ("carbohydrate", "carbs_g", carbs_g),
            )
        }
        return {
            "planDayDietItemId": _row_id(row_id),
            "foodType": "CUSTOM_ITEM",
            "name": item_name,
            **nutrition,
            "measureId": 1,
            "measureQuantity": 100,
            "measureWeight": 100,
            "measureCapacity": 0,
            "source": source,
            "updatedAt": PlannerModule._now_timestamp(),
        }

    def rollback_added_item(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        item_id: str,
    ) -> dict[str, Any]:
        """Delete a row that was just added to ``meal_type``; ``item_id`` is the add's ``row_id``.

        Only the row with that ``planDayDietItemId`` in that meal is deleted, never another
        row of the same food: a rollback of an add that never landed, or a repeated one,
        leaves the day alone and returns the result of :meth:`remove_day_item` for a
        missing row (``ok`` False, ``reason`` ``item_not_found``).
        """
        day = as_date(day)
        day_data = self.get_day(user_id, day)
        meal_key = self._meal(day_data, meal_type)[0]
        found = next(
            (
                (meal, row)
                for meal, row in self._iter_rows(day_data)
                if meal == meal_key and str(row.get("planDayDietItemId")) == str(item_id)
            ),
            None,
        )
        return self._remove_row(user_id, day, day_data, found, meal_key=meal_key, item_id=item_id)

    def add_search_result_to_day_meal(
        self,
        user_id: str,
        day: date | str,
        *,
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
        """Search food and add one hit to a meal: a PRODUCT hit by ``productId``, a RECIPE
        hit by ``recipeId`` (search rows carry either id as ``foodId``). A hit of any other
        type raises FitatuValidationError.

        Without ``measure_unit`` the hit's own search measure is used, ``measure_quantity``
        (or ``measure_amount``) of it. ``measure_unit`` with ``measure_amount`` is resolved
        through the product's measures (:meth:`resolve_product_measure`) and works for
        PRODUCT hits only; a recipe is logged in its search measure, usually portions. A hit
        that names no measure raises FitatuValidationError rather than guessing one.
        ``row_id`` works as in :meth:`add_product_to_day_meal`.
        """
        day = as_date(day)
        validate_non_negative_int(index, "index")
        row_id = _row_id(row_id)
        search_items = self._client.search_food(phrase, limit=index + 1)
        if index >= len(search_items):
            raise FitatuNotFoundError(f"No search result at index={index} for phrase={phrase!r} ({len(search_items)} hits)")
        hit = search_items[index]
        food_id = hit.get("foodId") or hit.get("id")
        if food_id is None:
            raise FitatuApiError("Selected search item has no foodId/id")
        hit_type = str(hit.get("type") or "").upper()
        if hit_type not in {"PRODUCT", "RECIPE"}:
            raise FitatuValidationError(
                f"search hit {hit.get('name')!r} is of type {hit.get('type')!r}; only PRODUCT and RECIPE hits can be added"
            )
        is_recipe = hit_type == "RECIPE"
        search_measure = as_dict(hit.get("measure")) or {}
        if is_recipe and measure_unit is not None:
            raise FitatuValidationError(
                f"measure_unit works for products only; {hit.get('name')!r} is a recipe, pass "
                f"measure_quantity in its measure ({search_measure.get('measureName')})"
            )

        amount = (
            measure_quantity
            if measure_amount is None
            else self._coerce_positive_float(measure_amount, field_name="measure_amount")
        )
        if measure_unit is not None:
            resolution = self.resolve_product_measure(
                product_id=food_id,
                requested_amount=amount,
                requested_unit=measure_unit,
                strict_measure=strict_measure,
                search_product=hit,
            )
        else:
            measure_id = self._extract_measure_id(hit)
            if measure_id is None:
                raise FitatuValidationError(
                    f"search hit {hit.get('name')!r} names no measure; for a product pass measure_amount with "
                    "measure_unit ('g' or 'ml'), or add it by id with a measure from get_product_details()"
                )
            resolution = {
                "measure_id": measure_id,
                "measure_name": search_measure.get("measureName"),
                "measure_quantity": amount,
                "requested_amount": amount,
                "requested_unit": None,
                "strategy": "search_default",
                "strict_measure": strict_measure,
                "warnings": [],
            }

        row = {
            "meal_type": meal_type,
            "measure_id": resolution["measure_id"],
            "measure_quantity": resolution["measure_quantity"],
            "eaten": eaten,
            "row_id": row_id,
        }
        if is_recipe:
            result = self.add_recipe_to_day_meal(user_id, day, recipe_id=food_id, **row)
        else:
            result = self.add_product_to_day_meal(user_id, day, product_id=food_id, **row)
        result["search_item"] = {
            "name": hit.get("name"),
            "type": hit.get("type"),
            "food_id": hit.get("foodId"),
            "id": hit.get("id"),
            "measure_id": resolution["measure_id"],
        }
        result["measure_resolution"] = resolution
        return result

    def sync_days(
        self,
        user_id: str,
        days_payload: dict[str, Any],
        *,
        synchronous: bool = True,
    ) -> Any:
        """Sync one or more planner days.

        Synchronous by default, so a GET right after it already sees the change. The
        sync upserts rows by id, so resending the same payload is safe and the request
        is retried like a read on a network error, 429 or 5xx.

        ``days_payload`` keys must be ISO-format date strings (``date.isoformat()``),
        not ``date`` objects — the payload is serialised directly to JSON::

            planner.sync_days(user_id, {date.today().isoformat(): day_snapshot})

        Returns the server's per-day result list, e.g.
        ``[{"date": "2026-10-13", "revision": "...", "errorMessage": None}]``.

        Raises:
            FitatuPlannerSyncError: When any day comes back with an ``errorMessage``.
                The endpoint still answers 202 in that case and nothing is saved for
                the rejected day (e.g. a ``planDayDietItemId`` that is not a UUID v1).
        """
        if not isinstance(days_payload, dict) or not all(isinstance(key, str) for key in days_payload):
            raise FitatuValidationError(
                f"days_payload must map 'YYYY-MM-DD' strings to day payloads, got {days_payload!r:.100}"
            )
        response = self._client.request(
            "POST",
            f"/diet-plan/{validate_numeric_id(user_id, 'user_id')}/days",
            json_data=days_payload,
            params={"synchronous": "true"} if synchronous else None,
            idempotent=True,
        )
        if isinstance(response, list):
            rejected = [row for row in response if isinstance(row, dict) and row.get("errorMessage")]
            if rejected:
                details = "; ".join(f"{row.get('date')}: {row.get('errorMessage')}" for row in rejected)
                raise FitatuPlannerSyncError(f"Planner sync rejected: {details}", results=response)
        return response

    def get_day_revisions(self, user_id: str, day: date | str) -> list[str]:
        """Return the day's revision ids, newest first.

        Every accepted sync adds one, so comparing this list with a previously seen
        one is a cheap way to notice changes made elsewhere (e.g. on the phone)
        without downloading the whole day.
        """
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request("GET", f"/diet-plan/{uid}/days/{as_date(day).isoformat()}/revisions")
        return [str(rev) for rev in data] if isinstance(data, list) else []

    def sync_single_day(
        self,
        user_id: str,
        day: date | str,
        *,
        day_payload: dict[str, Any],
        synchronous: bool = True,
    ) -> Any:
        """Sync a single day snapshot."""
        day = as_date(day)
        logger.debug("sync_single_day user=%s day=%s", user_id, day)
        return self.sync_days(user_id, {day.isoformat(): day_payload}, synchronous=synchronous)

    def move_day_item(
        self,
        user_id: str,
        from_day: date | str,
        *,
        from_meal_type: str,
        item_id: str | int,
        to_day: date | str | None = None,
        to_meal_type: str | None = None,
    ) -> dict[str, Any]:
        """Move a row to another meal and/or day: delete it and add a copy, in one sync.

        ``item_id`` is the row's ``planDayDietItemId`` (found in any meal); a ``productId``
        works only when exactly one row of ``from_meal_type`` has it. ``to_meal_type``
        defaults to the row's meal and must exist on the target day; that is checked
        before anything is sent. ``ok`` is True when a re-read shows the copy (``row_id``)
        on the target day and the original gone.
        """
        from_day = as_date(from_day, "from_day")
        target_day = from_day if to_day is None else as_date(to_day, "to_day")

        source_day = self.get_day(user_id, from_day)
        source_meal_key, source_item = self._find_row(source_day, item_id, meal_type=from_meal_type)
        target = source_day if target_day == from_day else self.get_day(user_id, target_day)
        to_meal_key = self._meal(target, to_meal_type or source_meal_key)[0]

        moved_item = {key: value for key, value in source_item.items() if key != "deletedAt"}
        moved_item["planDayDietItemId"] = _new_row_id()
        delete_marker = self._mark_deleted(source_item)

        days_payload: dict[str, Any] = {from_day.isoformat(): {"dietPlan": {source_meal_key: {"items": [delete_marker]}}}}
        target_plan = days_payload.setdefault(target_day.isoformat(), {"dietPlan": {}})["dietPlan"]
        target_plan.setdefault(to_meal_key, {"items": []})["items"].append(moved_item)

        sync_response = self.sync_days(user_id, days_payload)
        source_after = self.get_day(user_id, from_day)
        target_after = source_after if target_day == from_day else self.get_day(user_id, target_day)
        removed_id = str(source_item.get("planDayDietItemId"))
        return {
            "ok": not self._has_row(source_after, removed_id) and self._has_row(target_after, moved_item["planDayDietItemId"]),
            "row_id": moved_item["planDayDietItemId"],
            "operation": "move_day_item",
            "from_date": from_day.isoformat(),
            "to_date": target_day.isoformat(),
            "from_meal": source_meal_key,
            "to_meal": to_meal_key,
            "removed_id": removed_id,
            "added_item": moved_item,
            "delete_marker": delete_marker,
            "sync_payload": days_payload,
            "sync_response": sync_response,
        }

    # What a copy sends of a catalog row: the keys of the recorded adds (sync_add_product, sync_add_recipe).
    _COPIED_KEYS = {
        "PRODUCT": ("foodType", "productId", "measureId", "measureQuantity", "ingredientsServing"),
        "RECIPE": ("foodType", "recipeId", "measureId", "measureQuantity", "ingredientsServing"),
    }

    def copy_meal(
        self,
        user_id: str,
        from_day: date | str,
        *,
        from_meal_type: str,
        to_day: date | str,
        to_meal_type: str | None = None,
        item_ids: list[str] | None = None,
        eaten: bool | None = None,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Copy a meal's rows (or those in ``item_ids``) to another meal and/or day, in one sync.

        ``to_meal_type`` defaults to the source meal, and must exist on ``to_day``; the target
        must differ from the source in day or meal. Each copy is rebuilt in the write shape of
        an add (a custom row from its name and nutrition), with the source row's ``eaten``
        unless ``eaten`` is given; a row of another type (e.g. ``RECIPE_AI``) is not copied
        but listed in ``skipped``. ``row_id`` (a UUID v1, else a new one) seeds the copies'
        ids: the n-th copy's id differs from it only in the clock sequence (the first copy
        gets ``row_id`` itself), so repeating the call with the same ``row_id`` writes the
        same rows again instead of new ones, and an id that another row of the target day
        already has raises FitatuValidationError, as for the adds. Nothing is sent before
        all of that is checked. ``ok`` is True when a re-read shows every copy (``row_ids``).
        """
        from_day, to_day = as_date(from_day, "from_day"), as_date(to_day, "to_day")
        if eaten is not None:
            validate_bool(eaten, "eaten")
        if item_ids is not None and not item_ids:  # None is the whole meal
            raise FitatuValidationError("item_ids must name at least one row, or be None for the whole meal")
        seed = uuid.UUID(_row_id(row_id))

        source_day = self.get_day(user_id, from_day)
        from_meal_key, bucket = self._meal(source_day, from_meal_type)
        rows = as_dict_list(bucket.get("items"))
        if item_ids is not None:
            wanted = [str(item_id) for item_id in item_ids]
            missing = sorted(set(wanted) - {str(row.get("planDayDietItemId")) for row in rows})
            if missing:
                raise FitatuNotFoundError(
                    f"not in {from_meal_key} on {from_day}: {', '.join(missing)}; its rows: "
                    f"{self._describe_rows([(from_meal_key, row) for row in rows])}"
                )
            rows = [row for row in rows if str(row.get("planDayDietItemId")) in wanted]
        if not rows:
            raise FitatuNotFoundError(f"{from_meal_key} on {from_day} has no rows to copy")
        target = source_day if to_day == from_day else self.get_day(user_id, to_day)
        to_meal_key = self._meal(target, to_meal_type or from_meal_key)[0]
        if (to_day, to_meal_key) == (from_day, from_meal_key):
            raise FitatuValidationError(f"copy {from_meal_key} to another meal or day, not onto itself")

        copies: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            copy_id = _derived_row_id(seed, index)
            food_type = str(row.get("foodType"))
            if food_type == "CUSTOM_ITEM":
                copy = self._custom_item_from_values(
                    name=row.get("name") or "",
                    calories=row.get("energy") or 0,
                    protein_g=row.get("protein") or 0,
                    fat_g=row.get("fat") or 0,
                    carbs_g=row.get("carbohydrate") or 0,
                    row_id=copy_id,
                )
            elif food_type in self._COPIED_KEYS:
                copy = {"planDayDietItemId": copy_id, **{key: row.get(key) for key in self._COPIED_KEYS[food_type]}, "source": "API"}
            else:  # no recorded write shape to rebuild it in
                skipped.append(row)
                continue
            copy["eaten"] = bool(row.get("eaten")) if eaten is None else eaten
            self._refuse_taken_row_id(target, to_meal_key, copy)
            copies.append(copy)

        row_ids = [copy["planDayDietItemId"] for copy in copies]
        sync_response = self._upsert_rows(user_id, to_day, {to_meal_key: copies}) if copies else None
        after = self.get_day(user_id, to_day) if copies else target
        return {
            "ok": bool(copies) and all(self._has_row(after, copied) for copied in row_ids),
            "row_ids": row_ids,
            "operation": "copy_meal",
            "from_date": from_day.isoformat(),
            "to_date": to_day.isoformat(),
            "from_meal": from_meal_key,
            "to_meal": to_meal_key,
            "added_items": copies,
            "skipped": skipped,
            "sync_response": sync_response,
        }

    def replace_day_item_with_custom_item(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        item_id: str | int,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        source: str = "API",
    ) -> dict[str, Any]:
        """Replace a row with a new custom row (delete + add) in one sync.

        ``item_id`` is the row's ``planDayDietItemId`` (found in any meal); a ``productId``
        works only when exactly one row of ``meal_type`` has it. The new row keeps the old
        one's ``eaten``. ``ok`` is True when a re-read shows the new row (``row_id``) and the
        old one gone.
        """
        day = as_date(day)
        replacement = self._custom_item_from_values(
            name=name, calories=calories, protein_g=protein_g, fat_g=fat_g, carbs_g=carbs_g, source=source
        )
        source_meal_key, source_item = self._find_row(self.get_day(user_id, day), item_id, meal_type=meal_type)
        replacement["eaten"] = bool(source_item.get("eaten", False))  # a row the user ate stays eaten

        delete_marker = self._mark_deleted(source_item)
        days_payload = {day.isoformat(): {"dietPlan": {source_meal_key: {"items": [delete_marker, replacement]}}}}
        sync_response = self.sync_days(user_id, days_payload)
        after = self.get_day(user_id, day)
        removed_id = str(source_item.get("planDayDietItemId"))
        return {
            "ok": not self._has_row(after, removed_id) and self._has_row(after, replacement["planDayDietItemId"]),
            "row_id": replacement["planDayDietItemId"],
            "operation": "replace_day_item_with_custom_item",
            "date": day.isoformat(),
            "meal": source_meal_key,
            "removed_id": removed_id,
            "replacement_item": replacement,
            "delete_marker": delete_marker,
            "sync_payload": days_payload,
            "sync_response": sync_response,
        }

    def classify_day_item_for_removal(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        item_id: str | int,
    ) -> dict[str, Any]:
        """Report which kind of row ``item_id`` is (``normal_item``, ``custom_add_item``,
        ``custom_recipe_item``). Every kind is deleted the same way; the kind is only a
        filter for :meth:`remove_day_items_by_kind`. A row that is not on the day raises
        FitatuNotFoundError listing the day's rows.
        """
        day = as_date(day)
        actual_meal, row = self._find_row(self.get_day(user_id, day), item_id, meal_type=meal_type)
        resolved_kind, reason = self._resolve_item_kind(row)
        return {
            "found": True,
            "requested_item_id": str(item_id),
            "resolved_kind": resolved_kind,
            "reason": reason,
            "meal": actual_meal,
            "item": row,
        }

    @staticmethod
    def _resolve_item_kind(item: dict[str, Any]) -> tuple[str, str]:
        """Classify a day-item dict as normal_item, custom_add_item, or custom_recipe_item."""
        food_type = str(item.get("foodType") or "").strip().upper()
        source = str(item.get("source") or "").strip().upper()
        product_id = item.get("productId")
        try:
            quantity = float(item.get("measureQuantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0

        if food_type == "PRODUCT" or product_id is not None:
            return "normal_item", "product_or_has_product_id"
        if food_type == "CUSTOM_ITEM":
            # Live data shows that API quick-add rows often materialize as CUSTOM_ITEM
            # with serving-like quantities, so we keep treating those as the
            # recipe-like bucket for deletion strategy purposes.
            if source == "API" and quantity <= 2.0:
                return "custom_recipe_item", "custom_item_api_serving_like"
            return "custom_add_item", "custom_item_manual_like"
        return "normal_item", "default"

    def list_day_items_for_removal(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the day's rows (optionally one meal) with their removal kind and meal name."""
        planner_day = self.get_day(user_id, as_date(day))
        diet_plan = self._diet_plan(planner_day)
        selected_meal = self._meal(planner_day, meal_type)[0] if meal_type is not None else None

        rows: list[dict[str, Any]] = []
        for current_meal_key, item in self._iter_rows(planner_day):
            if selected_meal is not None and current_meal_key != selected_meal:
                continue
            resolved_kind, reason = self._resolve_item_kind(item)
            rows.append(
                {
                    "meal": current_meal_key,
                    "meal_name": diet_plan[current_meal_key].get("mealName"),
                    "item_id": str(item.get("planDayDietItemId") or ""),
                    "product_id": item.get("productId"),
                    "food_type": item.get("foodType"),
                    "source": item.get("source"),
                    "resolved_kind": resolved_kind,
                    "reason": reason,
                    "item": item,
                }
            )
        return rows

    def remove_day_items_by_kind(
        self,
        user_id: str,
        day: date | str,
        *,
        item_kind: str,
        meal_type: str | None = None,
        all_meals: bool = False,
        row_ids: list[str] | None = None,
        max_items: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Delete every row of one kind (or ``auto`` for all) in one meal or the whole day.

        The scope is explicit: pass ``meal_type``, or ``all_meals=True`` for every meal.
        ``row_ids`` narrows the targets to those ``planDayDietItemId`` values (e.g. the rows
        a caller added itself), ``max_items`` caps them. A requested id that is not a
        target (not on the day, of another kind or meal, or past ``max_items``) is listed
        in ``missing_row_ids`` and makes ``ok`` False. The result lists the ``targets``
        (rows as :meth:`list_day_items_for_removal` gives them). With ``dry_run=True``
        nothing is sent; otherwise all targets go out in one sync and the day is re-read
        to confirm each is gone (``attempts``).
        """
        day = as_date(day)
        requested_kind = (item_kind or "").strip().lower()
        valid_kinds = {"normal_item", "custom_add_item", "custom_recipe_item", "auto"}
        if requested_kind not in valid_kinds:
            raise FitatuValidationError(
                f"item_kind must be one of: auto, normal_item, custom_add_item, custom_recipe_item; got {item_kind!r}"
            )
        if meal_type is None and not all_meals:
            raise FitatuValidationError("pass meal_type for one meal, or all_meals=True for the whole day")
        if meal_type is not None and all_meals:
            raise FitatuValidationError("pass meal_type or all_meals=True, not both")
        if max_items is not None:
            validate_non_negative_int(max_items, "max_items")
        logger.debug("remove_by_kind user=%s day=%s kind=%s meal=%s", user_id, day, requested_kind, meal_type)
        wanted_ids = None if row_ids is None else {str(row_id) for row_id in row_ids}
        targets = [
            row
            for row in self.list_day_items_for_removal(user_id, day, meal_type=meal_type)
            if row["item_id"]
            and requested_kind in {"auto", row["resolved_kind"]}
            and (wanted_ids is None or row["item_id"] in wanted_ids)
        ][:max_items]
        targeted_ids = {row["item_id"] for row in targets}
        missing = [str(row_id) for row_id in row_ids or [] if str(row_id) not in targeted_ids]
        meal_key = self.normalize_meal_key(meal_type) if meal_type is not None else None
        if dry_run:
            return {
                "ok": not missing,
                "dry_run": True,
                "requested_kind": requested_kind,
                "targeted_count": len(targets),
                "missing_row_ids": missing,
                "meal": meal_key,
                "targets": targets,
            }

        sync_response: Any = None
        still_visible: set[str] = set()
        if targets:
            sync_response, still_visible = self._delete_rows(
                user_id, day, [(row["meal"], row["item"]) for row in targets]
            )
        attempts = [
            {
                "seq": index,
                "meal": row["meal"],
                "item_id": row["item_id"],
                "requested_kind": requested_kind,
                "resolved_kind": row["resolved_kind"],
                "ok": row["item_id"] not in still_visible,
                "cleanup_mode": "none" if row["item_id"] in still_visible else "removed",
            }
            for index, row in enumerate(targets, start=1)
        ]
        removed_count = sum(1 for attempt in attempts if attempt["ok"])
        return {
            "ok": removed_count == len(targets) and not missing,
            "requested_kind": requested_kind,
            "targeted_count": len(targets),
            "removed_count": removed_count,
            "failed_count": len(targets) - removed_count,
            "missing_row_ids": missing,
            "meal": meal_key,
            "targets": targets,
            "attempts": attempts,
            "sync_response": sync_response,
        }

    def remove_day_item(
        self,
        user_id: str,
        day: date | str,
        *,
        meal_type: str,
        item_id: str | int,
    ) -> dict[str, Any]:
        """Delete a planner row the way the app does (``deletedAt``) and confirm it is gone.

        Pass the row's ``planDayDietItemId`` as ``item_id`` (it is found in any meal); a
        ``productId`` is accepted only when exactly one row of ``meal_type`` has it. Check
        ``ok``: ``False`` means the row was not found (``reason`` is ``item_not_found`` and
        ``message`` lists the day's rows) or is still there after the sync.
        """
        day = as_date(day)
        logger.debug("remove_day_item user=%s day=%s meal=%s item=%s", user_id, day, meal_type, item_id)
        day_data = self.get_day(user_id, day)
        found = self._locate_row(day_data, item_id, meal_type=meal_type)
        return self._remove_row(user_id, day, day_data, found, meal_key=self.normalize_meal_key(meal_type), item_id=item_id)

    # ------------------------------------------------------------------
    # Activities (workouts) travel in the same day sync, under "activities", in the
    # write shape below (recorded live). The day GET lists them under "activityPlan" in
    # a longer read shape, which the sync rejects field by field when sent back.
    # ------------------------------------------------------------------

    @staticmethod
    def _find_activity(day_data: dict[str, Any], row_id: str) -> dict[str, Any] | None:
        return next((a for a in as_dict_list(day_data.get("activityPlan")) if str(a.get("uuid")) == str(row_id)), None)

    @staticmethod
    def _describe_activities(activities: list[dict[str, Any]]) -> str:
        return "; ".join(f"{a.get('uuid')} ({a.get('activityName')}, {a.get('energy')} kcal)" for a in activities) or "none"

    def add_activity(
        self,
        user_id: str,
        day: date | str,
        *,
        activity_id: int | str,
        minutes: int,
        energy: int,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        """Log ``minutes`` of a catalog activity that burned ``energy`` kcal.

        ``activity_id`` comes from ``client.activities.search(phrase)``. The server stores
        ``energy`` exactly as sent and does not compute it; ``activities.search(phrase,
        bmr=..., sex=...)`` returns ``energy`` values computed for the user to derive it
        from. ``row_id`` (a UUID v1) becomes the activity's ``uuid`` and makes a retry
        safe, as for the add_* rows; one that is already on the day with another activity,
        energy or duration raises FitatuValidationError instead of overwriting it. The day
        is re-read: ``ok`` says the activity is there, ``row_id`` is what
        :meth:`remove_activity` takes.
        """
        day = as_date(day)
        validate_positive_int(minutes, "minutes")
        validate_positive_int(energy, "energy")
        activity: dict[str, Any] = {
            "uuid": _row_id(row_id),
            "planDayActivityType": 1,
            "activityId": int(validate_numeric_id(activity_id, "activity_id")),
            "energy": energy,
            "simpleMeasure": {"measureId": 1, "value": minutes},  # measure 1 = minutes
            "advancedMeasures": [],
            "deletedAt": None,
        }
        existing = None if row_id is None else self._find_activity(self.get_day(user_id, day), activity["uuid"])
        if existing is not None and (
            str(existing.get("activityId")),
            existing.get("energy"),
            [measure.get("value") for measure in as_dict_list(existing.get("simpleMeasures"))],
        ) != (str(activity["activityId"]), energy, [minutes]):
            raise FitatuValidationError(
                f"row_id {activity['uuid']} already belongs to another activity on this day: "
                f"{self._describe_activities([existing])}. If this add repeats an earlier one, that attempt "
                "was saved and the activity has changed since: do not add it again. An activity cannot be "
                "updated: remove it and add it again"
            )
        sync_response = self.sync_single_day(user_id, day, day_payload={"activities": [activity]})
        return {
            "ok": self._find_activity(self.get_day(user_id, day), activity["uuid"]) is not None,
            "row_id": activity["uuid"],
            "added_activity": activity,
            "sync_response": sync_response,
        }

    def remove_activity(self, user_id: str, day: date | str, *, item_id: str) -> dict[str, Any]:
        """Delete a logged activity by its ``uuid`` (``item_id``, the ``row_id`` of :meth:`add_activity`).

        Like the app, the activity is re-sent with ``deletedAt``, converted to the write
        shape, then the day is re-read: ``ok`` says it is gone. An activity that is not on
        the day is not an error, as for rows: ``ok`` is False, ``reason`` is
        ``item_not_found`` and ``message`` lists the day's activities.
        """
        day = as_date(day)
        day_data = self.get_day(user_id, day)
        activity = self._find_activity(day_data, item_id)
        if activity is None:
            listing = self._describe_activities(as_dict_list(day_data.get("activityPlan")))
            return {
                "ok": False,
                "row_id": item_id,
                "reason": "item_not_found",
                "message": f"activity not found: {item_id!r}; activities on this day: {listing}",
                "removed": None,
                "sync_response": None,
            }
        # shortcut: only the first simple measure is carried over; an activity logged with advanced
        # measures only would be rejected (FitatuPlannerSyncError). Map those if one turns up.
        measure = next(iter(as_dict_list(activity.get("simpleMeasures"))), {})
        deleted: dict[str, Any] = {
            "uuid": activity.get("uuid"),
            "planDayActivityType": activity.get("planDayActivityType"),
            "activityId": activity.get("activityId"),
            "energy": activity.get("energy"),
            "simpleMeasure": {"measureId": measure.get("measureId"), "value": measure.get("value")},
            "advancedMeasures": [],
            "deletedAt": self._now_timestamp(),
        }
        sync_response = self.sync_single_day(user_id, day, day_payload={"activities": [deleted]})
        return {
            "ok": self._find_activity(self.get_day(user_id, day), str(activity.get("uuid"))) is None,
            "row_id": activity.get("uuid"),
            "removed": activity,
            "sync_response": sync_response,
        }
