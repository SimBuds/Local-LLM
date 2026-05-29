"""
Learning/explain battery for run-learn.py.

Unlike coding_tasks.py (which says "code only"), each task here asks for working
code PLUS a teaching explanation. Scoring is two-part: the code must still pass
hidden asserts (execution gate), and the explanation is graded by a local LLM
judge against a fixed rubric. This targets "best local coding tutor", not just
"best code writer".

Tasks are chosen so the explanation actually carries weight — algorithms where
the *why* (invariant, complexity, tradeoff) is the learning, not the syntax.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LearnTask:
    name: str
    difficulty: str
    entrypoint: str
    topic: str               # short description handed to the judge for context
    prompt: str
    tests: str


def _p(signature: str, spec: str) -> str:
    return (
        f"{spec}\n\n"
        f"Implement this signature:\n\n    {signature}\n\n"
        "Respond in two parts:\n"
        "1. The implementation in a single fenced ```python code block.\n"
        "2. A short explanation covering: the approach, the time AND space "
        "complexity (Big-O), one alternative approach and its tradeoff, and one "
        "edge case or pitfall to watch for.\n"
        "Write the explanation for someone learning — clear and concrete, not padded."
    )


TASKS: list[LearnTask] = [
    LearnTask(
        name="binary_search",
        difficulty="easy",
        entrypoint="search",
        topic="iterative binary search over a sorted array",
        prompt=_p(
            "def search(nums: list[int], target: int) -> int:",
            "Given a sorted ascending list `nums` and a `target`, return the "
            "index of target or -1 if absent. Must run in O(log n).",
        ),
        tests="""
assert search([-1,0,3,5,9,12], 9) == 4
assert search([-1,0,3,5,9,12], 2) == -1
assert search([5], 5) == 0
assert search([], 1) == -1
assert search([1,2,3,4,5], 1) == 0
assert search([1,2,3,4,5], 5) == 4
""",
    ),
    LearnTask(
        name="merge_intervals",
        difficulty="medium",
        entrypoint="merge",
        topic="merging overlapping intervals via sort-and-sweep",
        prompt=_p(
            "def merge(intervals: list[list[int]]) -> list[list[int]]:",
            "Merge all overlapping intervals and return the non-overlapping set "
            "sorted by start.",
        ),
        tests="""
assert merge([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]
assert merge([[1,4],[4,5]]) == [[1,5]]
assert merge([[1,4],[2,3]]) == [[1,4]]
assert merge([[1,4]]) == [[1,4]]
""",
    ),
    LearnTask(
        name="lru_cache",
        difficulty="hard",
        entrypoint="LRUCache",
        topic="O(1) LRU cache design (hash map + doubly linked list / ordered dict)",
        prompt=_p(
            "class LRUCache:  # __init__(self, capacity); get(key)->int; put(key, value)->None",
            "Design an O(1) Least-Recently-Used cache. `get` returns the value or "
            "-1 and marks it most-recently-used; `put` inserts/updates and evicts "
            "the least-recently-used entry when over capacity.",
        ),
        tests="""
c = LRUCache(2)
c.put(1,1); c.put(2,2)
assert c.get(1) == 1
c.put(3,3)
assert c.get(2) == -1
c.put(4,4)
assert c.get(1) == -1
assert c.get(3) == 3 and c.get(4) == 4
""",
    ),
    LearnTask(
        name="edit_distance",
        difficulty="hard",
        entrypoint="min_distance",
        topic="Levenshtein edit distance via dynamic programming",
        prompt=_p(
            "def min_distance(word1: str, word2: str) -> int:",
            "Return the minimum single-character insertions, deletions, or "
            "substitutions to turn word1 into word2 (Levenshtein distance).",
        ),
        tests="""
assert min_distance("horse", "ros") == 3
assert min_distance("intention", "execution") == 5
assert min_distance("", "abc") == 3
assert min_distance("same", "same") == 0
""",
    ),
]
