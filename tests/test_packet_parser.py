"""
test_packet_parser.py - Tests for the packet format and parser (Phase 3, Task 3).
"""

import json
from typing import Any

import pytest

from backend.services.packet_parser import (
    MAX_PACKET_CHARS,
    MAX_SEQUENCE,
    PacketError,
    PacketType,
    parse_packet,
)


def make(**overrides: Any) -> dict[str, Any]:
    """A valid packet dictionary, with optional field overrides."""
    packet: dict[str, Any] = {
        "v": 1,
        "src": "WATER_01",
        "type": "data",
        "seq": 7,
        "d": {"flow_in": 15.4},
    }
    packet.update(overrides)
    return packet


def raw(**overrides: Any) -> str:
    return json.dumps(make(**overrides))


def raw_without(key: str) -> str:
    packet = make()
    del packet[key]
    return json.dumps(packet)


# --- valid packets -----------------------------------------------------------


def test_parses_a_full_data_packet() -> None:
    packet = parse_packet(
        '{"v":1,"src":"WATER_01","type":"data","seq":1042,'
        '"d":{"flow_in":15.4,"flow_out":11.2,"humidity":58}}'
    )
    assert packet.version == 1
    assert packet.source == "WATER_01"
    assert packet.packet_type == PacketType.DATA
    assert packet.sequence == 1042
    assert packet.payload == {"flow_in": 15.4, "flow_out": 11.2, "humidity": 58}
    assert packet.signature is None


def test_heartbeat_may_omit_the_payload() -> None:
    packet = parse_packet('{"v":1,"src":"MASTER_01","type":"heartbeat","seq":0}')
    assert packet.packet_type == PacketType.HEARTBEAT
    assert packet.payload == {}


def test_source_is_trimmed_and_uppercased() -> None:
    assert parse_packet(raw(src=" water_01 ")).source == "WATER_01"


def test_surrounding_whitespace_and_crlf_are_ignored() -> None:
    assert parse_packet("  " + raw() + "\r\n").source == "WATER_01"


def test_payload_value_types_are_preserved() -> None:
    payload = parse_packet(
        raw(d={"door": True, "count": 3, "flow": 1.5, "state": "open"})
    ).payload
    assert type(payload["door"]) is bool
    assert type(payload["count"]) is int
    assert type(payload["flow"]) is float
    assert type(payload["state"]) is str


def test_signature_field_is_accepted() -> None:
    assert parse_packet(raw(sig="abc123")).signature == "abc123"


def test_maximum_sequence_number_is_accepted() -> None:
    assert parse_packet(raw(seq=MAX_SEQUENCE)).sequence == MAX_SEQUENCE


# --- rejected lines ----------------------------------------------------------


@pytest.mark.parametrize("line", ["", "   \r\n"])
def test_empty_lines_are_rejected(line: str) -> None:
    with pytest.raises(PacketError) as info:
        parse_packet(line)
    assert info.value.reason == "empty"


def test_overlong_lines_are_rejected() -> None:
    with pytest.raises(PacketError) as info:
        parse_packet("x" * (MAX_PACKET_CHARS + 1))
    assert info.value.reason == "too_long"


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("{not json", id="not-json"),
        pytest.param('{"v":1,"src":"WATER_01"', id="truncated"),
        pytest.param(
            '{"v":1,"src":"WATER_01","type":"data","seq":1,"d":{"a":NaN}}', id="nan"
        ),
        pytest.param(
            '{"v":1,"src":"WATER_01","type":"data","seq":1,"d":{"a":Infinity}}',
            id="infinity",
        ),
        pytest.param("[" * 900, id="deep-nesting"),
    ],
)
def test_invalid_json_is_rejected(line: str) -> None:
    with pytest.raises(PacketError) as info:
        parse_packet(line)
    assert info.value.reason == "invalid_json"


@pytest.mark.parametrize("line", ["[1,2]", '"text"', "42", "null"])
def test_json_that_is_not_an_object_is_rejected(line: str) -> None:
    with pytest.raises(PacketError) as info:
        parse_packet(line)
    assert info.value.reason == "not_object"


INVALID_PACKETS = [
    pytest.param(raw_without("v"), id="missing-v"),
    pytest.param(raw_without("src"), id="missing-src"),
    pytest.param(raw_without("type"), id="missing-type"),
    pytest.param(raw_without("seq"), id="missing-seq"),
    pytest.param(raw(v=2), id="version-2"),
    pytest.param(raw(v="1"), id="version-as-string"),
    pytest.param(raw(src="bad id!"), id="src-bad-characters"),
    pytest.param(raw(src="AB"), id="src-too-short"),
    pytest.param(raw(src=5), id="src-not-a-string"),
    pytest.param(raw(type="toaster"), id="unknown-type"),
    pytest.param(raw(seq=-1), id="negative-seq"),
    pytest.param(raw(seq=MAX_SEQUENCE + 1), id="seq-too-big"),
    pytest.param(raw(seq=1.5), id="float-seq"),
    pytest.param(raw(seq="7"), id="string-seq"),
    pytest.param(raw(seq=True), id="bool-seq"),
    pytest.param(raw(d=[]), id="payload-is-a-list"),
    pytest.param(raw(d="x"), id="payload-is-a-string"),
    pytest.param(raw(d={}), id="data-with-empty-payload"),
    pytest.param(raw(type="event", d={}), id="event-with-empty-payload"),
    pytest.param(raw(d={"a": {"b": 1}}), id="nested-object"),
    pytest.param(raw(d={"a": [1]}), id="nested-list"),
    pytest.param(raw(d={"a": None}), id="null-value"),
    pytest.param(raw(d={f"k{i}": 1 for i in range(33)}), id="too-many-keys"),
    pytest.param(raw(d={"k" * 41: 1}), id="key-too-long"),
    pytest.param(raw(d={"": 1}), id="empty-key"),
    pytest.param(raw(d={"a": "x" * 201}), id="text-value-too-long"),
    pytest.param(raw(color="red"), id="unknown-field"),
    pytest.param(raw(sig="x" * 129), id="signature-too-long"),
]


@pytest.mark.parametrize("line", INVALID_PACKETS)
def test_invalid_packets_are_rejected(line: str) -> None:
    with pytest.raises(PacketError) as info:
        parse_packet(line)
    assert info.value.reason == "invalid_packet"
    assert info.value.detail


@pytest.mark.parametrize(
    ("line", "field"),
    [
        pytest.param(raw_without("src"), "src", id="missing-src"),
        pytest.param(raw(seq=-1), "seq", id="bad-seq"),
        pytest.param(raw(color="red"), "color", id="unknown-field"),
    ],
)
def test_error_detail_names_the_offending_field(line: str, field: str) -> None:
    with pytest.raises(PacketError) as info:
        parse_packet(line)
    assert field in info.value.detail
    assert str(info.value).startswith("invalid_packet: ")