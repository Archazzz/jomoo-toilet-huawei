"""Wire-level regressions; none of these tests operate real devices."""

import pytest

from jomoo_gateway.protocol import (
    STATUS_QUERY,
    Frame,
    FrameStream,
    InvalidFrame,
    candidate_fields,
)

# Published ZS800J observation, not a private household packet.
SETTINGS = bytes.fromhex("f3f40b2192e24a52b3450900003dfc")


def test_documented_status_and_query_vectors():
    frame = Frame.decode(SETTINGS)
    assert frame.command == 0xE2
    assert frame.is_known_status
    assert candidate_fields(frame) == {
        "automatic_lid": True,
        "automatic_flush": True,
        "water_temperature_level": 4,
        "seat_temperature_level": 2,
        "dryer_temperature_level": 3,
        "automatic_night_light": True,
    }
    query = Frame.decode(STATUS_QUERY)
    assert query.command == 0xE2
    assert not query.is_known_status  # An echoed request is never accepted as state.
    assert candidate_fields(query) == {}


@pytest.mark.parametrize("index", range(len(SETTINGS)))
def test_single_byte_corruption_rejected(index):
    packet = bytearray(SETTINGS)
    packet[index] ^= 1
    with pytest.raises(InvalidFrame):
        Frame.decode(packet)


@pytest.mark.parametrize("split", range(1, len(SETTINGS)))
def test_every_notification_split_boundary(split):
    stream = FrameStream()
    assert stream.feed(SETTINGS[:split]) == []
    assert stream.feed(SETTINGS[split:]) == [Frame.decode(SETTINGS)]


def test_noise_corruption_and_multiple_frames_recover():
    stream = FrameStream()
    damaged = bytearray(SETTINGS)
    damaged[-2] ^= 1
    result = stream.feed(b"junk" + damaged + SETTINGS + SETTINGS)
    assert result == [Frame.decode(SETTINGS)] * 2
    assert stream.invalid_frames == 1


def test_incomplete_frame_is_not_reported_as_state():
    stream = FrameStream()
    assert stream.feed(SETTINGS[:-1]) == []


def test_unknown_payload_length_is_not_decoded():
    frame = Frame(0x21, 0x92, 0xE2, b"\x00", b"")
    assert not frame.is_known_status
    assert candidate_fields(frame) == {}


def test_split_header_and_malformed_short_length():
    stream = FrameStream()
    assert stream.feed(b"garbage\xf3\xf4\x01\xf3") == []
    assert stream.feed(SETTINGS[1:]) == [Frame.decode(SETTINGS)]
