"""Registration-token providers for worker edges."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path as PathType

logger = logging.getLogger(__name__)


class RegistrationTokenProvider(Protocol):
    """Resolve the token used by the next authenticated operation."""

    def current(self) -> str | None:
        """Return the current token without exposing it to callers."""


class EnvironmentOrFileTokenProvider:
    """Prefer an explicit token and otherwise read a mounted token file."""

    def __init__(self, env_token: str | None, token_file: PathType | None) -> None:
        self._env_token = self._normalise(env_token)
        self._token_file = token_file

    @staticmethod
    def _normalise(value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip()
        return normalised or None

    def current(self) -> str | None:
        """Return the explicit token or the latest readable file value."""
        if self._env_token is not None:
            return self._env_token
        if self._token_file is None:
            return None
        try:
            return self._normalise(self._token_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError, UnicodeError:
            logger.warning("Unable to read worker registration token file")
            return None
