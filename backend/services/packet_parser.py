"""
packet_parser.py - Packet format, parser and validation (Phase 3, Task 3).

Wire format: one JSON object per line, UTF-8, newline-terminated.

    {"v":1,"src":"WATER_01","type":"data","seq":1042,"d":{"flow_in":15.4}}

    v     protocol version (must be 1)
    src   node_id of the sender, e.g. WATER_01
    type  "heartbeat", "data" or "event"
    seq   per-node sequence number (used for replay detection in Phase 8)
    d     flat object of numbers, booleans and strings; required for
          "data" and "event", optional for "heartbeat"
    sig   optional signature, reserved for Phase 8 and ignored for now

This module validates the envelope only. What the payload fields mean for
water, power and security is checked in the later phases that use them.

    packet = parse_packet(line)   # raises PacketError if the line is invalid
"""

import json
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.schemas.device import NODE_ID_PATTERN

PROTOCOL_VERSION = 1
MAX_PACKET_CHARS = 1024
MAX_SEQUENCE = 4_294_967_295
MAX_PAYLOAD_KEYS = 32
MAX_KEY_LENGTH = 40
MAX_TEXT_VALUE_LENGTH = 200
MAX_SIGNATURE_LENGTH = 128

# Tried left to right so True stays bool, 3 stays int and 1.5 stays float.
PayloadValue = Annotated[
    StrictBool | StrictInt | StrictFloat | StrictStr,
    Field(union_mode="left_to_right"),
]


class PacketType(StrEnum):
    """The kinds of packet a node can send."""

    HEARTBEAT = "heartbeat"
    DATA = "data"
    EVENT = "event"


class PacketError(ValueError):
    """Raised when a line is not a valid packet."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class Packet(BaseModel):
    """A validated packet. Built from the wire field names (v, src, type, seq, d, sig)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: StrictInt = Field(alias="v")
    source: str = Field(alias="src", pattern=NODE_ID_PATTERN)
    packet_type: PacketType = Field(alias="type")
    sequence: StrictInt = Field(alias="seq", ge=0, le=MAX_SEQUENCE)
    payload: dict[str, PayloadValue] = Field(default_factory=dict, alias="d")
    signature: str | None = Field(default=None, alias="sig", max_length=MAX_SIGNATURE_LENGTH)

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        if value != PROTOCOL_VERSION:
            raise ValueError(
                f"unsupported protocol version {value} (expected {PROTOCOL_VERSION})"
            )
        return value

    @field_validator("source", mode="before")
    @classmethod
    def _normalise_source(cls, value: object) -> object:
        """Trim and uppercase so 'water_01' and 'WATER_01' are the same node."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("payload")
    @classmethod
    def _check_payload_limits(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > MAX_PAYLOAD_KEYS:
            raise ValueError(f"at most {MAX_PAYLOAD_KEYS} keys allowed, got {len(value)}")
        for key, item in value.items():
            if not 1 <= len(key) <= MAX_KEY_LENGTH:
                raise ValueError(f"keys must be 1 to {MAX_KEY_LENGTH} characters")
            if isinstance(item, str) and len(item) > MAX_TEXT_VALUE_LENGTH:
                raise ValueError(
                    f"text values are limited to {MAX_TEXT_VALUE_LENGTH} characters"
                )
        return value

    @model_validator(mode="after")
    def _require_payload_for_data_and_events(self) -> Self:
        if self.packet_type in (PacketType.DATA, PacketType.EVENT) and not self.payload:
            raise ValueError(
                f"'{self.packet_type.value}' packets must carry a non-empty 'd' object"
            )
        return self


def _reject_constant(name: str) -> None:
    """json.loads hook: refuse NaN, Infinity and -Infinity."""
    raise ValueError(f"invalid JSON constant: {name}")


def _summarise(error: ValidationError, limit: int = 3) -> str:
    """Condense a validation error to the first few 'field: message' pairs."""
    problems = error.errors()
    parts = [
        f"{'.'.join(str(part) for part in item['loc']) or 'packet'}: {item['msg']}"
        for item in problems[:limit]
    ]
    if len(problems) > limit:
        parts.append(f"(+{len(problems) - limit} more)")
    return "; ".join(parts)


def parse_packet(line: str) -> Packet:
    """
    Parse and validate one line from the serial port.

    Raises PacketError with reason one of: empty, too_long, invalid_json,
    not_object, invalid_packet.
    """
    text = line.strip()
    if not text:
        raise PacketError("empty", "line is empty")
    if len(text) > MAX_PACKET_CHARS:
        raise PacketError(
            "too_long", f"packet is {len(text)} characters (maximum {MAX_PACKET_CHARS})"
        )

    try:
        raw = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise PacketError("invalid_json", str(exc)) from exc

    if not isinstance(raw, dict):
        raise PacketError("not_object", f"expected a JSON object, got {type(raw).__name__}")

    try:
        return Packet.model_validate(raw)
    except ValidationError as exc:
        raise PacketError("invalid_packet", _summarise(exc)) from exc