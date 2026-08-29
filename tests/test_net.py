"""Session networking: protocol, dice, login, and filtered state over real sockets."""

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
    clean_name,
    decode,
    encode,
    new_join_code,
    normalise_code,
)
from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity

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


@pytest.mark.parametrize("frame", ["not json", "[1,2,3]", '{"v":2}', '"a string"'])
def test_malformed_frames_are_rejected(frame):
    with pytest.raises(ProtocolError):
        decode(frame)


def test_oversized_frames_are_rejected_before_parsing():
    """A hostile client must not be able to make us parse megabytes of JSON."""
    with pytest.raises(ProtocolError, match="too large"):
        decode('{"v":2,"t":"chat","d":{"text":"' + "x" * (MAX_FRAME_BYTES + 10) + '"}}')


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


def test_a_member_is_labelled_by_their_character_when_they_have_one():
    assert Member("1", "Marco", "player", character="Elara").label == "Elara"
    assert Member("1", "Marco", "player").label == "Marco"


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
def campaign(repos):
    return repos.campaigns.ensure_default("Test Campaign")


@pytest.fixture
def accounts(repos, campaign):
    """A DM and two players, one of whom plays a named character."""
    elara = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Elara")
    )
    return {
        "elara_entity": elara,
        "dm": repos.accounts.create(
            campaign.id, "gm", "run-the-game", role="dm", display_name="The DM"
        ),
        "marco": repos.accounts.create(
            campaign.id,
            "marco",
            "goblin-teeth",
            display_name="Marco",
            character_entity_id=elara.id,
        ),
        "elsa": repos.accounts.create(campaign.id, "elsa", "silver-moon"),
    }


@pytest.fixture
def server(qtbot, repos, campaign, accounts):
    instance = SessionServer(repos, campaign.id, "Test session")
    assert instance.start(0, announce=False), "could not bind an ephemeral port"
    yield instance
    instance.stop()


def _login(qtbot, server, username, password):
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_a_player_can_log_in(qtbot, server):
    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        assert client.me is not None
        assert client.me.name == "Marco"
        assert client.me.role == "player"
    finally:
        client.leave()


def test_the_password_never_crosses_the_wire(qtbot, server, monkeypatch):
    """A LAN has no TLS, so anything sent in the clear is public."""
    sent: list[str] = []
    client = SessionClient()
    real_send = client._socket.sendTextMessage

    def spy(text):
        sent.append(text)
        return real_send(text)

    monkeypatch.setattr(client._socket, "sendTextMessage", spy)
    try:
        with qtbot.waitSignal(client.connected, timeout=10000):
            client.join(f"ws://127.0.0.1:{server.port}", "marco", "goblin-teeth")
        assert sent, "nothing was sent"
        assert not any("goblin-teeth" in frame for frame in sent)
    finally:
        client.leave()


def test_a_wrong_password_is_refused_and_not_retried(qtbot, server):
    client = SessionClient()
    with qtbot.waitSignal(client.failed, timeout=10000) as blocker:
        client.join(f"ws://127.0.0.1:{server.port}", "marco", "wrong-password")

    assert "did not match" in blocker.args[0]
    assert client.me is None
    assert client._wanted is False
    client.leave()


def test_an_unknown_user_looks_exactly_like_a_wrong_password(qtbot, server):
    """Otherwise the login screen tells you who plays in the campaign."""
    client = SessionClient()
    with qtbot.waitSignal(client.failed, timeout=10000) as blocker:
        client.join(f"ws://127.0.0.1:{server.port}", "nobody", "anything")
    unknown = blocker.args[0]
    client.leave()

    other = SessionClient()
    with qtbot.waitSignal(other.failed, timeout=10000) as blocker:
        other.join(f"ws://127.0.0.1:{server.port}", "marco", "wrong-password")
    wrong = blocker.args[0]
    other.leave()

    assert unknown == wrong


def test_the_host_joins_its_own_server_with_a_token(qtbot, server):
    client = SessionClient()
    try:
        with qtbot.waitSignal(client.connected, timeout=10000):
            client.join_as_host(
                f"ws://127.0.0.1:{server.port}", server.local_token, "The DM"
            )
        assert client.me.role == "dm"
    finally:
        client.leave()


def test_a_forged_host_token_is_refused(qtbot, server):
    client = SessionClient()
    try:
        with qtbot.waitSignal(client.failed, timeout=10000):
            client.join_as_host(f"ws://127.0.0.1:{server.port}", "not-the-token", "Sneak")
        assert client.me is None
    finally:
        client.leave()


def test_chat_shows_the_character_name(qtbot, server):
    """At the table people are their characters, not their logins."""
    marco = _login(qtbot, server, "marco", "goblin-teeth")
    watcher = _login(qtbot, server, "elsa", "silver-moon")
    try:
        with qtbot.waitSignal(watcher.said, timeout=5000) as blocker:
            marco.send_chat("I check for traps.")
        member, text = blocker.args
        assert member.character == "Elara"
        assert member.label == "Elara"
        assert text == "I check for traps."
    finally:
        marco.leave()
        watcher.leave()


def test_a_player_with_no_character_falls_back_to_their_name(qtbot, server):
    elsa = _login(qtbot, server, "elsa", "silver-moon")
    try:
        with qtbot.waitSignal(elsa.said, timeout=5000) as blocker:
            elsa.send_chat("hello")
        assert blocker.args[0].label == "elsa"
    finally:
        elsa.leave()


def test_a_roll_is_broadcast_with_the_hosts_result(qtbot, server):
    roller = _login(qtbot, server, "marco", "goblin-teeth")
    watcher = _login(qtbot, server, "elsa", "silver-moon")
    try:
        with qtbot.waitSignal(watcher.rolled, timeout=5000) as blocker:
            roller.send_roll("2d6+3")
        _member, payload = blocker.args
        assert len(payload["rolls"]) == 2
        assert payload["total"] == sum(payload["rolls"]) + 3
    finally:
        roller.leave()
        watcher.leave()


def test_bad_dice_notation_is_reported_only_to_the_roller(qtbot, server):
    roller = _login(qtbot, server, "marco", "goblin-teeth")
    watcher = _login(qtbot, server, "elsa", "silver-moon")
    seen = []
    watcher.rolled.connect(lambda *args: seen.append(args))
    try:
        with qtbot.waitSignal(roller.failed, timeout=5000) as blocker:
            roller.send_roll("not dice")
        assert "notation" in blocker.args[0].lower()
        assert seen == []
    finally:
        roller.leave()
        watcher.leave()


# ------------------------------------------------------------ state over the wire


def test_a_player_receives_only_what_is_shared(qtbot, server, repos, campaign):
    sildar = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Sildar",
            data={"secrets": "he is the brother", "party_knows": "hired them"},
        )
    )
    repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="The Black Spider")
    )
    repos.shares.share(campaign.id, sildar.id)

    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        names = {e["name"] for e in client.state.all()}
        assert "Sildar" in names
        assert "The Black Spider" not in names, "an unshared NPC reached a player"

        received = client.state.get(sildar.id)
        assert received["data"].get("party_knows") == "hired them"
        assert "secrets" not in received["data"], "a secret reached a player"
    finally:
        client.leave()


def test_sharing_during_play_pushes_the_entity_live(qtbot, server, repos, campaign):
    npc = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Toblen")
    )
    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        assert client.state.get(npc.id) is None

        repos.shares.share(campaign.id, npc.id)
        with qtbot.waitSignal(client.state.changed, timeout=5000):
            server.publish_entity(npc.id)

        assert client.state.get(npc.id)["name"] == "Toblen"
    finally:
        client.leave()


def test_revoking_a_share_removes_it_from_the_player(qtbot, server, repos, campaign):
    """Taking it back must actually take it back, not leave a stale copy."""
    npc = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Toblen")
    )
    repos.shares.share(campaign.id, npc.id)
    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        assert client.state.get(npc.id) is not None

        repos.shares.unshare_all(npc.id)
        with qtbot.waitSignal(client.state.changed, timeout=5000):
            server.publish_entity(npc.id)

        assert client.state.get(npc.id) is None
    finally:
        client.leave()


def test_a_share_with_one_player_does_not_reach_another(qtbot, server, repos, campaign, accounts):
    contact = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="The Fence")
    )
    repos.shares.share(campaign.id, contact.id, accounts["marco"].id)

    marco = _login(qtbot, server, "marco", "goblin-teeth")
    elsa = _login(qtbot, server, "elsa", "silver-moon")
    try:
        assert marco.state.get(contact.id) is not None
        assert elsa.state.get(contact.id) is None
    finally:
        marco.leave()
        elsa.leave()


def test_a_player_sees_their_own_character(qtbot, server):
    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        own = client.state.own_character()
        assert own is not None and own["name"] == "Elara"
    finally:
        client.leave()


def test_a_player_can_edit_their_own_character_over_the_wire(qtbot, server, repos, accounts):
    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(client.state.changed, timeout=5000):
            client.send_edit(accounts["elara_entity"].id, {"data": {"hp": 7}})

        assert repos.entities.get(accounts["elara_entity"].id).data["hp"] == 7
    finally:
        client.leave()


def test_a_player_editing_someone_else_is_refused(qtbot, server, repos, campaign):
    npc = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Sildar",
            data={"status": "alive"},
        )
    )
    repos.shares.share(campaign.id, npc.id)
    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(client.failed, timeout=5000) as blocker:
            client.send_edit(npc.id, {"data": {"status": "dead"}})
        assert "own character" in blocker.args[0]
        assert repos.entities.get(npc.id).data["status"] == "alive"
    finally:
        client.leave()


def test_the_dm_receives_the_unfiltered_campaign(qtbot, server, repos, campaign):
    repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Sildar",
            data={"secrets": "the brother"},
        )
    )
    client = SessionClient()
    try:
        with qtbot.waitSignal(client.connected, timeout=10000):
            client.join_as_host(f"ws://127.0.0.1:{server.port}", server.local_token, "DM")
        sildar = next(e for e in client.state.all() if e["name"] == "Sildar")
        assert sildar["data"]["secrets"] == "the brother"
    finally:
        client.leave()


# -------------------------------------------------------------------- roster etc


def test_two_clients_see_each_other(qtbot, server):
    marco = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(marco.roster_changed, timeout=5000):
            elsa = _login(qtbot, server, "elsa", "silver-moon")
        assert {m.name for m in marco.members} == {"Marco", "elsa"}
        elsa.leave()
    finally:
        marco.leave()


def test_leaving_removes_you_from_the_roster(qtbot, server):
    marco = _login(qtbot, server, "marco", "goblin-teeth")
    elsa = _login(qtbot, server, "elsa", "silver-moon")
    try:
        qtbot.waitUntil(lambda: len(marco.members) == 2, timeout=5000)
        with qtbot.waitSignal(marco.roster_changed, timeout=5000):
            elsa.leave()
        assert [m.name for m in marco.members] == ["Marco"]
    finally:
        marco.leave()


def test_stopping_the_server_disconnects_clients(qtbot, server):
    client = _login(qtbot, server, "marco", "goblin-teeth")
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
            client.join("ws://192.0.2.1:8765", "marco", "goblin-teeth")

        message = blocker.args[0]
        assert "192.0.2.1" in message
        assert "firewall" in message.lower()
        assert "8765" in message
    finally:
        client.leave()


# ------------------------------------------------------------------ panel names


def test_the_dms_panel_names_reach_a_player(qtbot, server, repos):
    """The DM renames a panel for the party; everyone's dock follows."""
    repos.settings.set("panel_name.party.table", "The Tavern")

    client = SessionClient()
    try:
        with qtbot.waitSignal(client.panel_names_received, timeout=10000) as blocker:
            client.join(f"ws://127.0.0.1:{server.port}", "marco", "goblin-teeth")
        assert blocker.args[0] == {"table": "The Tavern"}
    finally:
        client.leave()


def test_renaming_mid_session_is_pushed(qtbot, server, repos):
    client = _login(qtbot, server, "marco", "goblin-teeth")
    try:
        repos.settings.set("panel_name.party.cities", "The Sword Coast")
        with qtbot.waitSignal(client.panel_names_received, timeout=5000) as blocker:
            server.publish_panel_names()
        assert blocker.args[0]["cities"] == "The Sword Coast"
    finally:
        client.leave()


def test_a_private_rename_is_not_published(qtbot, server, repos):
    """Only the party names travel; what you call it yourself stays yours."""
    repos.settings.set("panel_name.local.table", "Chat")
    repos.settings.set("panel_name.party.table", "The Tavern")

    assert server.panel_names == {"table": "The Tavern"}
