"""
Vars holds a set of named variables. The same type is used for DMN
evaluation contexts, BPMN process variables, and external job payloads.

Vars is a thin wrapper around ``dict[str, Any]`` with helpers for typed
access, chainable construction, and conversion to/from the wire shapes the
generated client uses.
"""

from __future__ import annotations

import decimal
import json
from typing import Any, Mapping, TypeVar, Type, cast

import simplejson
from pydantic import BaseModel, TypeAdapter

T = TypeVar("T")


class Vars:
    """A mapping of variable names to values, with typed accessors."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(data) if data else {}

    @classmethod
    def of(cls) -> "Vars":
        """Convenience for chained ``.set(...)`` calls."""
        return cls()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Vars":
        """Build a Vars from a plain dict. ``None`` produces an empty Vars."""
        return cls(data)

    def set(self, name: str, value: Any) -> "Vars":
        """Assign ``name=value`` and return ``self`` for chaining."""
        self._data[name] = value
        return self

    def lookup(self, name: str, default: Any = None) -> Any:
        """Return the raw value at ``name``, or ``default`` when not set."""
        return self._data.get(name, default)

    def get(self, name: str, type_: Type[T] | None = None) -> T:
        """
        Return the value at ``name`` decoded as ``type_``.

        Without ``type_`` the raw value is returned. With a Pydantic model or
        a typed dataclass, the value is JSON round-tripped and validated. With
        a plain Python type (``int``, ``str``, ``list``, ``dict``), the value
        is cast.
        """
        if name not in self._data:
            raise KeyError(f"variable '{name}' not set")
        raw = self._data[name]
        if type_ is None:
            return cast(T, raw)
        return _coerce(raw, type_)

    def as_type(self, type_: Type[T]) -> T:
        """Decode the entire Vars into ``type_`` (typically a Pydantic model)."""
        return _coerce(self._data, type_)

    def to_dict(self) -> dict[str, Any]:
        """Return a shallow copy of the underlying dict."""
        return dict(self._data)

    def to_feel_context(self) -> dict[str, Any]:
        """Convert to the FeelContext shape the DMN evaluate endpoints accept."""
        return _json_round_trip(self._data)

    def to_wire_map(self) -> dict[str, Any] | None:
        """
        Convert to the optional ``variables`` map BPMN endpoints accept.
        Returns ``None`` for an empty Vars so the optional field can be
        omitted from the request body.
        """
        if not self._data:
            return None
        return _json_round_trip(self._data)

    @classmethod
    def from_wire_map(cls, data: Mapping[str, Any] | None) -> "Vars":
        """Lift a wire-shape variables map into a Vars value."""
        if not data:
            return cls()
        return cls(data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, name: object) -> bool:
        return name in self._data

    def __iter__(self):
        return iter(self._data)

    def __getitem__(self, name: str) -> Any:
        return self._data[name]

    def __repr__(self) -> str:
        return f"Vars({self._data!r})"


def _coerce(value: Any, type_: Type[T]) -> T:
    if isinstance(type_, type) and issubclass(type_, BaseModel):
        return cast(T, type_.model_validate(value))
    # TypeAdapter handles primitives, dataclasses, list/dict generics, etc.
    return TypeAdapter(type_).validate_python(value)


def _json_round_trip(data: Mapping[str, Any]) -> dict[str, Any]:
    """Round-trip through JSON to normalize Pydantic models, dates, etc.

    FEEL numbers are exact decimals: serialize Decimal as an exact JSON number
    (``use_decimal=True``) and parse it back into Decimal (``parse_float``) so
    the value never narrows to a binary float on the outbound path.
    """
    encoded = simplejson.dumps(data, default=_json_default, use_decimal=True)
    return cast(dict[str, Any], json.loads(encoded, parse_float=decimal.Decimal))


def _json_default(o: Any) -> Any:
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
