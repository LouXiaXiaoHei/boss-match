"""Dual-identity Chrome Profile management."""

import os

from src.core.constants import DEFAULT_BASE_DATA_DIR, GEEK_CDP_PORT, BOSS_CDP_PORT


class ProfileManager:
    """Manages Chrome profiles and CDP ports for geek/boss identities."""

    def __init__(self):
        self._base_dir = os.path.expanduser(DEFAULT_BASE_DATA_DIR)

    def profile_dir(self, identity: str) -> str:
        """Return the Chrome profile directory for the given identity."""
        return os.path.join(self._base_dir, f"chrome-profile-{identity}")

    def cdp_port(self, identity: str) -> int:
        """Return the CDP port for the given identity."""
        return GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT

    def login_url(self, identity: str) -> str:
        """Return the login page URL for the given identity."""
        if identity == "boss":
            return "https://www.zhipin.com/web/boss/"
        return "https://www.zhipin.com/web/user/"

    def ensure_dirs(self, identity: str) -> str:
        """Ensure profile directory exists, return its path."""
        d = self.profile_dir(identity)
        os.makedirs(os.path.join(d, "Default"), exist_ok=True)
        return d

    def status(self, identity: str) -> dict:
        """Return profile status for the given identity."""
        return {
            "identity": identity,
            "profile_dir": self.profile_dir(identity),
            "cdp_port": self.cdp_port(identity),
            "login_url": self.login_url(identity),
        }
