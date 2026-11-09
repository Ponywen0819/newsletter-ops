"""Cloudflare Access 的 JWT 驗證：縱深防禦，Access 政策被誤刪或改錯時不會整站公開。

設了 NEWSLETTER_ACCESS_TEAM（team 名稱，如 myteam 或 myteam.cloudflareaccess.com）與 NEWSLETTER_ACCESS_AUD
（Access 應用程式的 Application Audience tag）才啟用：非本機的請求都要帶有效的 Cf-Access-Jwt-Assertion（RS256、aud、iss、exp），
否則 403。公鑰從 <team>/cdn-cgi/access/certs 取得並快取；取不到就拒絕（fail closed）。
"""
from __future__ import annotations

import os
from typing import Callable

import jwt

TEAM_ENV = "NEWSLETTER_ACCESS_TEAM"
AUD_ENV = "NEWSLETTER_ACCESS_AUD"
HEADER = "Cf-Access-Jwt-Assertion"


class Access:
    def __init__(self, team: str, aud: str, key_for: Callable[[str], object] | None = None):
        team = team.removeprefix("https://").removesuffix("/").removesuffix(".cloudflareaccess.com")
        self.issuer = f"https://{team}.cloudflareaccess.com"
        self.aud = aud
        if key_for is None:
            client = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
            key_for = client.get_signing_key_from_jwt
        self.key_for = key_for

    def ok(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            key = self.key_for(token)
            jwt.decode(token, getattr(key, "key", key), algorithms=["RS256"], audience=self.aud, issuer=self.issuer)
        except Exception:
            return False
        return True


def from_env() -> Access | None:
    team, aud = os.environ.get(TEAM_ENV, "").strip(), os.environ.get(AUD_ENV, "").strip()
    return Access(team, aud) if team and aud else None
