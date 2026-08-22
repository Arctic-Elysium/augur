"""Platform administration.

Two properties matter and they pull against each other: an admin must be able
to open a campaign they never joined, and a non-admin must not be able to tell
that campaign exists. Everything here is one of those two.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.core.auth.deps import is_platform_admin
from app.core.config.settings import Settings
from app.modules.campaigns.access import Access
from app.modules.campaigns.models import Campaign, CampaignRole


@dataclass(frozen=True)
class FakePrincipal:
    subject: str = "s"
    groups: tuple[str, ...] = ()

    def in_group(self, group: str) -> bool:
        return group in self.groups

    def in_any_group(self, groups) -> bool:
        return any(g in self.groups for g in groups)


def settings(**kw) -> Settings:
    return Settings(session_secret="x" * 48, **kw)


def access(role: CampaignRole, is_admin: bool = False) -> Access:
    return Access(
        campaign=Campaign(id=uuid.uuid4(), name="C"),
        user_id=uuid.uuid4(),
        role=role,
        is_admin=is_admin,
    )


# --------------------------------------------------------------- group check


def test_configured_admin_group_grants_admin():
    assert is_platform_admin(
        FakePrincipal(groups=("auth_admins",)), settings()
    )


def test_unrelated_groups_do_not():
    assert not is_platform_admin(
        FakePrincipal(groups=("players", "auth_users")), settings()
    )


def test_a_deployment_can_name_its_own_group():
    assert is_platform_admin(
        FakePrincipal(groups=("ops",)),
        settings(oidc_admin_groups=["ops"]),
    )


def test_no_groups_at_all_is_not_admin():
    """The claim being absent must read as "not admin", not as "unknown, allow"."""
    assert not is_platform_admin(FakePrincipal(), settings())


# ------------------------------------------------------------------- rights


def test_admin_runs_the_game_in_a_campaign_they_never_joined():
    a = access(CampaignRole.OBSERVER, is_admin=True)
    assert a.runs_the_game
    assert a.can_play
    a.require_gm()  # does not raise


def test_admin_may_act_as_owner_without_being_owner():
    """`require_owner` passes so test campaigns can be deleted; `is_owner`
    stays false so "who actually owns this" is still answerable."""
    a = access(CampaignRole.OBSERVER, is_admin=True)
    a.require_owner()
    assert not a.is_owner


def test_admin_does_not_read_private_journals():
    """The journal's promise is the whole reason anyone writes honestly in it.
    An admin backdoor would quietly make that promise false."""
    assert not access(CampaignRole.OWNER, is_admin=True).reads_private_notes
    assert not access(CampaignRole.OWNER).reads_private_notes


def test_observer_without_admin_still_cannot_act():
    a = access(CampaignRole.OBSERVER)
    assert not a.runs_the_game
    assert not a.can_play
    with pytest.raises(Exception):
        a.require_gm()


def test_plain_player_is_unaffected_by_the_admin_field():
    a = access(CampaignRole.PLAYER)
    assert a.can_play
    assert not a.runs_the_game
