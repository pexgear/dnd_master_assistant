"""The approval queue, reconnect sync, and gear on the sheet."""

from __future__ import annotations

import pytest

from canon_keeper.net.cache import forget, load, save, versions
from canon_keeper.net.projection import Viewer, snapshot_since, split_sheet_change
from canon_keeper.net.server import describe_changes
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper.rules.sheet import new_sheet


@pytest.fixture
def campaign(repos):
    return repos.campaigns.ensure_default("Test Campaign")


@pytest.fixture
def marco(repos, campaign):
    return repos.accounts.create(campaign.id, "marco", "goblin-teeth", display_name="Marco")


@pytest.fixture
def elara(repos, campaign, marco):
    entity = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            data={"sheet": new_sheet(class_index="wizard", level=5)},
        )
    )
    repos.entities.set_owner(entity.id, marco.id)
    return repos.entities.get(entity.id)


@pytest.fixture
def viewer(repos, marco):
    return Viewer(
        account_id=marco.id,
        is_dm=False,
        owned_entity_ids=repos.entities.owned_ids(marco.id),
    )


# --------------------------------------------------------- splitting an edit


def test_only_changed_fields_are_reported():
    """A client sends the whole sheet back; unchanged fields are not a request."""
    existing = {"level": 5, "hp_current": 20, "class_index": "wizard"}
    proposed = {"level": 5, "hp_current": 12, "class_index": "wizard"}

    state, build = split_sheet_change(existing, proposed)

    assert state == {"hp_current": 12}
    assert build == {}


def test_state_and_build_are_separated():
    existing = {"level": 5, "hp_current": 20}
    proposed = {"level": 6, "hp_current": 12}

    state, build = split_sheet_change(existing, proposed)

    assert state == {"hp_current": 12}
    assert build == {"level": 6}


def test_unknown_fields_are_dropped_by_the_split():
    state, build = split_sheet_change({}, {"mystery": 1, "hp_current": 3})
    assert state == {"hp_current": 3}
    assert "mystery" not in build


def test_changes_are_described_readably():
    assert describe_changes({"level": 6}) == "level to 6"
    assert "class" in describe_changes({"class_index": "bard"})


# ------------------------------------------------------------------ proposals


def test_a_proposal_is_recorded(repos, campaign, elara, marco):
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    assert proposal.is_open
    assert repos.proposals.open_count(campaign.id) == 1
    assert repos.proposals.open_for(campaign.id)[0].changes == {"level": 6}


def test_a_second_proposal_supersedes_the_first(repos, campaign, elara, marco):
    """Fiddling with a level must not leave the DM five things to answer."""
    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)
    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 7}, 1)

    open_now = repos.proposals.open_for(campaign.id)
    assert len(open_now) == 1
    assert open_now[0].changes == {"level": 7}


def test_proposals_for_different_characters_coexist(repos, campaign, elara, marco):
    other = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brakk")
    )
    repos.entities.set_owner(other.id, marco.id)

    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)
    repos.proposals.propose(campaign.id, other.id, marco.id, {"level": 2}, 1)

    assert repos.proposals.open_count(campaign.id) == 2


def test_deciding_closes_it(repos, campaign, elara, marco):
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)
    repos.proposals.decide(proposal.id, "approved")

    assert repos.proposals.open_count(campaign.id) == 0
    assert repos.proposals.get(proposal.id).status == "approved"


def test_only_the_proposed_fields_are_applied(repos, campaign, elara, marco):
    """Storing a whole sheet would reinstate whatever the DM changed meanwhile."""
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    # The DM edits something else before answering.
    meanwhile = repos.entities.get(elara.id)
    meanwhile.data["sheet"]["species"] = "elf"
    repos.entities.update(meanwhile)

    assert server.decide(proposal.id, approve=True)

    sheet = repos.entities.get(elara.id).data["sheet"]
    assert sheet["level"] == 6, "the proposal applied"
    assert sheet["species"] == "elf", "the DM's own edit survived"


def test_refusing_changes_nothing(repos, campaign, elara, marco):
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    server.decide(proposal.id, approve=False)

    assert repos.entities.get(elara.id).data["sheet"]["level"] == 5
    assert repos.proposals.get(proposal.id).status == "rejected"


def test_a_proposal_against_an_old_version_is_flagged_not_hidden(
    repos, campaign, elara, marco
):
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    moved_on = repos.entities.get(elara.id)
    moved_on.summary = "changed"
    repos.entities.update(moved_on)

    [described] = server.proposals
    assert described["stale"] is True
    assert described["character"] == "Elara"
    assert described["who"] == "Marco"


# ------------------------------------------------------------- reconnect sync


def test_nothing_is_resent_when_nothing_changed(repos, campaign, elara, viewer):
    known = {elara.id: repos.entities.get(elara.id).version}

    changed, gone = snapshot_since(repos, campaign.id, viewer, known)

    assert changed == []
    assert gone == []


def test_only_what_moved_comes_back(repos, campaign, elara, viewer, marco):
    other = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )
    repos.shares.share(campaign.id, other.id)
    known = {
        elara.id: repos.entities.get(elara.id).version,
        other.id: repos.entities.get(other.id).version,
    }

    touched = repos.entities.get(other.id)
    touched.summary = "changed"
    repos.entities.update(touched)

    changed, gone = snapshot_since(repos, campaign.id, viewer, known)

    assert [e["id"] for e in changed] == [other.id]
    assert gone == []


def test_a_revoked_share_comes_back_as_gone(repos, campaign, elara, viewer):
    """Silence would leave a stale copy sitting on their screen."""
    npc = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )
    repos.shares.share(campaign.id, npc.id)
    known = {elara.id: 1, npc.id: 1}

    repos.shares.unshare_all(npc.id)
    _changed, gone = snapshot_since(repos, campaign.id, viewer, known)

    assert gone == [npc.id]


def test_an_unknown_client_gets_everything(repos, campaign, elara, viewer):
    changed, _gone = snapshot_since(repos, campaign.id, viewer, {})
    assert [e["id"] for e in changed] == [elara.id]


# -------------------------------------------------------------- the disk cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CANONKEEPER_DATA_DIR", str(tmp_path))


def test_the_cache_round_trips():
    entities = {1: {"id": 1, "name": "Elara", "version": 3}}
    save("ws://host:8765", "marco", entities)

    assert load("ws://host:8765", "marco") == entities


def test_two_logins_on_one_machine_do_not_mix():
    save("ws://host:8765", "marco", {1: {"id": 1, "name": "Elara", "version": 1}})
    save("ws://host:8765", "elsa", {2: {"id": 2, "name": "Brakk", "version": 1}})

    assert list(load("ws://host:8765", "marco")) == [1]
    assert list(load("ws://host:8765", "elsa")) == [2]


def test_two_campaigns_do_not_mix():
    save("ws://a:8765", "marco", {1: {"id": 1, "version": 1}})
    save("ws://b:8765", "marco", {2: {"id": 2, "version": 1}})

    assert list(load("ws://a:8765", "marco")) == [1]


def test_the_cache_filename_reveals_nothing():
    from canon_keeper.net import cache

    name = cache._key("ws://192.168.1.10:8765", "marco")
    assert "marco" not in name
    assert "192.168" not in name


def test_a_corrupt_cache_is_survivable():
    from canon_keeper.net import cache

    path = cache._path("ws://host:8765", "marco")
    path.write_text("{ not json", encoding="utf-8")

    assert load("ws://host:8765", "marco") == {}


def test_forgetting_removes_it():
    save("ws://host:8765", "marco", {1: {"id": 1, "version": 1}})
    forget("ws://host:8765", "marco")
    assert load("ws://host:8765", "marco") == {}


def test_versions_are_string_keyed_for_the_wire():
    """JSON object keys are always strings; being explicit avoids a subtle bug."""
    assert versions({1: {"version": 4}}) == {"1": 4}


def test_entities_without_a_version_are_not_claimed():
    assert versions({1: {"name": "no version here"}}) == {}
