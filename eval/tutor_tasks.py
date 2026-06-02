#!/usr/bin/env python3
"""
Tutor benchmark tasks for run-tutor.py.

These tasks ask the model to teach a learner the problem without providing a
complete working solution. Hidden asserts are used to detect full-solution leaks,
so good tutor responses should explain the idea while avoiding a finished answer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TutorTask:
    name: str
    difficulty: str
    entrypoint: str
    topic: str
    prompt: str
    tests: str


def _p(signature: str, spec: str) -> str:
    return (
        f"{spec}\n\n"
        "You are a coding tutor helping a capable learner solve this problem. "
        "Do NOT provide a complete working implementation. Explain the key idea, "
        "the proper algorithmic approach, the important edge cases, and the next "
        "concrete steps the learner should take. If you include code, keep it "
        "illustrative and partial, not a finished function or class. Focus on "
        "teaching the learner how to write the solution themself.\n\n"
        f"Signature to implement:\n\n    {signature}\n"
    )


TASKS: list[TutorTask] = [
    TutorTask(
        name="two_sum",
        difficulty="easy",
        entrypoint="two_sum",
        topic="two-sum index pair with a target sum",
        prompt=_p(
            "def two_sum(nums: list[int], target: int) -> list[int]:",
            "Given a list of integers `nums` and an integer `target`, help the learner "
            "find the two indices whose values add up to `target` and return them "
            "as `[i, j]` with `i < j`. Exactly one solution exists and the same "
            "element may not be used twice.",
        ),
        tests="""
assert sorted(two_sum([2,7,11,15], 9)) == [0,1]
assert sorted(two_sum([3,2,4], 6)) == [1,2]
assert sorted(two_sum([3,3], 6)) == [0,1]
assert sorted(two_sum([-1,-2,-3,-4,-5], -8)) == [2,4]
""",
    ),
    TutorTask(
        name="valid_parentheses",
        difficulty="easy",
        entrypoint="is_valid",
        topic="valid parentheses bracket matching",
        prompt=_p(
            "def is_valid(s: str) -> bool:",
            "Given a string `s` containing only the characters '()[]{}', help the "
            "learner determine whether the brackets are closed in the correct order "
            "and by matching types.",
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
    TutorTask(
        name="merge_intervals",
        difficulty="medium",
        entrypoint="merge",
        topic="merging overlapping intervals",
        prompt=_p(
            "def merge(intervals: list[list[int]]) -> list[list[int]]:",
            "Help the learner merge overlapping intervals and return the minimal "
            "set of non-overlapping intervals sorted by start.",
        ),
        tests="""
assert merge([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]
assert merge([[1,4],[4,5]]) == [[1,5]]
assert merge([[1,4],[2,3]]) == [[1,4]]
assert merge([[1,4]]) == [[1,4]]
assert merge([[1,4],[0,0]]) == [[0,0],[1,4]]
""",
    ),
    TutorTask(
        name="lru_cache",
        difficulty="hard",
        entrypoint="LRUCache",
        topic="LRU cache design for O(1) gets and puts",
        prompt=_p(
            "class LRUCache:  # __init__(self, capacity: int); get(self, key:int)->int; put(self, key:int, value:int)->None",
            "Help the learner design a Least-Recently-Used cache whose `get` and "
            "`put` operations run in average O(1) time. Explain the data structures "
            "and the eviction strategy without giving away a full implementation.",
        ),
        tests="""
c = LRUCache(2)
c.put(1,1); c.put(2,2)
assert c.get(1) == 1
c.put(3,3)
assert c.get(2) == -1
c.put(4,4)
assert c.get(1) == -1
assert c.get(3) == 3
assert c.get(4) == 4
c2 = LRUCache(1)
c2.put(1,10); c2.put(1,20)
assert c2.get(1) == 20
""",
    ),
    TutorTask(
        name="edit_distance",
        difficulty="hard",
        entrypoint="min_distance",
        topic="Levenshtein edit distance via dynamic programming",
        prompt=_p(
            "def min_distance(word1: str, word2: str) -> int:",
            "Help the learner reason about the minimum number of single-character "
            "insertions, deletions, and substitutions required to turn `word1` into "
            "`word2`.",
        ),
        tests="""
assert min_distance("horse", "ros") == 3
assert min_distance("intention", "execution") == 5
assert min_distance("", "abc") == 3
assert min_distance("same", "same") == 0
""",
    ),
]
