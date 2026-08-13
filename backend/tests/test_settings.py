import pytest
from pydantic import ValidationError

from app.core.config.settings import Settings


def test_weak_session_secret_rejected_outside_local():
    with pytest.raises(ValidationError):
        Settings(environment="prod", session_secret="short")


def test_local_tolerates_empty_secret():
    assert Settings(environment="local", session_secret="").session_secret
