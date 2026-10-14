"""Evaluate how well a Claude model uses the agent tools: ~30 tasks in Polish and English.

Every task runs on a fresh demo diary (``FitatuTools.demo()``: made up, in memory, nothing sent
to Fitatu) with ``SYSTEM_PROMPT`` and every tool, the way ``fitatu-mcp`` serves them. A task
passes when all its checks pass: the tools it should call (with which arguments, in which
order), the tools it must not call, what the diary holds afterwards and, for questions, the
numbers in the answer. Every destructive call is approved, as a user who said yes would.

    pip install anthropic
    ANTHROPIC_API_KEY=... python scripts/eval_agent.py [--model claude-opus-5-5] [--effort medium] [--only ID,...] [--json report.json]

It costs real API usage (one conversation per task) and is not run in CI. The exit status is 0
only when every task passed. Refusals count as failures: no fallback model is configured, so
the score is the named model's own.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fitatu_api import FitatuTools
from fitatu_api.agent import SYSTEM_PROMPT, TOOLS, anthropic_tools

WRITES = {tool["name"] for tool in TOOLS if not tool["annotations"]["readOnlyHint"]}
PROPERTIES = {tool["name"]: tool["input_schema"]["properties"] for tool in TOOLS}
MAX_TURNS = 20


@dataclass
class Run:
    """One task's conversation: the tool calls with their results, the answer and the diary after it."""

    tools: FitatuTools
    today: str
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    answer: str = ""
    stop: str = ""

    def day(self, offset: int = 0) -> dict[str, Any]:
        date = (dt.date.fromisoformat(self.today) + dt.timedelta(days=offset)).isoformat()
        return self.tools.get_day(date)

    def rows(self, meal: str, offset: int = 0) -> list[dict[str, Any]]:
        return next(m["items"] for m in self.day(offset)["meals"] if m["meal_key"] == meal)


Check = tuple[str, Callable[[Run], bool]]


# -- checks -------------------------------------------------------------------------------
def called(name: str, **expected: Any) -> Check:
    """``name`` was called (and ran) with these arguments; a callable value tests the argument."""

    def test(run: Run) -> bool:
        return any(
            call == name and result["ok"] and all(value(args.get(key)) if callable(value) else args.get(key) == value for key, value in expected.items())
            for call, args, result in run.calls
        )

    shown = ", ".join(f"{key}={'...' if callable(value) else value!r}" for key, value in expected.items())
    return f"calls {name}({shown})", test


def called_any(*names: str) -> Check:
    return f"calls one of {', '.join(names)}", lambda run: any(call in names and result["ok"] for call, _, result in run.calls)


def not_called(*names: str) -> Check:
    return f"never calls {', '.join(names)}", lambda run: not any(call in names for call, _, _ in run.calls)


def no_writes() -> Check:
    return "changes nothing", lambda run: not any(call in WRITES for call, _, _ in run.calls)


def before(first: str, then: str) -> Check:
    def test(run: Run) -> bool:
        names = [call for call, _, result in run.calls if result["ok"]]
        return first in names and then in names and names.index(first) < names.index(then)

    return f"calls {first} before {then}", test


def today(offset: int = 0) -> Callable[[Run], Callable[[Any], bool]]:
    """An argument test for the day ``offset`` days from the demo's today."""
    return lambda run: lambda value: value == (dt.date.fromisoformat(run.today) + dt.timedelta(days=offset)).isoformat()


def on_day(name: str, offset: int = 0, **expected: Any) -> Check:
    """Like :func:`called`, with ``date`` (or ``from_date``) the day ``offset`` days from today."""
    key = "date" if "date" in PROPERTIES[name] else "from_date"

    def test(run: Run) -> bool:
        return called(name, **{key: today(offset)(run), **expected})[1](run)

    return f"calls {name}({key}=today{offset:+d}{''.join(f', {k}={v!r}' for k, v in expected.items())})", test


def has_row(meal: str, food: str, offset: int = 0, *, eaten: bool | None = None, weight: float | None = None) -> Check:
    def test(run: Run) -> bool:
        return any(
            food.lower() in row["name"].lower()
            and (eaten is None or row["eaten"] is eaten)
            and (weight is None or abs(row["weight"] - weight) <= 0.05 * weight)
            for row in run.rows(meal, offset)
        )

    return f"day {offset:+d} {meal} has {food}" + ("" if eaten is None else f" eaten={eaten}"), test


def no_row(meal: str, food: str, offset: int = 0) -> Check:
    return f"day {offset:+d} {meal} has no {food}", lambda run: not any(food.lower() in row["name"].lower() for row in run.rows(meal, offset))


def says(*words: str) -> Check:
    return f"the answer names {' or '.join(words)}", lambda run: any(word.lower() in run.answer.lower() for word in words)


def says_number(what: str, value: Callable[[Run], float], tolerance: float = 1.0) -> Check:
    def test(run: Run) -> bool:
        text = re.sub(r"(?<=\d)[ \u00a0,](?=\d{3}\b)", "", run.answer)  # 1 187,5 and 1,187.5 are 1187.5
        numbers = [float(number.replace(",", ".")) for number in re.findall(r"\d+(?:[.,]\d+)?", text)]
        return any(abs(number - abs(value(run))) <= tolerance for number in numbers)

    return f"the answer gives {what}", test


def state(what: str, test: Callable[[Run], bool]) -> Check:
    return what, test


def _progress(run: Run, nutrient: str, key: str) -> float:
    return float(run.tools.get_progress(run.today, run.today)["days"][0][nutrient][key])


def _monday(run: Run) -> str:
    day = dt.date.fromisoformat(run.today)
    return (day - dt.timedelta(days=day.weekday())).isoformat()


# -- tasks --------------------------------------------------------------------------------
TASKS: dict[str, tuple[str, list[Check]]] = {
    "eaten-today-pl": (
        "Ile kalorii dziś zjadłem?",
        [called_any("get_progress", "get_day"), no_writes(), says_number("the kcal ticked as eaten", lambda run: run.day()["eaten_totals"]["energy"], 1.5)],
    ),
    "left-today-en": (
        "How many calories do I have left today?",
        [on_day("get_progress"), no_writes(), says_number("what is left", lambda run: _progress(run, "energy", "left"), 1.5)],
    ),
    "protein-goal-pl": (
        "Czy dziś zmieściłem się w białku?",
        [on_day("get_progress"), no_writes(), says_number("the protein logged", lambda run: _progress(run, "protein", "logged"), 1.0)],
    ),
    "water-pl": ("Ile wody dziś wypiłem?", [on_day("get_water"), no_writes(), says("750")]),
    "barcode-en": ("What product has the barcode 5900000000024?", [called("find_by_barcode", barcode="5900000000024"), no_writes(), says("jogurt")]),
    "week-pl": (
        "Jak mi poszedł ten tydzień?",
        [
            state("asks for Monday to today", lambda run: any(c in ("get_report", "get_progress") and a.get("from_date") == _monday(run) and a.get("to_date") == run.today for c, a, _ in run.calls)),
            no_writes(),
        ],
    ),
    "weight-trend-en": (
        "How has my weight changed over the last month?",
        [called_any("get_weight_history", "get_report"), no_writes(), says("1.5", "1,5")],
    ),
    "top-protein-en": (
        "Which foods gave me the most protein over the last three days?",
        [
            state("ranks by protein", lambda run: any((c == "get_top_foods" and a.get("nutrient") == "protein") or c == "get_report" for c, a, _ in run.calls)),
            no_writes(),
            says("kurczak", "chicken"),
        ],
    ),
    "banana-supper-pl": (
        "Zjadłem banana na przekąskę.",
        [has_row("supper", "Banan", eaten=True), no_row("snack", "Banan"), not_called("remove_items", "update_item", "move_item")],
    ),
    "kolacja-slot-pl": (
        "Na kolację zjadłem jogurt naturalny i jabłko.",  # Kolacja is the snack slot in this diary
        [has_row("snack", "Jogurt naturalny", eaten=True), has_row("snack", "Jabłko", eaten=True), no_row("dinner", "Jabłko")],
    ),
    "eggs-en": ("Log 2 eggs for my second breakfast.", [has_row("second_breakfast", "Jajko", eaten=True, weight=120), called_any("add_food", "add_foods")]),
    "milk-ml-pl": ("Dodaj do śniadania 200 ml mleka.", [has_row("breakfast", "Mleko", weight=200, eaten=True)]),
    "custom-pizza-en": (
        "I had a slice of pizza for lunch, about 450 kcal.",
        [
            state("logs ~450 kcal at lunch", lambda run: any(abs(row["energy"] - 450) <= 50 for row in run.rows("lunch"))),
            state("estimates the macros", lambda run: any(c == "add_custom_item" and a.get("protein_g", 0) > 0 and a.get("fat_g", 0) > 0 for c, a, _ in run.calls) or not any(c == "add_custom_item" for c, _, _ in run.calls)),
        ],
    ),
    "water-glasses-en": ("I just drank two glasses of water.", [before("get_water", "set_water"), state("the day's water is 1250 ml", lambda run: run.tools.get_water(run.today)["water_ml"] == 1250)]),
    "weight-pl": ("Ważę dziś 80,1 kg.", [on_day("log_weight", weight_kg=80.1)]),
    "run-pl": (
        "Przebiegłem dziś 5 km w 30 minut.",
        [
            called("add_activity", activity_id=4000001, minutes=30, energy_kcal=lambda kcal: kcal is not None and 250 <= kcal <= 450),
            state("today lists the run", lambda run: any(a["name"] == "Bieganie" for a in run.day()["activities"])),
        ],
    ),
    "remove-run-en": ("Delete yesterday's run.", [called("remove_activity"), state("yesterday has no activity", lambda run: not run.day(-1)["activities"])]),
    "remove-butter-en": (
        "Remove the butter from today's breakfast.",
        [before("preview_removal", "remove_items"), no_row("breakfast", "Masło"), has_row("breakfast", "Jajko"), has_row("breakfast", "Chleb")],
    ),
    "clear-breakfast-pl": (
        "Usuń wszystko z dzisiejszego śniadania.",
        [before("preview_removal", "remove_items"), state("breakfast is empty", lambda run: not run.rows("breakfast"))],
    ),
    "move-rice-pl": ("Przenieś ryż z obiadu na kolację.", [called("move_item", to_meal="snack"), has_row("snack", "Ryż"), no_row("dinner", "Ryż")]),
    "tick-dinner-pl": (
        "Zjadłem obiad, odhacz go jako zjedzony.",  # the dinner slot, called Obiad here
        [state("every dinner row is eaten", lambda run: all(row["eaten"] for row in run.rows("dinner")) and bool(run.rows("dinner"))), not_called("add_food", "add_foods", "remove_items")],
    ),
    "plan-tomorrow-pl": (
        "Zaplanuj mi na jutro na obiad to samo, co mam dziś na obiad.",
        [called("copy_meal", from_meal="dinner"), has_row("dinner", "Pierś z kurczaka", 1, eaten=False), has_row("dinner", "Ryż", 1, eaten=False)],
    ),
    "same-breakfast-en": (
        "Same breakfast as yesterday, please.",
        [has_row("breakfast", "Owsianka", eaten=True), has_row("breakfast", "Kawa", eaten=True), not_called("remove_items")],
    ),
    "create-recipe-pl": (
        "Zapisz mi przepis na jajecznicę: trzy jajka i łyżeczka masła, jedna porcja.",
        [called("create_recipe"), state("my foods list it", lambda run: any("jajecznic" in hit["name"].lower() for hit in run.tools.get_my_foods("", 50)))],
    ),
    "delete-own-recipe-en": (
        "Delete my banana smoothie recipe.",
        [called("delete_recipe", recipe_id=3000003), state("it is gone from my foods", lambda run: not any("koktajl" in hit["name"].lower() for hit in run.tools.get_my_foods("", 50)))],
    ),
    "note-pl": (
        "Zapisz w dzisiejszej notatce, że bolała mnie głowa.",
        [on_day("set_note"), state("today's note mentions the headache", lambda run: "głow" in (run.day()["note"] or "").lower())],
    ),
    "dinner-idea-pl": (
        "Znajdź mi przepis na obiad do 500 kcal.",
        [called("search_recipes", max_energy_kcal=lambda kcal: kcal is not None and kcal <= 500), no_writes()],
    ),
    "shopping-week-pl": (
        "Zrób mi listę zakupów na ten tydzień.",  # past days' unticked rows were eaten, not to be bought
        [state("starts at today or later", lambda run: any(c == "get_shopping_list" and r["ok"] and a["from_date"] >= run.today for c, a, r in run.calls)), no_writes()],
    ),
    "catalog-recipe-en": (
        "Delete the spaghetti bolognese recipe.",  # a catalog recipe: not the user's to delete
        [
            state("looks the recipe up", lambda run: any(c in ("search_food", "get_my_foods", "delete_recipe") for c, _, _ in run.calls)),
            state("the catalog recipe stays", lambda run: run.tools.client.get_recipe(3000002)["deleted"] is False),
            not_called("remove_items"),
        ],
    ),
}


# -- the loop -----------------------------------------------------------------------------
def run_task(client: Any, model: str, effort: str, prompt: str) -> tuple[Run, dict[str, int]]:
    tools = FitatuTools.demo()
    run = Run(tools, tools.get_today()["date"])
    usage = {"input_tokens": 0, "output_tokens": 0}
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    definitions = anthropic_tools()
    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=definitions,
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},  # the tools and the system prompt are the same in every request
        )
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})
        run.stop = response.stop_reason
        if response.stop_reason != "tool_use":  # end_turn, or max_tokens / refusal: scored as it is
            run.answer = "".join(block.text for block in response.content if block.type == "text")
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                result = tools.call(block.name, block.input)
                run.calls.append((block.name, dict(block.input), result))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, ensure_ascii=False), "is_error": not result["ok"]})
        messages.append({"role": "user", "content": results})
    return run, usage


def score(checks: list[Check], run: Run) -> list[str]:
    """The descriptions of the checks that failed."""
    failed = []
    for description, test in checks:
        try:
            ok = test(run)
        except Exception as exc:  # a check that cannot even run fails, e.g. a meal the model renamed away
            ok, description = False, f"{description} ({type(exc).__name__}: {exc})"
        if not ok:
            failed.append(description)
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="claude-opus-5-5", help="model id (default: claude-opus-5-5)")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"], help="output_config.effort (default: medium)")
    parser.add_argument("--only", type=lambda text: text.split(","), metavar="ID,...", help=f"run only these tasks: {', '.join(TASKS)}")
    parser.add_argument("--json", metavar="PATH", help="also write the results, with every tool call, to this file")
    args = parser.parse_args(argv)
    unknown = sorted(set(args.only or []) - set(TASKS))
    if unknown:
        parser.error(f"unknown task(s): {', '.join(unknown)}")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(
            "Set ANTHROPIC_API_KEY to run the evaluation; it calls the Claude API once per task (real usage):\n"
            "    pip install anthropic\n"
            "    ANTHROPIC_API_KEY=sk-ant-... python scripts/eval_agent.py [--model claude-opus-5-5] [--only ID,...]\n"
            f"Tasks: {', '.join(TASKS)}",
            file=sys.stderr,
        )
        return 2
    try:
        import anthropic
    except ImportError:
        print("The evaluation needs the Anthropic SDK: pip install anthropic", file=sys.stderr)
        return 2

    client = anthropic.Anthropic()
    report = []
    for task_id in args.only or TASKS:
        prompt, checks = TASKS[task_id]
        started = time.monotonic()
        try:
            run, usage = run_task(client, args.model, args.effort, prompt)
            failed = score(checks, run)
        except anthropic.APIError as exc:  # the task could not be run, which is not the model's score
            print(f"ERROR {task_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            report.append({"task": task_id, "error": str(exc)})
            continue
        print(f"{'PASS' if not failed else 'FAIL'} {task_id} ({len(run.calls)} calls, {time.monotonic() - started:.0f} s)")
        for description in failed:
            print(f"    - {description}")
        report.append(
            {
                "task": task_id,
                "prompt": prompt,
                "passed": not failed,
                "failed": failed,
                "stop_reason": run.stop,
                "calls": [{"tool": name, "arguments": arguments, "ok": result["ok"]} for name, arguments, result in run.calls],
                "answer": run.answer,
                "usage": usage,
            }
        )
    scored = [entry for entry in report if "passed" in entry]
    passed = sum(entry["passed"] for entry in scored)
    tokens = {key: sum(entry["usage"][key] for entry in scored) for key in ("input_tokens", "output_tokens")}
    print(f"\n{passed}/{len(scored)} tasks passed with {args.model} (effort {args.effort}); {len(report) - len(scored)} could not run; tokens: {tokens}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as file:
            json.dump({"model": args.model, "effort": args.effort, "passed": passed, "tasks": report}, file, ensure_ascii=False, indent=2)
    return 0 if scored and passed == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
