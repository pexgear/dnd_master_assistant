"""Session networking: protocol, dice, and two real clients through a server."""

from __future__ import annotations

import pytest

from canon_keeper.net import dice
from canon_keeper.net.client import SessionClient
from canon_keeper.net.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    Member,
    MessageType,
    ProtocolError,
    Role,
    clean_name,
    decode,
    encode,
    new_join_code,
    normalise_code,
)
from canon_keeper.net.server import SessionServer

# --------------------------------------------------------------------- protocol


def test_round_trip():
    message = decode(encode(MessageType.CHAT, text="hello"))
    assert message.type == MessageType.CHAT
    assert message.get("text") == "hello"
    assert message.ts > 0


def test_version_mismatch_is_rejected_with_a_readable_reason():
    frame = encode(MessageType.CHAT, text="hi").replace(
        f'"v":{PROTOCOL_VERSION}', '"v":99'
    )
    with pytest.raises(ProtocolError, match="protocol version 99"):
        decode(frame)


@pytest.mark.parametrize("frame", ["not json", "[1,2,3]", '{"v":1}', '"a string"'])
def test_malformed_frames_are_rejected(frame):
    with pytest.raises(ProtocolError):
        decode(frame)


def test_oversized_frames_are_rejected_before_parsing():
    """A hostile client must not be able to make us parse megabytes of JSON."""
    with pytest.raises(ProtocolError, match="too large"):
        decode('{"v":1,"t":"chat","d":{"text":"' + "x" * (MAX_FRAME_BYTES + 10) + '"}}')


def test_names_are_cleaned_and_capped():
    assert clean_name("  Marco  ") == "Marco"
    assert clean_name("") == "Anonymous"
    assert clean_name("a\nb") == "a b"
    assert len(clean_name("x" * 500)) == 32


def test_join_codes_avoid_ambiguous_characters():
    for _ in range(50):
        code = new_join_code()
        assert len(code) == 6
        assert not set(code) & set("O0I1")


def test_codes_are_normalised_for_reading_aloud():
    assert normalise_code(" abc-234 ") == "ABC234"


def test_unknown_role_falls_back_to_player():
    """A client must not be able to promote itself by sending role='admin'."""
    assert Member.from_dict({"id": "1", "name": "x", "role": "admin"}).role == "player"


# ------------------------------------------------------------------------- dice


def test_simple_roll():
    result = dice.roll("2d6+3")
    assert len(result.rolls) == 2
    assert all(1 <= value <= 6 for value in result.rolls)
    assert result.total == sum(result.rolls) + 3


def test_bare_die_defaults_to_one():
    result = dice.roll("d20")
    assert len(result.rolls) == 1
    assert result.total == result.rolls[0]


def test_keep_highest_drops_the_rest():
    result = dice.roll("4d6kh3")
    assert len(result.rolls) == 4
    assert len(result.kept) == 3
    assert sorted(result.kept, reverse=True) == sorted(result.rolls, reverse=True)[:3]
    assert result.total == sum(result.kept)


def test_keep_lowest_is_disadvantage():
    result = dice.roll("2d20kl1")
    assert result.kept == [min(result.rolls)]


def test_negative_modifier():
    result = dice.roll("1d4-1")
    assert result.modifier == -1
    assert result.total == result.rolls[0] - 1


@pytest.mark.parametrize(
    "notation", ["", "hello", "d", "0d6", "2d1", "200d6", "1d5000", "4d6kh9"]
)
def test_bad_notation_is_refused(notation):
    with pytest.raises(dice.DiceError):
        dice.roll(notation)


def test_rolls_stay_within_bounds_over_many_trials():
    for _ in range(200):
        assert 3 <= dice.roll("3d8").total <= 24


# ------------------------------------------------------- server and client wiring


@pytest.fixture
def server(qtbot):
    instance = SessionServer("Test session", code="ABC234")
    assert instance.start(0, announce=False), "could not bind an ephemeral port"
    yield instance
    instance.stop()


def _join(qtbot, server, name, role=Role.PLAYER.value, code=None):
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.join(
            f"ws://127.0.0.1:{server.port}", code or server.code, name, role
        )
    return client


def test_a_client_can_join_and_is_welcomed(qtbot, server):
    client = _join(qtbot, server, "Marco")
    try:
        assert client.me is not None
        assert client.me.name == "Marco"
        assert [m.name for m in client.members] == ["Marco"]
    finally:
        client.leave()


def test_the_wrong_join_code_is_refused_and_not_retried(qtbot, server):
    client = SessionClient()
    with qtbot.waitSignal(client.failed, timeout=5000) as blocker:
        client.join(f"ws://127.0.0.1:{server.port}", "WRONG1", "Intruder")

    assert "code" in blocker.args[0].lower()
    assert client.me is None
    # A wrong code will never come right, so the client must stop trying.
    assert client._wanted is False
    client.leave()


def test_two_clients_see_each_other_in_the_roster(qtbot, server):
    dm = _join(qtbot, server, "The DM", Role.DM.value)
    try:
        with qtbot.waitSignal(dm.roster_changed, timeout=5000):
            player = _join(qtbot, server, "Marco")

        assert {m.name for m in dm.members} == {"The DM", "Marco"}
        assert {m.role for m in dm.members} == {"dm", "player"}
        player.leave()
    finally:
        dm.leave()


def test_chat_reaches_the_other_client(qtbot, server):
    sender = _join(qtbot, server, "Marco")
    receiver = _join(qtbot, server, "Elara")
    try:
        with qtbot.waitSignal(receiver.said, timeout=5000) as blocker:
            sender.send_chat("I check the door for traps.")

        member, text = blocker.args
        assert member.name == "Marco"
        assert text == "I check the door for traps."
    finally:
        sender.leave()
        receiver.leave()


def test_the_sender_also_receives_their_own_message(qtbot, server):
    """The host echoes to everyone, so no client needs local-echo logic."""
    client = _join(qtbot, server, "Marco")
    try:
        with qtbot.waitSignal(client.said, timeout=5000) as blocker:
            client.send_chat("hello")
        assert blocker.args[1] == "hello"
    finally:
        client.leave()


def test_a_roll_is_broadcast_with_the_host_s_result(qtbot, server):
    roller = _join(qtbot, server, "Marco")
    watcher = _join(qtbot, server, "Elara")
    try:
        with qtbot.waitSignal(watcher.rolled, timeout=5000) as blocker:
            roller.send_roll("2d6+3")

        member, payload = blocker.args
        assert member.name == "Marco"
        assert len(payload["rolls"]) == 2
        assert payload["total"] == sum(payload["rolls"]) + 3
        assert "2d6+3" in payload["description"]
    finally:
        roller.leave()
        watcher.leave()


def test_bad_dice_notation_is_reported_only_to_the_roller(qtbot, server):
    roller = _join(qtbot, server, "Marco")
    watcher = _join(qtbot, server, "Elara")
    seen = []
    watcher.rolled.connect(lambda *args: seen.append(args))
    try:
        with qtbot.waitSignal(roller.failed, timeout=5000) as blocker:
            roller.send_roll("not dice")
        assert "notation" in blocker.args[0].lower()
        assert seen == [], "a typo must not be broadcast to the table"
    finally:
        roller.leave()
        watcher.leave()


def test_empty_chat_is_dropped(qtbot, server):
    client = _join(qtbot, server, "Marco")
    seen = []
    client.said.connect(lambda *args: seen.append(args))
    try:
        client.send_chat("   ")
        qtbot.wait(300)
        assert seen == []
    finally:
        client.leave()


def test_leaving_removes_you_from_the_roster(qtbot, server):
    dm = _join(qtbot, server, "The DM", Role.DM.value)
    player = _join(qtbot, server, "Marco")
    try:
        qtbot.waitUntil(lambda: len(dm.members) == 2, timeout=5000)
        with qtbot.waitSignal(dm.roster_changed, timeout=5000):
            player.leave()
        assert [m.name for m in dm.members] == ["The DM"]
    finally:
        dm.leave()


def test_server_reports_its_roster_to_the_host_app(qtbot, server):
    with qtbot.waitSignal(server.roster_changed, timeout=5000) as blocker:
        client = _join(qtbot, server, "Marco")
    assert [m.name for m in blocker.args[0]] == ["Marco"]
    client.leave()


def test_stopping_the_server_disconnects_clients(qtbot, server):
    client = _join(qtbot, server, "Marco")
    with qtbot.waitSignal(client.disconnected, timeout=5000):
        server.stop()
    client.leave()


def test_an_unreachable_host_fails_fast_with_a_useful_message(qtbot, monkeypatch):
    """QWebSocket has no connect timeout, so an unreachable host would otherwise
    hang for the OS TCP timeout -- ~40s on Windows -- looking like a freeze."""
    from canon_keeper.net import client as client_module

    monkeypatch.setattr(client_module, "CONNECT_TIMEOUT_MS", 400)
    client = SessionClient()
    try:
        # RFC 5737 TEST-NET-1: guaranteed not routable, so the SYN goes nowhere.
        with qtbot.waitSignal(client.failed, timeout=4000) as blocker:
            client.join("ws://192.0.2.1:8765", "ABC234", "Marco")

        message = blocker.args[0]
        assert "192.0.2.1" in message
        assert "firewall" in message.lower()
        assert "8765" in message
    finally:
        client.leave()
