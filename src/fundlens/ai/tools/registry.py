"""The tool registry's shape and its two hard invariants.

**Read-only.** Every callable here reads; none writes. This is enforced by
construction (`compute/service.py`'s lot-rebuilding functions are simply
not registered) and pinned by a test, because the failure mode is severe:
an LLM that can mutate the ledger it is reasoning about could silently
corrupt real financial history while sounding confident about it.

**Every result must be JSON-serialisable and flat enough to cite.** Tool
results become part of the evidence the determinism validator resolves
citation paths against (`ai/validator.py`), so a result that can't be
walked as nested dicts/lists is a number the model could state but never
prove. Adapters therefore return plain dicts of primitives, converting
Decimal to str and dates to ISO strings the way the API response models
already do -- not the raw compute dataclasses.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

ToolFn = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # JSON Schema for the arguments, in the shape the Messages API's
    # `input_schema` expects.
    input_schema: dict[str, Any]
    fn: Callable[..., Mapping[str, Any]]

    def to_api_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolError(Exception):
    """The tool could not produce a result — an unknown goal, no risk
    profile, a fund with no history. Reported back to the model as a tool
    result so it can adapt, never raised out of the loop: a model that
    guessed a bad argument should get a chance to correct it."""


def object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def registry(tools: list[Tool]) -> dict[str, Tool]:
    by_name: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in by_name:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        by_name[tool.name] = tool
    return by_name


def call_tool(
    tools: Mapping[str, Tool], session: Session, name: str, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Dispatch with the argument validation the API's own schema
    enforcement doesn't guarantee: unknown tool names and unexpected
    argument keys are rejected here rather than reaching a compute function
    as a surprise keyword."""
    tool = tools.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool: {name!r}")

    allowed = set(tool.input_schema.get("properties", {}))
    unexpected = set(arguments) - allowed
    if unexpected:
        raise ToolError(f"Unexpected argument(s) for {name!r}: {sorted(unexpected)}")

    missing = set(tool.input_schema.get("required", [])) - set(arguments)
    if missing:
        raise ToolError(f"Missing required argument(s) for {name!r}: {sorted(missing)}")

    return tool.fn(session, **arguments)
