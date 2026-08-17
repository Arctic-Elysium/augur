"""Access rules.

Roles were in the schema from Milestone 0 but nothing enforced them. That is
fine for a solo campaign and wrong the moment a second person joins.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import ForbiddenError
from app.modules.campaigns.access import Access, Invite, new_code
from app.modules.campaigns.models import Campaign, CampaignRole


def access_as(role: CampaignRole) -> Access:
    return Access(
        campaign=Campaign(id=uuid.uuid4(), name="C"),
        user_id=uuid.uuid4(),
        role=role,
    )


def test_only_owner_and_gm_run_the_game():
    assert access_as(CampaignRole.OWNER).runs_the_game
    assert access_as(CampaignRole.GM).runs_the_game
    assert not access_as(CampaignRole.PLAYER).runs_the_game
    assert not access_as(CampaignRole.OBSERVER).runs_the_game


def test_observers_cannot_act():
    access_as(CampaignRole.PLAYER).require_play()
    with pytest.raises(ForbiddenError):
        access_as(CampaignRole.OBSERVER).require_play()


def test_players_cannot_do_gm_things():
    with pytest.raises(ForbiddenError):
        access_as(CampaignRole.PLAYER).require_gm()


def test_gm_is_not_owner():
    """A GM runs the table; only the owner can delete it or change roles."""
    with pytest.raises(ForbiddenError):
        access_as(CampaignRole.GM).require_owner()


def _invite(**overrides) -> Invite:
    base = dict(
        campaign_id=uuid.uuid4(), code=new_code(), role="player",
        created_by=uuid.uuid4(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        max_uses=4, uses=0, revoked=False,
    )
    base.update(overrides)
    return Invite(**base)


def test_a_fresh_invite_is_usable():
    assert not _invite().spent


def test_expired_revoked_and_used_up_invites_are_all_spent():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert _invite(expires_at=past).spent
    assert _invite(revoked=True).spent
    assert _invite(uses=4, max_uses=4).spent


def test_codes_avoid_ambiguous_characters():
    """These get read aloud down a voice call."""
    for _ in range(200):
        assert not set(new_code()) & set("IO01")
