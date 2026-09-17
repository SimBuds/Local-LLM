"""
Tasks for run-tools.py: does the model call the right tool, with the right
arguments, and does it stay quiet when no tool applies?

This is the suite behind the editor-agent pick (Cline). Every other runner
measures prose or JSON the model *writes*; an agent instead fails by calling the
wrong function, inventing an argument it was never given, or calling a tool for
a question it could answer itself. Those are the failures encoded here.

Each task carries:
  - tools:  the OpenAI-shape tool list offered for that turn, distractors
            included. A model that picks a plausible neighbour is wrong in the
            way that breaks a real agent loop.
  - user:   the user turn.
  - expect: the calls that count as correct, as {"name": ..., "args": {...}}.
            An empty list means "no tool call at all" — the model must answer,
            or ask, in prose.

Argument values are matched per key (see score_calls in run-tools.py):
a plain scalar compares normalized (case, whitespace, numeric value), while
{"any_of": [...]} accepts alternatives and {"contains": "..."} requires a
substring, for free-text arguments like a search query where exact wording is
not the thing under test.

Add tasks here; run-tools.py discovers everything in TASKS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- tool definitions ---------------------------------------------------------
# Shared so several tasks can offer the same tool as a distractor.

GET_WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
                "unit": {"type": "string", "enum": ["c", "f"],
                         "description": "Temperature unit"},
            },
            "required": ["city"],
        },
    },
}

WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the public web and return result snippets.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}

SEND_EMAIL = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": "Send an email to a recipient address.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
}

CREATE_EVENT = {
    "type": "function",
    "function": {
        "name": "create_event",
        "description": "Create a calendar event.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "time": {"type": "string", "description": "HH:MM, 24-hour"},
                    },
                    "required": ["date", "time"],
                },
                "duration_minutes": {"type": "integer"},
            },
            "required": ["title", "start"],
        },
    },
}

CONVERT_UNITS = {
    "type": "function",
    "function": {
        "name": "convert_units",
        "description": "Convert a value between units.",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
}


FIND_USER = {
    "type": "function",
    "function": {
        "name": "find_user",
        "description": "Look up a colleague's contact details by name.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
}


@dataclass
class ToolTask:
    """One turn, or two when `results` is set.

    A task with `results` is a round trip: the runner replays the model's own
    call, appends the canned tool output keyed by tool name, and asks again.
    `expect_after` scores the calls of that second round (empty means the model
    should now answer instead of calling again), and `answer_checks` scores the
    final prose. That is where grounding is tested: a model that ignores the
    tool output it was just handed, or invents detail the output does not
    contain, fails here while passing every single-turn task.

    answer_checks entries are (label, kind, value) with kind one of:
      has      - normalized substring must appear
      lacks    - normalized substring must not appear
      any      - at least one of a list of substrings must appear
    """

    key: str
    user: str
    tools: list[dict]
    expect: list[dict] = field(default_factory=list)
    why: str = ""          # what failure this task is here to catch
    results: dict[str, Any] = field(default_factory=dict)
    expect_after: list[dict] = field(default_factory=list)
    answer_checks: list[tuple] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


TASKS: dict[str, ToolTask] = {
    t.key: t for t in [
        ToolTask(
            key="pick_tool",
            user="What's the weather in Toronto right now?",
            tools=[GET_WEATHER, WEB_SEARCH, SEND_EMAIL],
            expect=[{"name": "get_weather", "args": {"city": "Toronto"}}],
            why="picks the right tool when plausible neighbours are offered",
        ),
        ToolTask(
            key="enum_arg",
            user="What's the temperature in Berlin? Give it in Fahrenheit.",
            tools=[GET_WEATHER, CONVERT_UNITS],
            expect=[{"name": "get_weather", "args": {"city": "Berlin", "unit": "f"}}],
            why="fills an enum argument from the wording instead of defaulting",
        ),
        ToolTask(
            key="parallel_calls",
            user="Compare the weather in Toronto and Oslo for me, in celsius.",
            tools=[GET_WEATHER, WEB_SEARCH],
            expect=[{"name": "get_weather", "args": {"city": "Toronto", "unit": "c"}},
                    {"name": "get_weather", "args": {"city": "Oslo", "unit": "c"}}],
            why="emits one call per entity rather than one merged call",
        ),
        ToolTask(
            key="nested_arg",
            user=("Put 'Dentist' on my calendar for 2026-10-02 at 14:30, "
                  "45 minutes long."),
            tools=[CREATE_EVENT, GET_WEATHER],
            expect=[{"name": "create_event",
                     "args": {"title": "Dentist",
                              "start": {"date": "2026-10-02", "time": "14:30"},
                              "duration_minutes": 45}}],
            why="builds a nested object argument in the required shape",
        ),
        ToolTask(
            key="search_query",
            user="Search the web for the latest llama.cpp release notes.",
            tools=[WEB_SEARCH, GET_WEATHER],
            expect=[{"name": "web_search",
                     "args": {"query": {"contains": "llama.cpp"}}}],
            why="passes the user's subject into a free-text query argument",
        ),
        ToolTask(
            key="no_tool_needed",
            user="What is 12 times 12? Just tell me the number.",
            tools=[GET_WEATHER, WEB_SEARCH, CONVERT_UNITS],
            expect=[],
            why="answers directly instead of reaching for a tool",
        ),
        ToolTask(
            key="no_tool_fits",
            user=("Rewrite this sentence to be shorter: 'The deployment process "
                  "is currently in a state of being reviewed by the team.'"),
            tools=[GET_WEATHER, SEND_EMAIL],
            expect=[],
            why="does not force an unrelated tool onto a text task",
        ),
        ToolTask(
            key="chain_lookup",
            user="Email Dana the Q3 summary: revenue was up 12 percent.",
            tools=[FIND_USER, SEND_EMAIL, WEB_SEARCH],
            expect=[{"name": "find_user", "args": {"name": "Dana"}}],
            results={"find_user": {"name": "Dana Whitfield",
                                   "email": "dana.whitfield@example.com",
                                   "team": "Finance"}},
            expect_after=[{"name": "send_email",
                           "args": {"to": "dana.whitfield@example.com",
                                    "body": {"contains": "12"}}}],
            why=("uses the looked-up address in the second call instead of "
                 "inventing one or stopping after the lookup"),
        ),
        ToolTask(
            key="grounded_answer",
            # The url check only earns its place because the turn asks for the
            # source: on 2026-09-17 all three models reported 7.3.1 correctly and
            # failed a citation nobody had requested.
            user=("Search for the current release version of the Vexil toolkit. "
                  "Tell me the version and the URL you got it from."),
            tools=[WEB_SEARCH],
            expect=[{"name": "web_search", "args": {"query": {"contains": "vexil"}}}],
            results={"web_search": {"results": [
                {"title": "Vexil 7.3.1 released",
                 "url": "https://vexil.example.org/releases/7.3.1",
                 "snippet": "Vexil 7.3.1 is the current stable release, published 2026-08-30."}]}},
            answer_checks=[("states the version from the result", "has", "7.3.1"),
                           ("cites the source url", "has", "vexil.example.org")],
            why="answers from the tool output and cites it rather than from memory",
        ),
        ToolTask(
            key="result_beats_prior",
            user=("What is the capital of Zubrowka? Search first and answer with "
                  "what the search says."),
            tools=[WEB_SEARCH],
            expect=[{"name": "web_search", "args": {"query": {"contains": "zubrowka"}}}],
            results={"web_search": {"results": [
                {"title": "Zubrowka moves its capital",
                 "url": "https://news.example.org/zubrowka-capital",
                 "snippet": "Since March 2026 the capital of Zubrowka is Nebelsbad, "
                            "replacing Lutz."}]}},
            answer_checks=[("follows the search result", "has", "Nebelsbad"),
                           ("does not keep the superseded answer", "lacks", "capital is Lutz")],
            why="prefers the tool result over whatever the model already believed",
        ),
        ToolTask(
            key="empty_results",
            user="Search for the Vexil toolkit's 2029 roadmap and tell me what it says.",
            tools=[WEB_SEARCH],
            expect=[{"name": "web_search", "args": {"query": {"contains": "vexil"}}}],
            results={"web_search": {"results": []}},
            # Phrase list widened 2026-09-17: qwen reported the empty result set
            # as "I don't have any information about ...", which the first list
            # missed. A check a correct answer cannot pass measures nothing.
            answer_checks=[("says nothing was found", "any",
                            ["no results", "nothing", "not find", "no information",
                             "couldn't find", "could not find", "didn't find",
                             "did not find", "unable to", "no roadmap",
                             "have any information", "does not exist",
                             "doesn't exist", "not exist"]),
                           ("does not invent a roadmap item", "lacks", "roadmap includes")],
            why="reports an empty result set instead of filling the gap",
        ),
        ToolTask(
            key="tool_error",
            user="What's the weather in Toronto?",
            tools=[GET_WEATHER],
            expect=[{"name": "get_weather", "args": {"city": "Toronto"}}],
            results={"get_weather": {"error": "upstream weather service unavailable (503)"}},
            answer_checks=[("reports the failure", "any",
                            ["unavailable", "error", "failed", "could not", "couldn't",
                             "unable", "not available", "try again"]),
                           ("does not state a temperature anyway", "lacks", "degrees")],
            why="says the tool failed instead of inventing the answer it wanted",
        ),
        ToolTask(
            key="missing_required_arg",
            user="Email Dana the quarterly report summary, please.",
            tools=[SEND_EMAIL, WEB_SEARCH],
            expect=[],
            why=("asks for the address instead of inventing one: no recipient "
                 "address exists in the conversation"),
        ),
    ]
}
