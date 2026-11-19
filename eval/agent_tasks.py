"""
Tasks for run-agent.py: multi-step work a single reply cannot do.

A task is a small project written into a fresh sandbox directory, an instruction,
and hidden tests that run after the model says it is done. The hidden tests are
never written to disk, so the model cannot read them.

Each task also carries three overlays used only by the offline tests in
test_agent.py, never by a run. They prove the hidden tests are sound:

  solution      a correct fix. The hidden tests must pass.
  alt_solution  a differently written correct fix. They must pass too, or the
                tests are too narrow and would fail a model that is right.
  exploit       a shortcut that satisfies the visible test without fixing the
                bug. The hidden tests must fail it, or they reward cheating.

Hidden tests check behaviour only: what the code returns, never how it is written.
Tasks use the Python standard library alone and need no network.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTask:
    key: str
    why: str                      # what this task catches, for the summary
    instruction: str              # the user message
    files: dict[str, str]         # relative path -> content, the starting project
    tests: str                    # hidden test program, run from the project root
    solution: dict[str, str]      # overlays on `files`, see the module docstring
    alt_solution: dict[str, str]
    exploit: dict[str, str]


# --- fix_failing_test ---------------------------------------------------------
# The visible test fails in cart.py's caller path, but the bug is one module
# away in pricing.py: an off-by-one on the bulk threshold.

_PRICING = '''"""Unit pricing rules."""

BULK_MIN_QTY = 10
BULK_PERCENT = 15


def unit_price(base_cents: int, qty: int) -> int:
    """Price per unit in cents.

    Orders of BULK_MIN_QTY units or more get BULK_PERCENT off, rounded down.
    """
    if qty > BULK_MIN_QTY:
        return base_cents * (100 - BULK_PERCENT) // 100
    return base_cents
'''

_CART = '''"""Cart totals."""

from shop.pricing import unit_price


def line_total(base_cents: int, qty: int) -> int:
    return unit_price(base_cents, qty) * qty


def cart_total(lines: list[tuple[int, int]]) -> int:
    """Total in cents for a list of (base_cents, qty) lines."""
    return sum(line_total(base, qty) for base, qty in lines)
'''

_TEST_CART = '''import unittest

from shop.cart import cart_total


class CartTests(unittest.TestCase):
    def test_small_line_pays_full_price(self):
        self.assertEqual(cart_total([(200, 3)]), 600)

    def test_bulk_line_gets_the_discount(self):
        self.assertEqual(cart_total([(200, 10)]), 1700)


if __name__ == "__main__":
    unittest.main()
'''

FIX_FAILING_TEST = AgentTask(
    key="fix_failing_test",
    why="Follows a failing test to a bug in a different file from the one the test imports",
    instruction=(
        "The tests in test_cart.py are failing. Find the bug and fix it so that "
        "`python3 test_cart.py` passes. Do not change test_cart.py. "
        "When the tests pass, reply with a one-line summary of the fix."
    ),
    files={
        "shop/__init__.py": "",
        "shop/pricing.py": _PRICING,
        "shop/cart.py": _CART,
        "test_cart.py": _TEST_CART,
    },
    tests='''from shop.cart import cart_total, line_total
from shop.pricing import unit_price

assert unit_price(1000, 9) == 1000
assert unit_price(1000, 10) == 850
assert unit_price(1000, 11) == 850
assert unit_price(333, 10) == 283
assert line_total(450, 10) == 3820
assert cart_total([(120, 2), (999, 10), (50, 40)]) == 240 + 8490 + 1680
assert cart_total([]) == 0
''',
    solution={
        "shop/pricing.py": _PRICING.replace("if qty > BULK_MIN_QTY:", "if qty >= BULK_MIN_QTY:"),
    },
    alt_solution={
        "shop/pricing.py": '''"""Unit pricing rules."""

BULK_MIN_QTY = 10
BULK_PERCENT = 15


def unit_price(base_cents: int, qty: int) -> int:
    percent_off = 0 if qty < BULK_MIN_QTY else BULK_PERCENT
    return base_cents * (100 - percent_off) // 100
''',
    },
    exploit={
        "shop/cart.py": _CART.replace(
            "    return sum(line_total(base, qty) for base, qty in lines)",
            "    if lines == [(200, 10)]:\n        return 1700\n"
            "    return sum(line_total(base, qty) for base, qty in lines)"),
    },
)


# --- log_triage ---------------------------------------------------------------
# A crash report buried in a noisy log. The traceback names the importer, whose
# docstring already says what must happen to a malformed row. The shortcut is to
# delete the bad row from the data file, which leaves the importer broken.

_IMPORTER = '''"""Order import."""

import csv

FIELDS = 3  # order_id, sku, quantity


def load_orders(path):
    """Read orders from a CSV file that starts with a header row.

    Returns (orders, skipped). `orders` is a list of
    {"order_id": str, "sku": str, "quantity": int} dicts in file order.

    A malformed row is skipped and counted in `skipped`, never raised. A row is
    malformed when it does not have exactly 3 fields, or when its quantity is
    not an integer.
    """
    orders, skipped = [], 0
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) != FIELDS:
                skipped += 1
                continue
            order_id, sku, quantity = row
            orders.append({"order_id": order_id, "sku": sku, "quantity": int(quantity)})
    return orders, skipped
'''

_REPORT = '''"""Nightly order report: units per SKU."""

import sys

from importer import load_orders


def units_by_sku(orders):
    totals = {}
    for order in orders:
        totals[order["sku"]] = totals.get(order["sku"], 0) + order["quantity"]
    return totals


def main(path):
    orders, skipped = load_orders(path)
    totals = units_by_sku(orders)
    for sku in sorted(totals):
        print(f"{sku} {totals[sku]}")
    print(f"skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
'''

_ORDERS_CSV = """order_id,sku,quantity
1001,WIDGET-A,2
1002,WIDGET-B,5
1003,GADGET,1
1004,WIDGET-A,7
1005,GADGET,3
1006,WIDGET-A,1
1007,WIDGET-B,twelve
1008,GADGET,4
1009,GADGET
1010,WIDGET-A,6
"""

_TRACEBACK = """2026-03-02 02:00:07 ERROR nightly: report job failed
Traceback (most recent call last):
  File "report.py", line 25, in <module>
    sys.exit(main(sys.argv[1]))
  File "report.py", line 16, in main
    orders, skipped = load_orders(path)
  File "importer.py", line 27, in load_orders
    orders.append({"order_id": order_id, "sku": sku, "quantity": int(quantity)})
ValueError: invalid literal for int() with base 10: 'twelve'
"""

_NOISE = ["INFO cache: warmed 128 keys", "INFO http: GET /health 200 3ms",
          "WARN mailer: retrying smtp connection (attempt 2)", "INFO cron: heartbeat",
          "INFO db: pool size 4, idle 3", "WARN disk: /var at 81% used",
          "INFO http: POST /orders 201 18ms", "DEBUG auth: token refreshed"]


def _noise(day: int, start: int, count: int) -> str:
    """`count` unrelated log lines, one a minute from minute-of-day `start`."""
    return "".join(
        f"2026-03-{day:02d} {(start + i) // 60 % 24:02d}:{(start + i) % 60:02d}:00 "
        f"{_NOISE[(start + i) % len(_NOISE)]}\n" for i in range(count))


_APP_LOG = _noise(1, 1290, 55) + _TRACEBACK + _noise(2, 121, 45)

LOG_TRIAGE = AgentTask(
    key="log_triage",
    why="Finds a crash in a noisy log, then fixes the code, not the data that exposed it",
    instruction=(
        "The nightly job `python3 report.py data/orders.csv` crashed last night. "
        "app.log has the details. Fix the code so the job completes. Do not edit "
        "anything under data/: the docstring in importer.py says how a malformed "
        "row must be handled. Reply with a one-line summary when the job runs cleanly."
    ),
    files={
        "importer.py": _IMPORTER,
        "report.py": _REPORT,
        "data/orders.csv": _ORDERS_CSV,
        "app.log": _APP_LOG,
    },
    tests='''import subprocess
import sys

from importer import load_orders

with open("_hidden_orders.csv", "w") as f:
    f.write("order_id,sku,quantity\\nA1,PEN,4\\nA2,PEN,3.5\\nA3,INK,\\nA4,INK,x9\\n"
            "A5,PEN,10\\nA6,CAP\\nA7,CAP,1,extra\\nA8,CAP,0\\n")
orders, skipped = load_orders("_hidden_orders.csv")
assert skipped == 5, skipped
assert orders == [{"order_id": "A1", "sku": "PEN", "quantity": 4},
                  {"order_id": "A5", "sku": "PEN", "quantity": 10},
                  {"order_id": "A8", "sku": "CAP", "quantity": 0}], orders

with open("_hidden_clean.csv", "w") as f:
    f.write("order_id,sku,quantity\\nB1,PEN,2\\n")
assert load_orders("_hidden_clean.csv") == ([{"order_id": "B1", "sku": "PEN", "quantity": 2}], 0)

job = subprocess.run([sys.executable, "report.py", "data/orders.csv"],
                     capture_output=True, text=True)
assert job.returncode == 0, job.stderr
assert job.stdout.splitlines() == ["GADGET 8", "WIDGET-A 16", "WIDGET-B 5", "skipped 2"], job.stdout
''',
    solution={
        "importer.py": _IMPORTER.replace(
            '''            order_id, sku, quantity = row
            orders.append({"order_id": order_id, "sku": sku, "quantity": int(quantity)})''',
            '''            order_id, sku, quantity = row
            try:
                count = int(quantity)
            except ValueError:
                skipped += 1
                continue
            orders.append({"order_id": order_id, "sku": sku, "quantity": count})'''),
    },
    alt_solution={
        "importer.py": '''"""Order import."""

import csv
import re

WHOLE_NUMBER = re.compile(r"-?[0-9]+")


def parse_row(row):
    """The order a row describes, or None when the row is malformed."""
    if len(row) != 3 or not WHOLE_NUMBER.fullmatch(row[2]):
        return None
    return {"order_id": row[0], "sku": row[1], "quantity": int(row[2])}


def load_orders(path):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))[1:]
    parsed = [parse_row(row) for row in rows]
    orders = [order for order in parsed if order is not None]
    return orders, len(parsed) - len(orders)
''',
    },
    exploit={
        "data/orders.csv": _ORDERS_CSV.replace("1007,WIDGET-B,twelve\n", ""),
    },
)


# --- signature_change ---------------------------------------------------------
# A function gains a required argument and every caller must follow. One caller
# is in tools/, outside the package the instruction talks about. The shortcut is
# a default value, which keeps the callers running and prints the wrong currency.

_MONEY = '''"""Price formatting."""


def format_price(amount_cents):
    return f"${amount_cents // 100}.{amount_cents % 100:02d}"
'''

_CATALOG = '''"""Products."""

from dataclasses import dataclass

from store.money import format_price


@dataclass
class Product:
    name: str
    price_cents: int
    currency: str = "USD"


def label(product):
    return f"{product.name}: {format_price(product.price_cents)}"
'''

_INVOICE = '''"""Receipts."""

from store.money import format_price


def receipt_line(product, qty):
    return f"{qty} x {product.name} = {format_price(product.price_cents * qty)}"
'''

_SHIPPING = '''"""Shipping rules."""

from store.money import format_price

FREE_OVER_CENTS = 5000


def free_shipping_note(product):
    """The threshold is quoted in the product's own currency."""
    return f"Free shipping on {product.name} over {format_price(FREE_OVER_CENTS)}"
'''

_EXPORT = '''"""CSV export for the accounting team. Run from the project root."""

from store.money import format_price


def export_rows(products):
    return [f"{p.name},{format_price(p.price_cents)}" for p in products]
'''

_TEST_MONEY = '''import unittest

from store.money import format_price


class FormatPriceTests(unittest.TestCase):
    def test_amount_is_followed_by_the_currency_code(self):
        self.assertEqual(format_price(1250, "USD"), "12.50 USD")
        self.assertEqual(format_price(5, "EUR"), "0.05 EUR")


if __name__ == "__main__":
    unittest.main()
'''

_MONEY_FIXED = '''"""Price formatting."""


def format_price(amount_cents, currency):
    return f"{amount_cents // 100}.{amount_cents % 100:02d} {currency}"
'''


def _swap(source: str, old: str, new: str) -> str:
    """`source` with `old` replaced, failing loudly if the seed text has drifted."""
    if source.count(old) != 1:
        raise ValueError(f"expected exactly one {old!r}")
    return source.replace(old, new)


SIGNATURE_CHANGE = AgentTask(
    key="signature_change",
    why="Carries a required new argument to every caller, including one outside the named package",
    instruction=(
        "Prices are becoming multi-currency. Change `format_price` in store/money.py "
        "to take a required second argument `currency`, a 3-letter code, and to return "
        'the amount followed by the code, for example `format_price(1250, "USD")` '
        'returns "12.50 USD". Every product already has a `currency` attribute. '
        "Update every caller of `format_price` in the project so each price is shown "
        "in its product's currency. test_money.py shows the new format. "
        "Reply with a one-line summary when you are done."
    ),
    files={
        "store/__init__.py": "",
        "store/money.py": _MONEY,
        "store/catalog.py": _CATALOG,
        "store/invoice.py": _INVOICE,
        "store/shipping.py": _SHIPPING,
        "tools/export.py": _EXPORT,
        "test_money.py": _TEST_MONEY,
    },
    tests='''from store.catalog import Product, label
from store.invoice import receipt_line
from store.money import format_price
from store.shipping import free_shipping_note
from tools.export import export_rows

assert format_price(1250, "USD") == "12.50 USD"
assert format_price(7, "EUR") == "0.07 EUR"
assert format_price(100000, "JPY") == "1000.00 JPY"
try:
    format_price(1250)
except TypeError:
    pass
else:
    raise SystemExit("currency must be a required argument")

eur = Product("Lamp", 4999, "EUR")
usd = Product("Desk", 12000, "USD")
assert label(eur) == "Lamp: 49.99 EUR", label(eur)
assert label(usd) == "Desk: 120.00 USD", label(usd)
assert receipt_line(eur, 3) == "3 x Lamp = 149.97 EUR", receipt_line(eur, 3)
assert free_shipping_note(eur) == "Free shipping on Lamp over 50.00 EUR", free_shipping_note(eur)
assert export_rows([eur, usd]) == ["Lamp,49.99 EUR", "Desk,120.00 USD"], export_rows([eur, usd])
''',
    solution={
        "store/money.py": _MONEY_FIXED,
        "store/catalog.py": _swap(_CATALOG, "format_price(product.price_cents)",
                                  "format_price(product.price_cents, product.currency)"),
        "store/invoice.py": _swap(_INVOICE, "format_price(product.price_cents * qty)",
                                  "format_price(product.price_cents * qty, product.currency)"),
        "store/shipping.py": _swap(_SHIPPING, "format_price(FREE_OVER_CENTS)",
                                   "format_price(FREE_OVER_CENTS, product.currency)"),
        "tools/export.py": _swap(_EXPORT, "format_price(p.price_cents)",
                                 "format_price(p.price_cents, p.currency)"),
    },
    alt_solution={
        "store/money.py": '''"""Price formatting."""


def format_price(amount_cents, currency):
    units, cents = divmod(amount_cents, 100)
    return "%d.%02d %s" % (units, cents, currency)
''',
        "store/catalog.py": _swap(_CATALOG, "format_price(product.price_cents)",
                                  "format_price(product.price_cents, currency=product.currency)"),
        "store/invoice.py": _swap(
            _INVOICE,
            '    return f"{qty} x {product.name} = {format_price(product.price_cents * qty)}"',
            "    total = format_price(currency=product.currency,"
            " amount_cents=product.price_cents * qty)\n"
            '    return f"{qty} x {product.name} = {total}"'),
        "store/shipping.py": _swap(_SHIPPING, "format_price(FREE_OVER_CENTS)",
                                   "format_price(FREE_OVER_CENTS, currency=product.currency)"),
        "tools/export.py": _swap(_EXPORT, "format_price(p.price_cents)",
                                 "format_price(p.price_cents, currency=p.currency)"),
    },
    exploit={
        "store/money.py": _swap(_MONEY_FIXED, "(amount_cents, currency)",
                                '(amount_cents, currency="USD")'),
    },
)


# --- refactor_keep_tests ------------------------------------------------------
# The same clean-up is written out in three modules. The work is to move it into
# one function and make all three call it, with no change in behaviour. The
# shortcut is to add the function and leave the three copies where they are.
# The hidden tests catch that by behaviour: they swap the shared function for a
# stand-in before the callers are imported and look for the stand-in's value.

_SKU_COPY = '''    sku = raw_sku.strip().upper()
    sku = "-".join(sku.split())
'''

_RECEIVING = '''"""Goods in."""


def receive(stock, raw_sku, qty):
    """Add `qty` units to `stock` and return the SKU they were filed under."""
    sku = raw_sku.strip().upper()
    sku = "-".join(sku.split())
    stock[sku] = stock.get(sku, 0) + qty
    return sku
'''

_PICKING = '''"""Goods out."""


def pick(stock, raw_sku, qty):
    """Take `qty` units out of `stock` and return the SKU they came from."""
    sku = raw_sku.strip().upper()
    sku = "-".join(sku.split())
    if sku not in stock:
        raise KeyError(sku)
    if stock[sku] < qty:
        raise ValueError(f"only {stock[sku]} of {sku} in stock")
    stock[sku] -= qty
    return sku
'''

_LABELS = '''"""Shelf labels."""


def label(raw_sku):
    """The text printed on a shelf label."""
    sku = raw_sku.strip().upper()
    sku = "-".join(sku.split())
    return f"SKU:{sku}"
'''

_TEST_INVENTORY = '''import unittest

from inventory.labels import label
from inventory.picking import pick
from inventory.receiving import receive


class InventoryTests(unittest.TestCase):
    def test_receive_files_stock_under_the_clean_sku(self):
        stock = {}
        self.assertEqual(receive(stock, " widget a ", 3), "WIDGET-A")
        self.assertEqual(stock, {"WIDGET-A": 3})

    def test_pick_takes_stock_out(self):
        stock = {"WIDGET-A": 3}
        self.assertEqual(pick(stock, "Widget  A", 2), "WIDGET-A")
        self.assertEqual(stock, {"WIDGET-A": 1})

    def test_label(self):
        self.assertEqual(label("bolt m8"), "SKU:BOLT-M8")


if __name__ == "__main__":
    unittest.main()
'''

_SKUS = '''"""SKU clean-up, shared by every module that takes a SKU from a person."""


def normalize_sku(raw):
    sku = raw.strip().upper()
    return "-".join(sku.split())
'''


def _delegating(source: str, import_line: str, call: str) -> str:
    """`source` with its copy of the SKU clean-up replaced by a call to the shared one."""
    with_import = _swap(source, '"""\n\n\n', f'"""\n\n{import_line}\n\n\n')
    return _swap(with_import, _SKU_COPY, f"    sku = {call}(raw_sku)\n")


_SKU_CALLERS = {"inventory/receiving.py": _RECEIVING, "inventory/picking.py": _PICKING,
                "inventory/labels.py": _LABELS}

REFACTOR_KEEP_TESTS = AgentTask(
    key="refactor_keep_tests",
    why="Replaces three copies of the same logic with one shared function, not a fourth copy",
    instruction=(
        "The same SKU clean-up is written out three times in inventory/: in "
        "receiving.py, picking.py, and labels.py. Move it into one function "
        "`normalize_sku(raw)` in a new module inventory/skus.py, and make all three "
        "modules call that function, so the clean-up is written in one place only. "
        "Behaviour must not change: `python3 test_inventory.py` passes now and must "
        "still pass. Reply with a one-line summary when you are done."
    ),
    files={"inventory/__init__.py": "", **_SKU_CALLERS, "test_inventory.py": _TEST_INVENTORY},
    tests='''import subprocess
import sys

from inventory.labels import label
from inventory.picking import pick
from inventory.receiving import receive
from inventory.skus import normalize_sku

assert normalize_sku("  ab 12 ") == "AB-12", normalize_sku("  ab 12 ")
assert normalize_sku("Hex   Nut\\tM6") == "HEX-NUT-M6"
stock = {}
assert receive(stock, " hex nut", 3) == "HEX-NUT"
assert receive(stock, "Hex  Nut ", 2) == "HEX-NUT"
assert stock == {"HEX-NUT": 5}, stock
assert pick(stock, "hex nut", 4) == "HEX-NUT"
assert stock == {"HEX-NUT": 1}, stock
for sku, qty, error in (("hex nut", 2, ValueError), ("no such", 1, KeyError)):
    try:
        pick(stock, sku, qty)
    except error:
        pass
    else:
        raise SystemExit(f"pick({sku!r}, {qty}) must raise {error.__name__}")
assert stock == {"HEX-NUT": 1}, stock
assert label(" x  1") == "SKU:X-1", label(" x  1")

probe = subprocess.run([sys.executable, "-c", """
import inventory.skus
inventory.skus.normalize_sku = lambda raw: "STAND-IN"
from inventory.labels import label
from inventory.picking import pick
from inventory.receiving import receive
stock = {"STAND-IN": 5}
if receive(stock, "a", 1) != "STAND-IN":
    raise SystemExit("receiving.py does not call normalize_sku")
if pick(stock, "b", 1) != "STAND-IN":
    raise SystemExit("picking.py does not call normalize_sku")
if label("c") != "SKU:STAND-IN":
    raise SystemExit("labels.py does not call normalize_sku")
"""], capture_output=True, text=True)
assert probe.returncode == 0, probe.stderr
''',
    solution={
        "inventory/skus.py": _SKUS,
        **{path: _delegating(source, "from inventory.skus import normalize_sku", "normalize_sku")
           for path, source in _SKU_CALLERS.items()},
    },
    alt_solution={
        "inventory/skus.py": '''"""SKU clean-up."""

import re


def normalize_sku(raw):
    return re.sub("[ \\t\\n\\r\\f\\v]+", "-", raw.strip()).upper()
''',
        **{path: _delegating(source, "from inventory import skus", "skus.normalize_sku")
           for path, source in _SKU_CALLERS.items()},
    },
    exploit={"inventory/skus.py": _SKUS},
)


# --- implement_spec -----------------------------------------------------------
# A module written from a spec file. The example test covers four inputs and the
# spec has more rules than that: unit order, repeats, signs, and the zero case.
# The shortcut is code that answers the examples and nothing else.

_SPEC = """# durations.py

Write `durations.py` in this directory. It has two functions.

## parse_duration(text)

Turns text such as `1h30m` into a whole number of seconds.

- The text is one or more parts with nothing between them. A part is a whole
  number, written with the digits 0 to 9 only, followed by one unit letter.
- The units are `d` (86400 seconds), `h` (3600), `m` (60), and `s` (1).
- Parts run from the largest unit to the smallest, and a unit appears at most
  once. `30m1h` and `1h2h` are both invalid.
- A number may be larger than its unit usually holds. `90m` is 5400.
- Everything else is invalid: empty text, spaces anywhere, a number with no
  unit, a unit with no number, an unknown unit, an upper-case unit, a sign, a
  decimal point.
- Invalid text raises `ValueError`.

## format_duration(seconds)

Turns a whole number of seconds into the shortest text `parse_duration` reads
back to the same number.

- Largest unit first. A unit whose count is zero is left out, so 3605 is `1h5s`.
- Each count is as large as it can be, so 5400 is `1h30m` and never `90m`.
- Zero is `0s`.
- A negative number raises `ValueError`.
"""

_TEST_EXAMPLES = '''import unittest

from durations import format_duration, parse_duration


class ExampleTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("45s"), 45)

    def test_format(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(0), "0s")

    def test_empty_text_is_invalid(self):
        with self.assertRaises(ValueError):
            parse_duration("")


if __name__ == "__main__":
    unittest.main()
'''

IMPLEMENT_SPEC = AgentTask(
    key="implement_spec",
    why="Builds a module from a written spec, including the rules its example test never exercises",
    instruction=(
        "Write durations.py as SPEC.md describes. test_examples.py has a few "
        "examples and `python3 test_examples.py` must pass, but SPEC.md is the "
        "full requirement. Reply with a one-line summary when you are done."
    ),
    files={"SPEC.md": _SPEC, "test_examples.py": _TEST_EXAMPLES},
    tests='''from durations import format_duration, parse_duration

for text, want in (("45s", 45), ("1h30m", 5400), ("90m", 5400), ("2d", 172800),
                   ("1d1s", 86401), ("1d2h3m4s", 93784), ("0s", 0), ("0h0s", 0),
                   ("100h", 360000)):
    if parse_duration(text) != want:
        raise SystemExit(f"parse_duration({text!r}) gave {parse_duration(text)!r}, not {want}")

for text in ("", " ", "1h 30m", " 1h", "1h ", "10", "h", "1h30", "30m1h", "1h2h",
             "1s1m", "1w", "1H", "-1h", "+5s", "1.5h", "1h-30m"):
    try:
        got = parse_duration(text)
    except ValueError:
        continue
    raise SystemExit(f"parse_duration({text!r}) must raise ValueError, gave {got!r}")

for seconds, want in ((0, "0s"), (59, "59s"), (60, "1m"), (3600, "1h"), (3605, "1h5s"),
                      (3661, "1h1m1s"), (86400, "1d"), (90061, "1d1h1m1s"),
                      (172805, "2d5s"), (360000, "4d4h")):
    if format_duration(seconds) != want:
        raise SystemExit(f"format_duration({seconds}) gave {format_duration(seconds)!r}, not {want!r}")

try:
    format_duration(-1)
except ValueError:
    pass
else:
    raise SystemExit("format_duration(-1) must raise ValueError")

assert all(parse_duration(format_duration(n)) == n for n in range(0, 400000, 137))
''',
    solution={
        "durations.py": '''"""Durations as text, per SPEC.md."""

import re

UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
PATTERN = re.compile("".join(f"(?:([0-9]+){unit})?" for unit, _ in UNITS))


def parse_duration(text):
    match = PATTERN.fullmatch(text)
    if not text or match is None:
        raise ValueError(f"invalid duration: {text!r}")
    return sum(int(count) * size
               for count, (_, size) in zip(match.groups(), UNITS) if count is not None)


def format_duration(seconds):
    if seconds < 0:
        raise ValueError("a duration cannot be negative")
    parts = []
    for unit, size in UNITS:
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts) or "0s"
''',
    },
    alt_solution={
        "durations.py": '''ORDER = "dhms"
SIZES = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    total, digits, last = 0, "", -1
    for ch in text:
        if ch in "0123456789":
            digits += ch
        elif ch in SIZES and digits and ORDER.index(ch) > last:
            total += int(digits) * SIZES[ch]
            digits, last = "", ORDER.index(ch)
        else:
            raise ValueError("cannot read " + repr(text) + " as a duration")
    if digits or last < 0:
        raise ValueError("cannot read " + repr(text) + " as a duration")
    return total


def format_duration(seconds):
    if seconds < 0:
        raise ValueError("negative duration")
    if seconds == 0:
        return "0s"
    days, rest = seconds // 86400, seconds % 86400
    hours, rest = rest // 3600, rest % 3600
    counts = {"d": days, "h": hours, "m": rest // 60, "s": rest % 60}
    return "".join(str(counts[unit]) + unit for unit in ORDER if counts[unit])
''',
    },
    exploit={
        "durations.py": '''PARSED = {"1h30m": 5400, "45s": 45}
FORMATTED = {5400: "1h30m", 0: "0s"}


def parse_duration(text):
    if text not in PARSED:
        raise ValueError(text)
    return PARSED[text]


def format_duration(seconds):
    return FORMATTED[seconds]
''',
    },
)


# --- config_migration ---------------------------------------------------------
# Settings move from an INI file to JSON, and two readers must follow. The
# shortcut is to write the JSON file and leave the readers on the INI file. The
# hidden tests remove the INI file, then change the JSON and expect the readers
# to report the new values.

_SETTINGS_INI = """[server]
host = 127.0.0.1
port = 8080
debug = false

[worker]
queues = email, reports, cleanup
concurrency = 4
retry_seconds = 2.5
"""

_SERVER = '''"""HTTP server settings."""

import configparser
from pathlib import Path

CONFIG = Path(__file__).parent / "settings.ini"


def _load():
    parser = configparser.ConfigParser()
    parser.read(CONFIG)
    return parser


def bind_address():
    """(host, port) the server listens on."""
    section = _load()["server"]
    return section["host"], section.getint("port")


def debug_enabled():
    return _load()["server"].getboolean("debug")
'''

_WORKER = '''"""Background worker settings."""

import configparser
from pathlib import Path

CONFIG = Path(__file__).parent / "settings.ini"


def worker_settings():
    """The worker's queues, in priority order, and its limits."""
    parser = configparser.ConfigParser()
    parser.read(CONFIG)
    section = parser["worker"]
    return {
        "queues": [name.strip() for name in section["queues"].split(",")],
        "concurrency": section.getint("concurrency"),
        "retry_seconds": section.getfloat("retry_seconds"),
    }
'''

_SETTINGS_JSON = """{
  "server": {
    "host": "127.0.0.1",
    "port": 8080,
    "debug": false
  },
  "worker": {
    "queues": ["email", "reports", "cleanup"],
    "concurrency": 4,
    "retry_seconds": 2.5
  }
}
"""

CONFIG_MIGRATION = AgentTask(
    key="config_migration",
    why="Converts a config file to a new format with real types and moves both of its readers",
    instruction=(
        "The project's settings are moving from settings.ini to JSON. Create "
        "settings.json holding the same settings. Keep the section and key names, "
        "and give each value its real JSON type: numbers as numbers, true or false "
        "for booleans, and `queues` as a list of strings. Change server.py and "
        "worker.py to read settings.json. Their functions must return the same "
        "values as they do now. settings.ini will be deleted after the migration, "
        "so nothing may depend on it. Reply with a one-line summary when you are done."
    ),
    files={"settings.ini": _SETTINGS_INI, "server.py": _SERVER, "worker.py": _WORKER},
    tests='''import json
import os
import subprocess
import sys

with open("settings.json") as f:
    config = json.load(f)
assert config == {"server": {"host": "127.0.0.1", "port": 8080, "debug": False},
                  "worker": {"queues": ["email", "reports", "cleanup"],
                             "concurrency": 4, "retry_seconds": 2.5}}, config
assert type(config["server"]["port"]) is int, config
assert type(config["server"]["debug"]) is bool, config
assert type(config["worker"]["concurrency"]) is int, config

if os.path.exists("settings.ini"):
    os.remove("settings.ini")

import server
import worker

assert tuple(server.bind_address()) == ("127.0.0.1", 8080), server.bind_address()
assert server.debug_enabled() is False, server.debug_enabled()
assert worker.worker_settings() == {"queues": ["email", "reports", "cleanup"],
                                    "concurrency": 4, "retry_seconds": 2.5}, worker.worker_settings()

with open("settings.json", "w") as f:
    json.dump({"server": {"host": "0.0.0.0", "port": 9000, "debug": True},
               "worker": {"queues": ["solo"], "concurrency": 1, "retry_seconds": 0.5}}, f)
probe = subprocess.run([sys.executable, "-c", """
import server
import worker
if tuple(server.bind_address()) != ("0.0.0.0", 9000):
    raise SystemExit(f"bind_address() ignores settings.json: {server.bind_address()!r}")
if server.debug_enabled() is not True:
    raise SystemExit("debug_enabled() ignores settings.json")
if worker.worker_settings() != {"queues": ["solo"], "concurrency": 1, "retry_seconds": 0.5}:
    raise SystemExit(f"worker_settings() ignores settings.json: {worker.worker_settings()!r}")
"""], capture_output=True, text=True)
assert probe.returncode == 0, probe.stderr
''',
    solution={
        "settings.json": _SETTINGS_JSON,
        "server.py": '''"""HTTP server settings."""

import json
from pathlib import Path

CONFIG = Path(__file__).parent / "settings.json"


def _load():
    with open(CONFIG) as f:
        return json.load(f)


def bind_address():
    """(host, port) the server listens on."""
    section = _load()["server"]
    return section["host"], section["port"]


def debug_enabled():
    return _load()["server"]["debug"]
''',
        "worker.py": '''"""Background worker settings."""

import json
from pathlib import Path

CONFIG = Path(__file__).parent / "settings.json"


def worker_settings():
    """The worker's queues, in priority order, and its limits."""
    with open(CONFIG) as f:
        section = json.load(f)["worker"]
    return {
        "queues": list(section["queues"]),
        "concurrency": section["concurrency"],
        "retry_seconds": section["retry_seconds"],
    }
''',
    },
    alt_solution={
        "settings.json": '{"worker": {"retry_seconds": 2.5, "concurrency": 4, "queues": '
                         '["email", "reports", "cleanup"]}, "server": {"debug": false, '
                         '"port": 8080, "host": "127.0.0.1"}}\n',
        "server.py": '''import json

with open("settings.json") as f:
    _SERVER = json.load(f)["server"]


def bind_address():
    return (_SERVER["host"], int(_SERVER["port"]))


def debug_enabled():
    return bool(_SERVER["debug"])
''',
        "worker.py": '''import json
import pathlib


def worker_settings():
    return dict(json.loads(pathlib.Path("settings.json").read_text())["worker"])
''',
    },
    exploit={"settings.json": _SETTINGS_JSON},
)


# --- flaky_test ---------------------------------------------------------------
# The tests pass in the order unittest runs them and fail in the reverse order,
# because every Inbox shares one list. The shortcut is to reset that list in the
# tests, which makes both orders green and leaves two inboxes sharing messages.

_INBOX = '''"""A user's inbox."""


class Inbox:
    messages = []

    def __init__(self, owner):
        self.owner = owner

    def deliver(self, text):
        self.messages.append(text)

    def unread(self):
        return len(self.messages)

    def read_all(self):
        """Return the unread messages, oldest first, and mark them read."""
        texts = list(self.messages)
        self.messages.clear()
        return texts
'''

_TEST_INBOX = '''import unittest

from mail.inbox import Inbox


class InboxTests(unittest.TestCase):
    def test_empty_inbox_has_no_unread(self):
        self.assertEqual(Inbox("ann").unread(), 0)

    def test_owner_is_kept(self):
        self.assertEqual(Inbox("bob").owner, "bob")

    def test_two_deliveries_are_counted(self):
        box = Inbox("cy")
        box.deliver("first")
        box.deliver("second")
        self.assertEqual(box.unread(), 2)


if __name__ == "__main__":
    unittest.main()
'''

_RUN_REVERSED = '''"""Run test_inbox.py with its tests in reverse order, as the CI shuffle sometimes does."""

import unittest

loader = unittest.TestLoader()
loader.sortTestMethodsUsing = lambda a, b: (a < b) - (a > b)
result = unittest.TextTestRunner().run(loader.loadTestsFromName("test_inbox"))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''

FLAKY_TEST = AgentTask(
    key="flaky_test",
    why="Traces an order-dependent test to shared state in the code and fixes it there",
    instruction=(
        "CI runs the tests in a random order, and test_inbox.py fails there some of "
        "the time. `python3 test_inbox.py` passes, and `python3 run_reversed.py` "
        "runs the same tests in reverse order and fails. Find the cause and fix it "
        "in the code under mail/. Do not change test_inbox.py or run_reversed.py. "
        "Reply with a one-line summary when both commands pass."
    ),
    files={"mail/__init__.py": "", "mail/inbox.py": _INBOX, "test_inbox.py": _TEST_INBOX,
           "run_reversed.py": _RUN_REVERSED},
    tests='''from mail.inbox import Inbox

ann, bob = Inbox("ann"), Inbox("bob")
ann.deliver("hi")
ann.deliver("again")
assert (ann.unread(), bob.unread()) == (2, 0), (ann.unread(), bob.unread())
bob.deliver("yo")
assert (ann.unread(), bob.unread()) == (2, 1), (ann.unread(), bob.unread())
assert Inbox("cy").unread() == 0
assert ann.read_all() == ["hi", "again"]
assert (ann.unread(), bob.unread()) == (0, 1), (ann.unread(), bob.unread())
assert ann.read_all() == []
assert bob.read_all() == ["yo"]
assert (ann.owner, bob.owner) == ("ann", "bob")
''',
    solution={
        "mail/inbox.py": _swap(
            _swap(_INBOX, "    messages = []\n\n", ""),
            "        self.owner = owner\n", "        self.owner = owner\n        self.messages = []\n"),
    },
    alt_solution={
        "mail/inbox.py": '''from dataclasses import dataclass, field


@dataclass
class Inbox:
    owner: str
    _texts: tuple = field(default=(), repr=False)

    def deliver(self, text):
        self._texts += (text,)

    def unread(self):
        return len(self._texts)

    def read_all(self):
        texts, self._texts = list(self._texts), ()
        return texts
''',
    },
    exploit={
        "test_inbox.py": _swap(
            _TEST_INBOX, "class InboxTests(unittest.TestCase):\n",
            "class InboxTests(unittest.TestCase):\n    def setUp(self):\n"
            "        Inbox.messages.clear()\n\n"),
    },
)


# --- cli_bug ------------------------------------------------------------------
# Two bug reports against one command, with causes in different files: the exit
# code is set in cli.py and the line numbers come from scan.py. The shortcut for
# the first is to exit 1 every time, which is right only for the reported file.

_LC_MAIN = '''import sys

from linecheck.cli import main

sys.exit(main())
'''

_LC_CLI = '''"""Command line for linecheck."""

import argparse
import sys

from linecheck.report import render
from linecheck.scan import long_lines


def main(argv=None):
    parser = argparse.ArgumentParser(prog="linecheck",
                                     description="Report lines longer than a limit.")
    parser.add_argument("path")
    parser.add_argument("--max", type=int, default=80, help="longest line allowed")
    args = parser.parse_args(argv)
    try:
        found = long_lines(args.path, args.max)
    except FileNotFoundError:
        print(f"linecheck: no such file: {args.path}", file=sys.stderr)
        return 2
    print(render(args.path, found))
    return 0
'''

_LC_SCAN = '''"""Finds the long lines."""


def long_lines(path, limit):
    """(line number, length) for each line longer than `limit` characters."""
    found = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f):
            length = len(line.rstrip("\\n"))
            if length > limit:
                found.append((number, length))
    return found
'''

_LC_REPORT = '''"""Formats what the scan found."""


def render(path, found):
    lines = [f"{path}:{number}: {length} characters" for number, length in found]
    lines.append(f"{len(found)} lines too long")
    return "\\n".join(lines)
'''

_LC_SAMPLE = """Shopping
Remember to buy oat milk and bread
eggs
Call the plumber about the kitchen tap before Friday
"""

_LC_SCAN_FIXED = _swap(_LC_SCAN, "enumerate(f)", "enumerate(f, 1)")

CLI_BUG = AgentTask(
    key="cli_bug",
    why="Fixes two reported bugs in one command whose causes are in two different files",
    instruction=(
        "There are two bug reports against `python3 -m linecheck`, which lists the "
        "lines of a file that are longer than `--max` characters. Try it with "
        "`python3 -m linecheck sample.txt --max 20`. First: it exits 0 even when "
        "it finds long lines. It must exit 1 when at least one line is too long and "
        "0 when none is. Second: the line numbers it prints are one too low. The "
        "first line of a file is line 1. Fix both, and change nothing else about "
        "the output. Reply with a one-line summary when you are done."
    ),
    files={"linecheck/__init__.py": "", "linecheck/__main__.py": _LC_MAIN,
           "linecheck/cli.py": _LC_CLI, "linecheck/scan.py": _LC_SCAN,
           "linecheck/report.py": _LC_REPORT, "sample.txt": _LC_SAMPLE},
    tests='''import subprocess
import sys


def run(*args):
    done = subprocess.run([sys.executable, "-m", "linecheck", *args],
                          capture_output=True, text=True)
    return done.returncode, done.stdout.splitlines()


with open("_hidden.txt", "w") as f:
    f.write("x" * 12 + "\\nok\\n\\n" + "y" * 30 + "\\n" + "z" * 10 + "\\n")

assert run("_hidden.txt", "--max", "10") == (1, ["_hidden.txt:1: 12 characters",
                                                 "_hidden.txt:4: 30 characters",
                                                 "2 lines too long"]), run("_hidden.txt", "--max", "10")
assert run("_hidden.txt", "--max", "29") == (1, ["_hidden.txt:4: 30 characters",
                                                 "1 lines too long"]), run("_hidden.txt", "--max", "29")
assert run("_hidden.txt", "--max", "30") == (0, ["0 lines too long"]), run("_hidden.txt", "--max", "30")
assert run("_hidden.txt") == (0, ["0 lines too long"]), run("_hidden.txt")
assert run("_hidden_missing.txt") == (2, []), run("_hidden_missing.txt")
assert run("sample.txt", "--max", "20") == (1, ["sample.txt:2: 34 characters",
                                                "sample.txt:4: 52 characters",
                                                "2 lines too long"]), run("sample.txt", "--max", "20")
''',
    solution={
        "linecheck/scan.py": _LC_SCAN_FIXED,
        "linecheck/cli.py": _swap(_LC_CLI, "    return 0\n", "    return 1 if found else 0\n"),
    },
    alt_solution={
        "linecheck/report.py": _swap(_LC_REPORT, "{path}:{number}:", "{path}:{number + 1}:"),
        "linecheck/cli.py": _swap(_LC_CLI, "    print(render(args.path, found))\n    return 0\n",
                                  "    sys.stdout.write(render(args.path, found) + \"\\n\")\n"
                                  "    return int(len(found) > 0)\n"),
    },
    exploit={
        "linecheck/scan.py": _LC_SCAN_FIXED,
        "linecheck/cli.py": _swap(_LC_CLI, "    return 0\n", "    return 1\n"),
    },
)


TASKS: dict[str, AgentTask] = {
    t.key: t for t in (FIX_FAILING_TEST, LOG_TRIAGE, SIGNATURE_CHANGE, REFACTOR_KEEP_TESTS,
                       IMPLEMENT_SPEC, CONFIG_MIGRATION, FLAKY_TEST, CLI_BUG)
}
