"""
Coding benchmark battery for run-code.py.

Each task is a self-contained Python problem with hidden tests. The runner asks
a model to emit ONLY a fenced ```python block implementing `entrypoint`, then
appends `tests` and executes the whole thing in a sandboxed subprocess. A task
passes for an attempt iff the process exits 0 (all asserts held, no exception).

Difficulty spread is deliberate: easy warm-ups, then stateful design, DP, and a
parser — enough to separate the models rather than have everyone score 100%.

TASKS is nine classic textbook problems followed by six fresh tasks written for
this repo. Only the fresh ones carry validity fixtures, checked by test_code.py.
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
    # Fresh tasks only, used by test_code.py and never by a run. They prove the
    # hidden asserts: `solution` and the differently written `alt_solution` must
    # pass them, and `exploit`, a plausible wrong shortcut that still gets the
    # prompt's example right, must fail them.
    solution: str = ""
    alt_solution: str = ""
    exploit: str = ""


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


# --- fresh tasks ---------------------------------------------------------------
# The nine tasks above are textbook problems that every model has seen many
# times. These are small business rules written for this repo on 2026-11-19, so
# a pass shows the model worked the rule out. They run beside the classics
# (Casey, 2026-11-19): the list is added to TASKS at the bottom of this file, after
# the nine, so the classic columns in a summary still compare with earlier passes.

FRESH_TASKS: list[Task] = [
    Task(
        name="split_bill",
        difficulty="medium",
        entrypoint="split_bill",
        prompt=_p(
            "def split_bill(total_cents: int, weights: list[int]) -> list[int]:",
            "Split `total_cents` among people in proportion to their integer "
            "`weights`, and return each person's share in whole cents, in the same "
            "order. Each person first gets their exact proportional share rounded "
            "down. The cents left over are then handed out one each, to the people "
            "whose exact share had the largest fractional part first. When two "
            "fractional parts are equal, the person earlier in the list comes "
            "first. The shares always add up to `total_cents`. Weights are zero or "
            "more and at least one is positive. A weight of zero gets nothing. "
            "Example: split_bill(100, [1, 1, 1]) returns [34, 33, 33].",
        ),
        tests="""
assert split_bill(100, [1, 2]) == [33, 67]
assert split_bill(0, [1, 2]) == [0, 0]
assert split_bill(5, [0, 1, 0]) == [0, 5, 0]
assert split_bill(10, [3, 3, 3, 1]) == [3, 3, 3, 1]
assert split_bill(1, [1, 1, 1, 1]) == [1, 0, 0, 0]
assert split_bill(1000, [7, 3, 5]) == [467, 200, 333]
assert split_bill(101, [1, 1]) == [51, 50]
assert split_bill(2, [1, 1, 1]) == [1, 1, 0]
assert split_bill(100, [1, 1, 4]) == [17, 17, 66]
assert split_bill(1000000000001, [1, 1, 1]) == [333333333334, 333333333334, 333333333333]
""",
        solution="""def split_bill(total_cents: int, weights: list[int]) -> list[int]:
    whole = sum(weights)
    shares = [total_cents * w // whole for w in weights]
    remainders = [total_cents * w % whole for w in weights]
    left = total_cents - sum(shares)
    by_need = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for i in by_need[:left]:
        shares[i] += 1
    return shares
""",
        alt_solution="""from fractions import Fraction


def split_bill(total_cents, weights):
    exact = [Fraction(total_cents * w, sum(weights)) for w in weights]
    shares = [int(e) for e in exact]
    while sum(shares) < total_cents:
        best = 0
        for i, e in enumerate(exact):
            if e - int(e) > exact[best] - int(exact[best]):
                best = i
        shares[best] += 1
        exact[best] = Fraction(int(exact[best]))
    return shares
""",
        exploit="""def split_bill(total_cents: int, weights: list[int]) -> list[int]:
    whole = sum(weights)
    shares = [total_cents * w // whole for w in weights]
    for i in range(total_cents - sum(shares)):
        shares[i] += 1
    return shares
""",
    ),
    Task(
        name="paid_minutes",
        difficulty="medium",
        entrypoint="paid_minutes",
        prompt=_p(
            "def paid_minutes(shifts: list[tuple[str, str]]) -> int:",
            "Each shift is a (start, end) pair of 24-hour 'HH:MM' times. A shift "
            "whose end is earlier than its start runs past midnight into the next "
            "day. A shift whose end equals its start has no length. Return the "
            "total paid minutes over all shifts. Breaks are unpaid and are taken "
            "per shift: a shift longer than 6 hours loses 30 minutes, and a shift "
            "longer than 10 hours loses 60 minutes in total. A shift of exactly 6 "
            "hours loses nothing, and one of exactly 10 hours loses 30. "
            "Example: paid_minutes([('09:00', '17:00')]) returns 450.",
        ),
        tests="""
assert paid_minutes([]) == 0
assert paid_minutes([("22:00", "02:00")]) == 240
assert paid_minutes([("08:00", "14:00")]) == 360
assert paid_minutes([("08:00", "14:01")]) == 331
assert paid_minutes([("06:00", "16:00")]) == 570
assert paid_minutes([("06:00", "16:30")]) == 570
assert paid_minutes([("12:00", "12:00")]) == 0
assert paid_minutes([("20:00", "07:00")]) == 600
assert paid_minutes([("09:00", "12:00"), ("13:00", "17:00")]) == 420
assert paid_minutes([("23:30", "00:15"), ("00:00", "23:59")]) == 45 + 1439 - 60
""",
        solution="""def paid_minutes(shifts: list[tuple[str, str]]) -> int:
    def minutes(clock: str) -> int:
        hours, mins = clock.split(":")
        return int(hours) * 60 + int(mins)

    total = 0
    for start, end in shifts:
        length = (minutes(end) - minutes(start)) % (24 * 60)
        if length > 600:
            length -= 60
        elif length > 360:
            length -= 30
        total += length
    return total
""",
        alt_solution="""from datetime import datetime, timedelta


def paid_minutes(shifts):
    paid = timedelta()
    for start, end in shifts:
        begin = datetime.strptime(start, "%H:%M")
        finish = datetime.strptime(end, "%H:%M")
        if finish < begin:
            finish += timedelta(days=1)
        worked = finish - begin
        unpaid = sum(30 for limit in (6, 10) if worked > timedelta(hours=limit))
        paid += worked - timedelta(minutes=unpaid)
    return int(paid.total_seconds()) // 60
""",
        exploit="""def paid_minutes(shifts: list[tuple[str, str]]) -> int:
    def minutes(clock: str) -> int:
        hours, mins = clock.split(":")
        return int(hours) * 60 + int(mins)

    total = 0
    for start, end in shifts:
        length = (minutes(end) - minutes(start)) % (24 * 60)
        if length >= 600:
            length -= 60
        elif length >= 360:
            length -= 30
        total += length
    return total
""",
    ),
    Task(
        name="irv_winner",
        difficulty="hard",
        entrypoint="irv_winner",
        prompt=_p(
            "def irv_winner(ballots: list[list[str]]) -> str | None:",
            "Count an instant-runoff election. Each ballot lists candidate names "
            "in order of preference and may be empty. Every name on any ballot "
            "starts in the race. In each round a ballot counts for its "
            "highest-ranked candidate still in the race. A ballot with no "
            "candidate left in the race is exhausted and stops counting. A "
            "candidate wins when they hold strictly more than half of the ballots "
            "still counting. If nobody wins the round, the candidate with the "
            "fewest votes leaves the race, and when several are tied for fewest, "
            "the one whose name sorts last alphabetically leaves. Repeat until "
            "someone wins. Return the winner's name, or None when no ballot is "
            "counting. Example: irv_winner([['A', 'B'], ['A'], ['B', 'A']]) "
            "returns 'A'.",
        ),
        tests="""
assert irv_winner([]) is None
assert irv_winner([[], []]) is None
assert irv_winner([["A", "B"], ["B", "A"], ["C", "B"]]) == "B"
assert irv_winner([["A"], ["B"]]) == "A"
assert irv_winner([[], ["Zed"]]) == "Zed"
assert irv_winner([["A", "C"], ["B", "C"], ["B", "A"], ["A", "B"], ["C"]]) == "A"
assert irv_winner([["A", "D"], ["A", "D"], ["B", "D"], ["B", "D"], ["C", "B"]]) == "B"
assert irv_winner([["Ann", "Bob"], ["Bob", "Cy"], ["Cy", "Bob"], ["Cy", "Bob"], ["Ann", "Bob"]]) == "Cy"
assert irv_winner([["Bob", "Ann"], ["Ann", "Bob"], ["Cy", "Ann"], ["Dee", "Ann"]]) == "Ann"
""",
        solution="""def irv_winner(ballots: list[list[str]]) -> str | None:
    racing = {name for ballot in ballots for name in ballot}
    while racing:
        votes = dict.fromkeys(racing, 0)
        counting = 0
        for ballot in ballots:
            for name in ballot:
                if name in racing:
                    votes[name] += 1
                    counting += 1
                    break
        for name, count in votes.items():
            if count * 2 > counting:
                return name
        fewest = min(votes.values())
        racing.remove(max(name for name, count in votes.items() if count == fewest))
    return None
""",
        alt_solution="""def irv_winner(ballots):
    live = [list(b) for b in ballots if b]
    if not live:
        return None
    names = sorted({n for b in live for n in b})
    tally = {n: sum(1 for b in live if b[0] == n) for n in names}
    top = max(names, key=lambda n: tally[n])
    if 2 * tally[top] > len(live):
        return top
    loser = max(n for n in names if tally[n] == min(tally.values()))
    return irv_winner([[n for n in b if n != loser] for b in live])
""",
        exploit="""def irv_winner(ballots: list[list[str]]) -> str | None:
    firsts = [ballot[0] for ballot in ballots if ballot]
    if not firsts:
        return None
    return min(set(firsts), key=lambda name: (-firsts.count(name), name))
""",
    ),
    Task(
        name="sort_versions",
        difficulty="medium",
        entrypoint="sort_versions",
        prompt=_p(
            "def sort_versions(versions: list[str]) -> list[str]:",
            "Return the version strings sorted from oldest to newest. A version is "
            "one to three dot-separated numbers, optionally followed by a hyphen "
            "and a pre-release tag: 'alpha', 'beta', or 'rc', with an optional "
            "number straight after it, as in '2.0.0-rc2'. Compare the numbers as "
            "integers, and treat a missing number as 0, so '1.2' equals '1.2.0'. A "
            "pre-release comes before the same version without a tag. Among "
            "pre-releases of the same version, alpha comes before beta and beta "
            "before rc, and then the lower tag number comes first, with a missing "
            "tag number counting as 0. Versions that compare equal keep their "
            "input order, and every string is returned unchanged. "
            "Example: sort_versions(['1.10.0', '1.2.0']) returns ['1.2.0', '1.10.0'].",
        ),
        tests="""
assert sort_versions([]) == []
assert sort_versions(["1.0.0", "1.0.0-rc1", "1.0.0-beta", "1.0.0-alpha2", "1.0.0-alpha"]) == ["1.0.0-alpha", "1.0.0-alpha2", "1.0.0-beta", "1.0.0-rc1", "1.0.0"]
assert sort_versions(["1.2", "1.2.0-rc", "1.1.9"]) == ["1.1.9", "1.2.0-rc", "1.2"]
assert sort_versions(["2.0.0-rc10", "2.0.0-rc9"]) == ["2.0.0-rc9", "2.0.0-rc10"]
assert sort_versions(["1.0", "1.0.0", "1"]) == ["1.0", "1.0.0", "1"]
assert sort_versions(["0.9.12", "0.10.1", "0.9.2"]) == ["0.9.2", "0.9.12", "0.10.1"]
assert sort_versions(["3.0.0-beta2", "3.0.0-alpha", "2.9.9", "3.0.0"]) == ["2.9.9", "3.0.0-alpha", "3.0.0-beta2", "3.0.0"]
assert sort_versions(["2-beta", "2.0-alpha", "1.99.99"]) == ["1.99.99", "2.0-alpha", "2-beta"]
assert sort_versions(["1.0.0-rc", "1.0.0-rc0", "1.0.0-beta9"]) == ["1.0.0-beta9", "1.0.0-rc", "1.0.0-rc0"]
""",
        solution="""TAGS = {"alpha": 0, "beta": 1, "rc": 2}


def sort_versions(versions: list[str]) -> list[str]:
    def key(version: str):
        numbers, _, tag = version.partition("-")
        parts = [int(n) for n in numbers.split(".")]
        parts += [0] * (3 - len(parts))
        if not tag:
            return (*parts, len(TAGS), 0)
        name = tag.rstrip("0123456789")
        return (*parts, TAGS[name], int(tag[len(name):] or 0))

    return sorted(versions, key=key)
""",
        alt_solution="""import functools
import re


def _split(version):
    m = re.fullmatch(r"(\\d+)(?:\\.(\\d+))?(?:\\.(\\d+))?(?:-(alpha|beta|rc)(\\d*))?", version)
    major, minor, patch, tag, tag_no = m.groups()
    return [int(major), int(minor or 0), int(patch or 0)], tag, int(tag_no or 0)


def _compare(a, b):
    (na, ta, ia), (nb, tb, ib) = _split(a), _split(b)
    if na != nb:
        return -1 if na < nb else 1
    if ta == tb:
        return (ia > ib) - (ia < ib)
    if ta is None:
        return 1
    if tb is None:
        return -1
    order = ["alpha", "beta", "rc"]
    return -1 if order.index(ta) < order.index(tb) else 1


def sort_versions(versions):
    return sorted(versions, key=functools.cmp_to_key(_compare))
""",
        exploit="""def sort_versions(versions: list[str]) -> list[str]:
    def key(version: str):
        numbers, _, tag = version.partition("-")
        parts = [int(n) for n in numbers.split(".")]
        parts += [0] * (3 - len(parts))
        return (*parts, tag)

    return sorted(versions, key=key)
""",
    ),
    Task(
        name="meeting_room",
        difficulty="hard",
        entrypoint="MeetingRoom",
        prompt=_p(
            "class MeetingRoom:  # __init__(self); book(self, start: int, end: int, owner: str) -> bool; "
            "cancel(self, owner: str) -> int; free_slots(self, day_start: int, day_end: int) -> list[tuple[int, int]]",
            "Track the bookings of one meeting room. Times are whole minutes. A "
            "booking covers `start` up to but not including `end`, so one booking "
            "may begin at the minute another ends. `book` adds the booking and "
            "returns True, or changes nothing and returns False when `start` is "
            "not before `end` or when the booking would overlap an existing one. "
            "`cancel` removes every booking made by `owner` and returns how many "
            "it removed. `free_slots` returns the unbooked stretches between "
            "`day_start` and `day_end` as a list of (start, end) tuples in time "
            "order, each cut to fit inside that window, with no empty stretch "
            "listed. Example: on a new room, book(60, 120, 'ann') returns True "
            "and then book(90, 150, 'bob') returns False.",
        ),
        tests="""
r = MeetingRoom()
assert r.book(540, 600, "ann") is True
assert r.book(600, 660, "bob") is True
assert r.book(480, 540, "cy") is True
assert r.book(599, 601, "dee") is False
assert r.book(700, 700, "dee") is False
assert r.book(710, 705, "dee") is False
assert r.book(500, 520, "dee") is False
assert r.book(400, 700, "dee") is False
assert r.free_slots(480, 720) == [(660, 720)]
assert r.free_slots(420, 660) == [(420, 480)]
assert r.cancel("dee") == 0
assert r.cancel("ann") == 1
assert r.cancel("ann") == 0
assert r.free_slots(480, 720) == [(540, 600), (660, 720)]
assert r.book(540, 570, "bob") is True
assert r.cancel("bob") == 2
assert r.free_slots(0, 1440) == [(0, 480), (540, 1440)]
assert r.free_slots(500, 530) == []
assert r.free_slots(530, 550) == [(540, 550)]
assert MeetingRoom().free_slots(60, 120) == [(60, 120)]
""",
        solution="""class MeetingRoom:
    def __init__(self):
        self.bookings = []  # (start, end, owner)

    def book(self, start: int, end: int, owner: str) -> bool:
        if start >= end:
            return False
        if any(start < b_end and b_start < end for b_start, b_end, _ in self.bookings):
            return False
        self.bookings.append((start, end, owner))
        return True

    def cancel(self, owner: str) -> int:
        kept = [b for b in self.bookings if b[2] != owner]
        removed = len(self.bookings) - len(kept)
        self.bookings = kept
        return removed

    def free_slots(self, day_start: int, day_end: int) -> list[tuple[int, int]]:
        slots, cursor = [], day_start
        for start, end, _ in sorted(self.bookings):
            if end <= cursor or start >= day_end:
                continue
            if start > cursor:
                slots.append((cursor, start))
            cursor = end
        if cursor < day_end:
            slots.append((cursor, day_end))
        return slots
""",
        alt_solution="""class MeetingRoom:
    def __init__(self):
        self.taken = {}  # minute -> owner
        self.count = {}  # owner -> number of bookings

    def book(self, start, end, owner):
        minutes = range(start, end)
        if len(minutes) == 0 or any(m in self.taken for m in minutes):
            return False
        for m in minutes:
            self.taken[m] = owner
        self.count[owner] = self.count.get(owner, 0) + 1
        return True

    def cancel(self, owner):
        self.taken = {m: o for m, o in self.taken.items() if o != owner}
        return self.count.pop(owner, 0)

    def free_slots(self, day_start, day_end):
        slots, begin = [], None
        for m in range(day_start, day_end + 1):
            free = m < day_end and m not in self.taken
            if free and begin is None:
                begin = m
            elif not free and begin is not None:
                slots.append((begin, m))
                begin = None
        return slots
""",
        exploit="""class MeetingRoom:
    def __init__(self):
        self.bookings = []

    def book(self, start: int, end: int, owner: str) -> bool:
        if start >= end:
            return False
        if any(start <= b_end and b_start <= end for b_start, b_end, _ in self.bookings):
            return False
        self.bookings.append((start, end, owner))
        return True

    def cancel(self, owner: str) -> int:
        kept = [b for b in self.bookings if b[2] != owner]
        removed = len(self.bookings) - len(kept)
        self.bookings = kept
        return removed

    def free_slots(self, day_start: int, day_end: int) -> list[tuple[int, int]]:
        slots, cursor = [], day_start
        for start, end, _ in sorted(self.bookings):
            if end <= cursor or start >= day_end:
                continue
            if start > cursor:
                slots.append((cursor, start))
            cursor = end
        if cursor < day_end:
            slots.append((cursor, day_end))
        return slots
""",
    ),
    Task(
        name="tally_stock",
        difficulty="medium",
        entrypoint="tally_stock",
        prompt=_p(
            "def tally_stock(events: list[str]) -> dict[str, int]:",
            "Work out stock levels from a list of event lines, applied in order. "
            "Fields are separated by one or more spaces. 'IN item qty' adds qty. "
            "'OUT item qty' removes qty, but only when at least that many are in "
            "stock. Otherwise the line is ignored and nothing is removed. "
            "'RENAME old new' moves all of old's stock onto new, adding to any "
            "stock new already has, and does nothing when old has no stock. Item "
            "names are case-insensitive and are reported in lower case. Verbs are "
            "upper case. Ignore any line that does not fit: an unknown verb, the "
            "wrong number of fields, or a qty that is not a whole number above "
            "zero. Return a dict of item to quantity, leaving out items with "
            "nothing in stock. "
            "Example: tally_stock(['IN bolt 5', 'OUT bolt 2']) returns {'bolt': 3}.",
        ),
        tests="""
assert tally_stock([]) == {}
assert tally_stock(["IN Bolt 5", "in bolt 2", "IN BOLT 1"]) == {"bolt": 6}
assert tally_stock(["IN nut 3", "OUT nut 5"]) == {"nut": 3}
assert tally_stock(["IN nut 3", "OUT nut 3"]) == {}
assert tally_stock(["OUT nut 1", "IN nut 2"]) == {"nut": 2}
assert tally_stock(["IN a 2", "RENAME a b", "IN A 1"]) == {"b": 2, "a": 1}
assert tally_stock(["IN a 2", "IN b 3", "RENAME A B"]) == {"b": 5}
assert tally_stock(["IN a x", "IN a -1", "IN a 0", "IN a 1 extra", "IN a", "OUT a 1", "IN a 4"]) == {"a": 4}
assert tally_stock(["RENAME ghost b", "STOCKTAKE a 5", ""]) == {}
assert tally_stock(["IN a 2", "RENAME a a"]) == {"a": 2}
assert tally_stock(["IN   bolt   2", "OUT bolt   1"]) == {"bolt": 1}
assert tally_stock(["IN a 2", "IN b 1", "OUT a 3", "OUT b 1", "OUT a 2"]) == {}
""",
        solution="""def tally_stock(events: list[str]) -> dict[str, int]:
    stock: dict[str, int] = {}
    for line in events:
        fields = line.split()
        if len(fields) != 3:
            continue
        verb, first, last = fields[0], fields[1].lower(), fields[2]
        if verb == "RENAME":
            if stock.get(first) and first != last.lower():
                stock[last.lower()] = stock.get(last.lower(), 0) + stock.pop(first)
            continue
        if verb not in ("IN", "OUT") or not last.isdigit() or int(last) == 0:
            continue
        qty = int(last)
        if verb == "IN":
            stock[first] = stock.get(first, 0) + qty
        elif stock.get(first, 0) >= qty:
            stock[first] -= qty
    return {item: qty for item, qty in stock.items() if qty > 0}
""",
        alt_solution="""from collections import Counter


def tally_stock(events):
    stock = Counter()
    for line in events:
        match line.split():
            case ["IN", item, qty] if qty.isdecimal() and int(qty) > 0:
                stock[item.lower()] += int(qty)
            case ["OUT", item, qty] if qty.isdecimal() and 0 < int(qty) <= stock[item.lower()]:
                stock[item.lower()] -= int(qty)
            case ["RENAME", old, new] if old.lower() != new.lower():
                stock[new.lower()] += stock.pop(old.lower(), 0)
            case _:
                pass
    return {item: qty for item, qty in sorted(stock.items()) if qty > 0}
""",
        exploit="""def tally_stock(events: list[str]) -> dict[str, int]:
    stock: dict[str, int] = {}
    for line in events:
        fields = line.split()
        if len(fields) != 3:
            continue
        verb, first, last = fields[0], fields[1].lower(), fields[2]
        if verb == "RENAME":
            if stock.get(first) and first != last.lower():
                stock[last.lower()] = stock.get(last.lower(), 0) + stock.pop(first)
            continue
        if verb not in ("IN", "OUT") or not last.isdigit() or int(last) == 0:
            continue
        qty = int(last)
        if verb == "IN":
            stock[first] = stock.get(first, 0) + qty
        else:
            stock[first] = max(0, stock.get(first, 0) - qty)
    return {item: qty for item, qty in stock.items() if qty > 0}
""",
    ),
]

TASKS.extend(FRESH_TASKS)
