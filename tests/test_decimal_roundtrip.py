"""Exact-decimal round-trips through the SDK.

FEEL numbers are exact decimals server-side. The literals below are
unrepresentable as IEEE-754 doubles, so any hop that narrows through float
changes the digits and fails these assertions.
"""

import decimal
import json

import simplejson

from quantumbpm.api_client import ApiClient
from quantumbpm.models.feel_value import FeelValue
from quantumbpm.variables import Vars

EXACT = "1234567890.123456789012345678"


def test_feel_value_from_json_parses_decimal():
    fv = FeelValue.from_json(EXACT)
    assert isinstance(fv.actual_instance, decimal.Decimal)
    assert fv.actual_instance == decimal.Decimal(EXACT)


def test_feel_value_nested_context_keeps_decimals():
    fv = FeelValue.from_json('{"amount": ' + EXACT + ', "items": [0.1, 0.2]}')
    ctx = fv.actual_instance
    assert ctx["amount"] == decimal.Decimal(EXACT)
    # The classic float trap: exact decimals actually add up.
    assert ctx["items"][0] + ctx["items"][1] == decimal.Decimal("0.3")


def test_feel_value_to_json_emits_exact_number():
    fv = FeelValue(decimal.Decimal(EXACT))
    assert fv.to_json() == EXACT


def test_feel_value_from_dict_accepts_decimal():
    fv = FeelValue.from_dict({"amount": decimal.Decimal(EXACT)})
    assert fv.actual_instance["amount"] == decimal.Decimal(EXACT)


def test_request_body_serializes_decimal_as_exact_number():
    client = ApiClient()
    sanitized = client.sanitize_for_serialization({"amount": decimal.Decimal(EXACT)})
    assert isinstance(sanitized["amount"], decimal.Decimal), (
        "sanitize must keep Decimal intact for the REST layer, "
        "not stringify or narrow it"
    )
    body = simplejson.dumps(sanitized, use_decimal=True)
    assert body == '{"amount": ' + EXACT + "}"
    # And the wire bytes parse back to the same exact value.
    parsed = json.loads(body, parse_float=decimal.Decimal)
    assert parsed["amount"] == decimal.Decimal(EXACT)


def test_vars_to_wire_map_keeps_decimal():
    # The outbound BPMN path (start_instance / complete_job) goes through
    # Vars.to_wire_map, not sanitize_for_serialization. Its JSON round-trip
    # must preserve Decimal rather than narrowing or rejecting it.
    wire = Vars().set("amount", decimal.Decimal(EXACT)).to_wire_map()
    assert wire["amount"] == decimal.Decimal(EXACT)
    assert isinstance(wire["amount"], decimal.Decimal)


def test_vars_to_feel_context_keeps_decimal():
    ctx = Vars().set("amount", decimal.Decimal(EXACT)).to_feel_context()
    assert ctx["amount"] == decimal.Decimal(EXACT)
    assert isinstance(ctx["amount"], decimal.Decimal)
