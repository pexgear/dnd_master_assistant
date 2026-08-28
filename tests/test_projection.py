"""Accounts, sharing, and what a player is actually allowed to see.

These are the tests that matter most in the project. Everything else being wrong
costs an evening; this being wrong spoils the campaign.
"""

from __future__ import annotations

import pytest

from canon_keeper.net import auth
from canon_keeper.net.projection import (
    EditRefused,
    Viewer,
    apply_player_edit,
    project_entity,
    snapshot,
    visible_entity_ids,
)
from canon_keeper.repo.entities import KIND_LOCATION, KIND_NPC, KIND_PC, Entity


@pytest.fixture
def campaign(repos):
    return repos.campaigns.ensure_default("Test Campaign")


@pytest.fixture
def world(repos, campaign):
    """A small campaign: two PCs, a secret-laden NPC, and two places."""
    made = {}
    made["marco_pc"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            summary="A tired cleric",
            data={"status": "alive", "hp": 14, "max_hp": 22, "secrets": "is a spy"},
        )
    )
    made["elsa_pc"] = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brakk")
    )
    made["sildar"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Sildar Hallwinter",
            summary="A weary man of the Alliance",
            data={
                "status": "alive",
                "motive": "Find Iarno before the others do",
                "secrets": "He is Iarno's brother",
                "voice": "clipped, formal",
                "party_knows": "He hired them in Neverwinter",
            },
        )
    )
    made["hidden_npc"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="The Black Spider",
            data={"secrets": "is a drow"},
        )
    )
    made["city"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_LOCATION,
            name="Phandalin",
            data={"place_type": "town", "notes": "DM notes", "rumours": "a hook"},
        )
    )
    made["hideout"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_LOCATION,
            name="Cragmaw Hideout",
            parent_id=made["city"].id,
            data={"place_type": "building", "notes": "the ambush"},
        )
    )
    return made


@pytest.fixture
def marco(repos, campaign, world):
    return repos.accounts.create(
        campaign.id,
        "marco",
        "goblin-teeth",
        display_name="Marco",
        character_entity_id=world["marco_pc"].id,
    )


@pytest.fixture
def elsa(repos, campaign, world):
    return repos.accounts.create(
        campaign.id, "elsa", "silver-moon", character_entity_id=world["elsa_pc"].id
    )


def _viewer(account) -> Viewer:
    return Viewer(
        account_id=account.id,
        is_dm=account.is_dm,
        own_entity_id=account.character_entity_id,
    )


# ------------------------------------------------------------------- accounts


def test_password_is_never_stored(repos, campaign):
    account = repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    assert b"goblin-teeth" not in account.verifier
    assert b"goblin-teeth" not in account.salt
    assert account.verifier != b""


def test_login_succeeds_without_sending_the_password(repos, campaign):
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    stored = repos.accounts.by_username(campaign.id, "marco")

    # What the client does: derive locally, prove against the server's nonce.
    nonce = auth.new_nonce()
    client_verifier = auth.derive_verifier("goblin-teeth", stored.salt)
    offered = auth.proof(client_verifier, nonce)

    assert repos.accounts.authenticate(campaign.id, "marco", nonce, offered) is not None


def test_the_wrong_password_is_refused(repos, campaign):
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    stored = repos.accounts.by_username(campaign.id, "marco")
    nonce = auth.new_nonce()
    offered = auth.proof(auth.derive_verifier("wrong", stored.salt), nonce)

    assert repos.accounts.authenticate(campaign.id, "marco", nonce, offered) is None


def test_a_proof_cannot_be_replayed_against_a_new_nonce(repos, campaign):
    """An eavesdropper on the LAN must not be able to reuse what they heard."""
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    stored = repos.accounts.by_username(campaign.id, "marco")
    captured = auth.proof(auth.derive_verifier("goblin-teeth", stored.salt), auth.new_nonce())

    assert repos.accounts.authenticate(campaign.id, "marco", auth.new_nonce(), captured) is None


def test_usernames_are_case_insensitive_and_unique(repos, campaign):
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    assert repos.accounts.by_username(campaign.id, "MARCO") is not None
    with pytest.raises(ValueError, match="taken"):
        repos.accounts.create(campaign.id, "Marco", "another")


def test_a_disabled_account_cannot_log_in(repos, campaign):
    account = repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    repos.accounts.set_disabled(account.id, True)
    stored = repos.accounts.by_username(campaign.id, "marco")
    nonce = auth.new_nonce()
    offered = auth.proof(auth.derive_verifier("goblin-teeth", stored.salt), nonce)

    assert repos.accounts.authenticate(campaign.id, "marco", nonce, offered) is None


def test_failures_are_indistinguishable(repos, campaign):
    """The login screen must not reveal who plays in the campaign."""
    assert auth.explain("no such user") == auth.explain("wrong password")


# ------------------------------------------------------------------- sharing


def test_a_player_sees_nothing_until_something_is_shared(repos, campaign, world, marco):
    visible = visible_entity_ids(repos, campaign.id, _viewer(marco))
    # Their own character, and nothing else.
    assert visible == {world["marco_pc"].id}


def test_sharing_with_the_party_reaches_every_player(repos, campaign, world, marco, elsa):
    repos.shares.share(campaign.id, world["sildar"].id)

    assert world["sildar"].id in visible_entity_ids(repos, campaign.id, _viewer(marco))
    assert world["sildar"].id in visible_entity_ids(repos, campaign.id, _viewer(elsa))


def test_sharing_with_one_player_excludes_the_others(repos, campaign, world, marco, elsa):
    """Only the rogue knows about the contact."""
    repos.shares.share(campaign.id, world["hidden_npc"].id, marco.id)

    assert world["hidden_npc"].id in visible_entity_ids(repos, campaign.id, _viewer(marco))
    assert world["hidden_npc"].id not in visible_entity_ids(repos, campaign.id, _viewer(elsa))


def test_unsharing_takes_it_back(repos, campaign, world, marco):
    repos.shares.share(campaign.id, world["sildar"].id)
    repos.shares.unshare_all(world["sildar"].id)

    assert world["sildar"].id not in visible_entity_ids(repos, campaign.id, _viewer(marco))


def test_the_dm_sees_everything(repos, campaign, world):
    visible = visible_entity_ids(repos, campaign.id, Viewer.dungeon_master())
    assert visible == {e.id for e in world.values()}


def test_sharing_is_idempotent(repos, campaign, world):
    repos.shares.share(campaign.id, world["sildar"].id)
    repos.shares.share(campaign.id, world["sildar"].id)
    assert repos.shares.audiences(world["sildar"].id) == (True, set())


def test_set_audiences_replaces_the_whole_set(repos, campaign, world, marco, elsa):
    repos.shares.share(campaign.id, world["sildar"].id)
    repos.shares.set_audiences(campaign.id, world["sildar"].id, False, {elsa.id})

    party, accounts = repos.shares.audiences(world["sildar"].id)
    assert party is False
    assert accounts == {elsa.id}


# ---------------------------------------------------------------- projection


def test_dm_secrets_never_reach_a_player(repos, campaign, world, marco):
    repos.shares.share(campaign.id, world["sildar"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, _viewer(marco)) if e["id"] == world["sildar"].id
    ]

    assert projected["name"] == "Sildar Hallwinter"
    assert projected["data"].get("party_knows") == "He hired them in Neverwinter"
    for forbidden in ("secrets", "motive", "voice"):
        assert forbidden not in projected["data"], f"{forbidden} leaked to a player"


def test_dm_notes_on_a_place_never_reach_a_player(repos, campaign, world, marco):
    repos.shares.share(campaign.id, world["city"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, _viewer(marco)) if e["id"] == world["city"].id
    ]

    assert projected["data"].get("place_type") == "town"
    assert "notes" not in projected["data"]
    assert "rumours" not in projected["data"]


def test_unknown_fields_are_private_by_default(repos, campaign, world, marco):
    """An allowlist, so a field invented later cannot leak on the next release."""
    sildar = repos.entities.get(world["sildar"].id)
    sildar.data["blackmail_material"] = "the letters"
    repos.entities.update(sildar)
    repos.shares.share(campaign.id, sildar.id)

    [projected] = [
        e for e in snapshot(repos, campaign.id, _viewer(marco)) if e["id"] == sildar.id
    ]
    assert "blackmail_material" not in projected["data"]


def test_an_unshared_entity_is_absent_not_redacted(repos, campaign, world, marco):
    """Existence is itself a secret: no stub, no placeholder, no id."""
    repos.shares.share(campaign.id, world["sildar"].id)
    ids = {e["id"] for e in snapshot(repos, campaign.id, _viewer(marco))}
    assert world["hidden_npc"].id not in ids


def test_a_parent_place_is_hidden_unless_it_too_is_shared(repos, campaign, world, marco):
    """Otherwise 'inside somewhere you have never heard of' leaks the somewhere."""
    repos.shares.share(campaign.id, world["hideout"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, _viewer(marco)) if e["id"] == world["hideout"].id
    ]
    assert projected["parent_id"] is None

    repos.shares.share(campaign.id, world["city"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, _viewer(marco)) if e["id"] == world["hideout"].id
    ]
    assert projected["parent_id"] == world["city"].id


def test_a_player_sees_their_own_character_without_a_share(repos, campaign, world, marco):
    ids = {e["id"] for e in snapshot(repos, campaign.id, _viewer(marco))}
    assert world["marco_pc"].id in ids


def test_own_pc_shows_player_fields_but_not_dm_notes(repos, campaign, world, marco):
    pc = repos.entities.get(world["marco_pc"].id)
    pc.data["inventory"] = "a rope"
    repos.entities.update(pc)

    [projected] = [
        e for e in snapshot(repos, campaign.id, _viewer(marco)) if e["id"] == pc.id
    ]
    assert projected["own"] is True
    assert projected["data"]["hp"] == 14
    assert projected["data"]["inventory"] == "a rope"
    # The DM's private note about someone's character stays the DM's.
    assert "secrets" not in projected["data"]


def test_another_players_pc_does_not_expose_their_private_fields(
    repos, campaign, world, marco, elsa
):
    pc = repos.entities.get(world["elsa_pc"].id)
    pc.data.update({"hp": 9, "player_notes": "I suspect the cleric"})
    repos.entities.update(pc)
    repos.shares.share(campaign.id, pc.id)

    [projected] = [
        e for e in snapshot(repos, campaign.id, _viewer(marco)) if e["id"] == pc.id
    ]
    assert projected["own"] is False
    assert projected["data"]["hp"] == 9, "party HP is shared on purpose"
    assert "player_notes" not in projected["data"]


def test_the_dm_projection_is_complete(repos, campaign, world):
    [projected] = [
        e
        for e in snapshot(repos, campaign.id, Viewer.dungeon_master())
        if e["id"] == world["sildar"].id
    ]
    assert projected["data"]["secrets"] == "He is Iarno's brother"


# --------------------------------------------------------------- player edits


def test_a_player_can_edit_their_own_character(repos, campaign, world, marco):
    apply_player_edit(
        repos, _viewer(marco), world["marco_pc"].id, {"data": {"hp": 3, "conditions": "prone"}}
    )
    updated = repos.entities.get(world["marco_pc"].id)
    assert updated.data["hp"] == 3
    assert updated.data["conditions"] == "prone"


def test_a_player_cannot_edit_another_character(repos, campaign, world, marco):
    with pytest.raises(EditRefused):
        apply_player_edit(repos, _viewer(marco), world["elsa_pc"].id, {"data": {"hp": 999}})
    assert "hp" not in repos.entities.get(world["elsa_pc"].id).data


def test_a_player_cannot_edit_an_npc(repos, campaign, world, marco):
    repos.shares.share(campaign.id, world["sildar"].id)
    with pytest.raises(EditRefused):
        apply_player_edit(
            repos, _viewer(marco), world["sildar"].id, {"data": {"status": "dead"}}
        )
    assert repos.entities.get(world["sildar"].id).data["status"] == "alive"


def test_a_player_cannot_rewrite_dm_fields_on_their_own_sheet(repos, campaign, world, marco):
    """The one that matters: an edit is an allowlist, not a merge."""
    apply_player_edit(
        repos,
        _viewer(marco),
        world["marco_pc"].id,
        {
            "data": {"hp": 20, "secrets": "rewritten", "motive": "mine now"},
            "name": "Elara the Unkillable",
            "kind": "npc",
        },
    )

    updated = repos.entities.get(world["marco_pc"].id)
    assert updated.data["hp"] == 20
    assert updated.data["secrets"] == "is a spy", "a DM field was overwritten"
    assert "motive" not in updated.data
    assert updated.name == "Elara"
    assert updated.kind == KIND_PC


def test_a_player_cannot_move_themselves_into_an_unshared_place(
    repos, campaign, world, marco
):
    apply_player_edit(
        repos, _viewer(marco), world["marco_pc"].id, {"parent_id": world["hideout"].id}
    )
    assert repos.entities.get(world["marco_pc"].id).parent_id is None


def test_a_player_with_no_character_can_edit_nothing(repos, campaign, world):
    spectator = repos.accounts.create(campaign.id, "watcher", "passing-by")
    with pytest.raises(EditRefused):
        apply_player_edit(repos, _viewer(spectator), world["sildar"].id, {"data": {"hp": 1}})
