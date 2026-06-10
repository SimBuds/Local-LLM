"""
Coding benchmark battery for run-code.py.

Each task is a self-contained Python problem with hidden tests. The runner asks
a model to emit ONLY a fenced ```python block implementing `entrypoint`, then
appends `tests` and executes the whole thing in a sandboxed subprocess. A task
passes for an attempt iff the process exits 0 (all asserts held, no exception).

Difficulty spread is deliberate: easy warm-ups, then stateful design, DP, and a
parser — enough to separate the models rather than have everyone score 100%.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    name: str
    difficulty: str          # easy | medium | hard
    entrypoint: str          # symbol the tests call
    prompt: str              # instruction sent to the model
    tests: str               # assert-based harness appended after the model code


def _p(signature: str, spec: str) -> str:
    """Build a uniform 'code only' prompt so format discipline is part of the test."""
    return (
        f"{spec}\n\n"
        f"Implement exactly this signature:\n\n    {signature}\n\n"
        "Return ONLY one fenced ```python code block — the implementation and any "
        "imports it needs. No prose before or after, no example usage, no tests."
    )


TASKS: list[Task] = [
    Task(
        name="two_sum",
        difficulty="easy",
        entrypoint="two_sum",
        prompt=_p(
            "def two_sum(nums: list[int], target: int) -> list[int]:",
            "Return the indices of the two numbers in `nums` that add up to "
            "`target`, as a list `[i, j]` with i < j. Exactly one solution "
            "exists and you may not use the same element twice.",
        ),
        tests="""
assert sorted(two_sum([2,7,11,15], 9)) == [0,1]
assert sorted(two_sum([3,2,4], 6)) == [1,2]
assert sorted(two_sum([3,3], 6)) == [0,1]
assert sorted(two_sum([-1,-2,-3,-4,-5], -8)) == [2,4]
""",
    ),
    Task(
        name="valid_parentheses",
        difficulty="easy",
        entrypoint="is_valid",
        prompt=_p(
            "def is_valid(s: str) -> bool:",
            "Given a string `s` of just the characters '()[]{}', return True if "
            "every bracket is closed by the same type in the correct order.",
        ),
        tests="""
assert is_valid("()") is True
assert is_valid("()[]{}") is True
assert is_valid("(]") is False
assert is_valid("([)]") is False
assert is_valid("{[]}") is True
assert is_valid("") is True
assert is_valid("(") is False
""",
    ),
    Task(
        name="merge_intervals",
        difficulty="medium",
        entrypoint="merge",
        prompt=_p(
            "def merge(intervals: list[list[int]]) -> list[list[int]]:",
            "Merge all overlapping intervals and return the non-overlapping "
            "intervals that cover all the input, sorted by start.",
        ),
        tests="""
assert merge([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]
assert merge([[1,4],[4,5]]) == [[1,5]]
assert merge([[1,4],[2,3]]) == [[1,4]]
assert merge([[1,4]]) == [[1,4]]
assert merge([[1,4],[0,4]]) == [[0,4]]
assert merge([[1,4],[0,0]]) == [[0,0],[1,4]]
""",
    ),
    Task(
        name="lru_cache",
        difficulty="hard",
        entrypoint="LRUCache",
        prompt=_p(
            "class LRUCache:  # __init__(self, capacity: int); get(self, key:int)->int; put(self, key:int, value:int)->None",
            "Design a Least-Recently-Used cache. `get(key)` returns the value or "
            "-1 if absent and marks the key most-recently-used. `put(key,value)` "
            "inserts/updates and evicts the least-recently-used entry when over "
            "capacity. Both operations should be O(1) average.",
        ),
        tests="""
c = LRUCache(2)
c.put(1,1); c.put(2,2)
assert c.get(1) == 1
c.put(3,3)              # evicts key 2
assert c.get(2) == -1
c.put(4,4)              # evicts key 1
assert c.get(1) == -1
assert c.get(3) == 3
assert c.get(4) == 4
c2 = LRUCache(1)
c2.put(1,10); c2.put(1,20)
assert c2.get(1) == 20
""",
    ),
    Task(
        name="edit_distance",
        difficulty="hard",
        entrypoint="min_distance",
        prompt=_p(
            "def min_distance(word1: str, word2: str) -> int:",
            "Return the minimum number of single-character insertions, deletions, "
            "or substitutions required to turn `word1` into `word2` "
            "(Levenshtein distance).",
        ),
        tests="""
assert min_distance("horse", "ros") == 3
assert min_distance("intention", "execution") == 5
assert min_distance("", "abc") == 3
assert min_distance("abc", "") == 3
assert min_distance("same", "same") == 0
assert min_distance("a", "b") == 1
""",
    ),
    Task(
        name="calc",
        difficulty="hard",
        entrypoint="calculate",
        prompt=_p(
            "def calculate(s: str) -> int:",
            "Evaluate a basic arithmetic expression string containing "
            "non-negative integers and the operators + - * / with normal "
            "precedence (no parentheses). Integer division truncates toward "
            "zero. The expression is always valid.",
        ),
        tests="""
assert calculate("3+2*2") == 7
assert calculate(" 3/2 ") == 1
assert calculate(" 3+5 / 2 ") == 5
assert calculate("14-3/2") == 13
assert calculate("100") == 100
assert calculate("2*3+4*5") == 26
""",
    ),
    # --- Phase 3 additions: parser, DP, and data-shape transform --------------
    Task(
        name="decode_string",
        difficulty="hard",
        entrypoint="decode_string",
        prompt=_p(
            "def decode_string(s: str) -> str:",
            "Decode a string encoded as `k[encoded]`, where `encoded` is repeated "
            "exactly `k` times. `k` is a positive integer and may be multiple "
            "digits; brackets may be nested. The input always contains only "
            "digits, square brackets, and lowercase letters and is well-formed. "
            "Example: '3[a2[c]]' decodes to 'accaccacc'.",
        ),
        tests="""
assert decode_string("3[a]2[bc]") == "aaabcbc"
assert decode_string("3[a2[c]]") == "accaccacc"
assert decode_string("2[abc]3[cd]ef") == "abcabccdcdcdef"
assert decode_string("abc") == "abc"
assert decode_string("10[a]") == "a" * 10
assert decode_string("2[2[b]]") == "bbbb"
""",
    ),
    Task(
        name="coin_change",
        difficulty="hard",
        entrypoint="coin_change",
        prompt=_p(
            "def coin_change(coins: list[int], amount: int) -> int:",
            "Return the fewest number of coins from `coins` (unlimited supply of "
            "each denomination) that sum to exactly `amount`, or -1 if no "
            "combination does. `amount` of 0 needs 0 coins.",
        ),
        tests="""
assert coin_change([1,2,5], 11) == 3
assert coin_change([2], 3) == -1
assert coin_change([1], 0) == 0
assert coin_change([1,2,5], 0) == 0
assert coin_change([186,419,83,408], 6249) == 20
assert coin_change([2,5,10,1], 27) == 4
""",
    ),
    Task(
        name="flatten_dict",
        difficulty="medium",
        entrypoint="flatten_dict",
        prompt=_p(
            "def flatten_dict(d: dict) -> dict:",
            "Flatten a nested dictionary into a single-level dict whose keys are "
            "the paths to each leaf joined by '.' (a dot). Recurse into nested "
            "dicts only; treat any non-dict value (including lists) as a leaf and "
            "keep it unchanged. Example: {'a':{'b':1}} -> {'a.b':1}.",
        ),
        tests="""
assert flatten_dict({"a":1,"b":{"c":2,"d":{"e":3}}}) == {"a":1,"b.c":2,"b.d.e":3}
assert flatten_dict({"x":{"y":{"z":1}}}) == {"x.y.z":1}
assert flatten_dict({"a":1}) == {"a":1}
assert flatten_dict({}) == {}
assert flatten_dict({"a":{"b":1},"c":[1,2]}) == {"a.b":1,"c":[1,2]}
""",
    ),
]
