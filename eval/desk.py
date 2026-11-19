"""
A simulated order desk for run-agent.py's desk tasks.

The shop is a dict of customers and orders held in memory. The model is given
the tools below and a written policy (prompts/desk-system.md). The tools read
and change the records, and they do NOT enforce the policy: a model that cancels
a shipped order because the customer asked has changed the data. Whether the
records end up right is the whole test, so a tool that refused would be doing the
model's job for it.

Every tool returns text for the model. A call about a record that does not exist
is answered with an error line and changes nothing. A call that is not usable at
all (unknown tool, missing or wrong-typed argument) is answered the same way and
also reported as malformed, which the runner counts.
"""

from __future__ import annotations

import copy
import json

SHOP: dict = {
    "customers": {
        "C1": {"name": "Ana Ruiz", "email": "ana@example.org"},
        "C2": {"name": "Ben Okoye", "email": "ben@example.org"},
        "C3": {"name": "Chloe Tan", "email": "chloe@example.org"},
    },
    "orders": {
        "A101": {"customer": "C1", "item": "blue kettle", "status": "pending",
                 "address": "12 Elm St, Leeds", "paid_cents": 4500, "refunded_cents": 0},
        "A102": {"customer": "C1", "item": "desk lamp", "status": "shipped",
                 "address": "12 Elm St, Leeds", "paid_cents": 3200, "refunded_cents": 0},
        "A103": {"customer": "C1", "item": "wool blanket", "status": "delivered",
                 "address": "12 Elm St, Leeds", "paid_cents": 5900, "refunded_cents": 0},
        "B201": {"customer": "C2", "item": "blue kettle", "status": "pending",
                 "address": "9 Oak Ave, York", "paid_cents": 4500, "refunded_cents": 0},
        "B202": {"customer": "C2", "item": "espresso cups", "status": "delivered",
                 "address": "9 Oak Ave, York", "paid_cents": 2800, "refunded_cents": 1000},
        "C301": {"customer": "C3", "item": "desk lamp", "status": "pending",
                 "address": "3 Mill Lane, Hull", "paid_cents": 3200, "refunded_cents": 0},
        "C302": {"customer": "C3", "item": "desk lamp", "status": "pending",
                 "address": "77 Quay Rd, Hull", "paid_cents": 3200, "refunded_cents": 0},
    },
}


def new_state() -> dict:
    """A fresh copy of the shop for one attempt."""
    return copy.deepcopy(SHOP)


def patched(state: dict, changes: dict[str, dict]) -> dict:
    """A copy of `state` with `changes` ({order id: {field: value}}) applied.

    Raises KeyError for an order or field that does not exist, so a task cannot
    expect a change to something the shop does not have.
    """
    out = copy.deepcopy(state)
    for order_id, fields in changes.items():
        order = out["orders"][order_id]
        for field, value in fields.items():
            if field not in order:
                raise KeyError(f"{order_id} has no field {field!r}")
            order[field] = value
    return out


def _plain(value):
    """Text compared without case, repeated spaces, or closing punctuation, so an
    address typed as '40 pine rd, leeds.' matches '40 Pine Rd, Leeds'."""
    if isinstance(value, str):
        return " ".join(value.casefold().split()).rstrip(" .,")
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def same_state(a: dict, b: dict) -> bool:
    return _plain(a) == _plain(b)


# --- tools ----------------------------------------------------------------------

def _find_customer(state: dict, email: str) -> str:
    wanted = email.strip().casefold()
    for customer_id, customer in state["customers"].items():
        if customer["email"].casefold() == wanted:
            return f"customer {customer_id}: {customer['name']}, {customer['email']}"
    return f"error: no customer with the email {email!r}"


def _list_orders(state: dict, customer_id: str) -> str:
    if customer_id not in state["customers"]:
        return f"error: no customer {customer_id!r}"
    lines = [f"{order_id}: {order['item']}, {order['status']}"
             for order_id, order in state["orders"].items() if order["customer"] == customer_id]
    return "\n".join(lines) or "this customer has no orders"


def _get_order(state: dict, order_id: str) -> str:
    if order_id not in state["orders"]:
        return f"error: no order {order_id!r}"
    return json.dumps({"order_id": order_id, **state["orders"][order_id]})


def _cancel_order(state: dict, order_id: str) -> str:
    if order_id not in state["orders"]:
        return f"error: no order {order_id!r}"
    state["orders"][order_id]["status"] = "cancelled"
    return f"order {order_id} is now cancelled"


def _update_address(state: dict, order_id: str, address: str) -> str:
    if order_id not in state["orders"]:
        return f"error: no order {order_id!r}"
    state["orders"][order_id]["address"] = address.strip()
    return f"order {order_id} will be delivered to {address.strip()}"


def _refund_order(state: dict, order_id: str, amount_cents: int) -> str:
    if order_id not in state["orders"]:
        return f"error: no order {order_id!r}"
    order = state["orders"][order_id]
    order["refunded_cents"] += amount_cents
    return f"refunded {amount_cents} cents on order {order_id}, {order['refunded_cents']} in total"


def _tool(name: str, description: str, **properties: dict) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(properties)}}}


_ORDER_ID = {"type": "string", "description": "An order id, as list_orders shows it."}

TOOLS: list[dict] = [
    _tool("find_customer", "Look up a customer by email address.",
          email={"type": "string", "description": "The customer's email address."}),
    _tool("list_orders", "List a customer's orders with each order's id, item, and status.",
          customer_id={"type": "string", "description": "A customer id, as find_customer shows it."}),
    _tool("get_order", "Show every detail of one order.", order_id=_ORDER_ID),
    _tool("cancel_order", "Cancel an order.", order_id=_ORDER_ID),
    _tool("update_address", "Change the delivery address of an order.", order_id=_ORDER_ID,
          address={"type": "string", "description": "The full new delivery address."}),
    _tool("refund_order", "Refund money on an order.", order_id=_ORDER_ID,
          amount_cents={"type": "integer", "description": "The amount to refund, in cents."}),
]

HANDLERS = {"find_customer": _find_customer, "list_orders": _list_orders,
            "get_order": _get_order, "cancel_order": _cancel_order,
            "update_address": _update_address, "refund_order": _refund_order}


def _usable(spec: dict, value) -> bool:
    if spec["type"] == "integer":
        return type(value) is int and value > 0
    return isinstance(value, str) and bool(value.strip())


def dispatch(state: dict, call: dict) -> tuple[str, bool]:
    """Run one parsed tool call against `state`.

    Returns (text for the model, whether the call was well formed). Never
    raises: whatever a model sends comes back as an error line.
    """
    name = call.get("name")
    if name not in HANDLERS:
        return (f"error: unknown tool {name!r}. The tools are: {', '.join(HANDLERS)}."), False
    arguments = call.get("arguments")
    if not call.get("arguments_ok") or not isinstance(arguments, dict):
        return "error: the arguments must be a JSON object. Nothing was changed.", False
    properties = next(t for t in TOOLS if t["function"]["name"] == name
                      )["function"]["parameters"]["properties"]
    for key, spec in properties.items():
        if key not in arguments or not _usable(spec, arguments[key]):
            kind = "a whole number above zero" if spec["type"] == "integer" else "non-empty text"
            return f"error: {name} needs {key!r} as {kind}. Nothing was changed.", False
    return HANDLERS[name](state, **{key: arguments[key] for key in properties}), True
