"""Deployment-only configuration for durable media objects (independent of ASR)."""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field, fields


@dataclass(frozen=True)
class OssConfig:
    media_backend: str = "local"
    podcast_backend: str = "local"
    bucket: str = ""
    region: str = ""
    endpoint: str = ""
    prefix: str = ""
    credential_provider: str = "static"
    ecs_role_name: str = ""
    access_key_id: str = field(default="", repr=False)
    access_key_secret: str = field(default="", repr=False)
    security_token: str = field(default="", repr=False)
    timeout_seconds: int = 30
    minimum_free_mb: int = 1024

    def __post_init__(self):
        if self.media_backend not in {"local", "oss"} or self.podcast_backend not in {"local", "oss"}:
            raise ValueError("OSS storage backend must be local or oss")
        if self.timeout_seconds <= 0 or self.minimum_free_mb < 0:
            raise ValueError("Invalid OSS timeout or disk reserve")
        if self.credential_provider not in {"static", "ecs_role"}:
            raise ValueError("OSS credential provider must be static or ecs_role")
        if self.enabled:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket):
                raise ValueError("OSS bucket is required")
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", self.region):
                raise ValueError("OSS region is required")
            if self.endpoint not in {
                f"https://oss-{self.region}.aliyuncs.com",
                f"https://oss-{self.region}-internal.aliyuncs.com",
            }:
                raise ValueError("OSS endpoint must be HTTPS and match its region")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", self.prefix):
                raise ValueError("OSS prefix must identify this environment and writer")
            if self.credential_provider == "ecs_role" and not re.fullmatch(r"[A-Za-z0-9.@_-]{1,64}", self.ecs_role_name):
                raise ValueError("OSS ECS role name is required")
            if self.credential_provider == "static" and (not self.access_key_id or not self.access_key_secret):
                raise ValueError("OSS credentials must be injected via environment")

    @property
    def enabled(self) -> bool:
        return "oss" in {self.media_backend, self.podcast_backend}

    def backend(self, namespace: str) -> str:
        return self.media_backend if namespace == "media" else self.podcast_backend


def load_oss_config(parser: configparser.ConfigParser) -> OssConfig:
    defaults = OssConfig()
    values = {}
    secrets = {"access_key_id", "access_key_secret", "security_token"}
    for item in fields(defaults):
        default = getattr(defaults, item.name)
        raw = os.getenv(f"DORAMI_OSS_{item.name.upper()}")
        if raw is None or not raw.strip():
            # Credentials never come from tracked INI examples or application KV.
            raw = "" if item.name in secrets else parser.get("oss", item.name, fallback=str(default))
        values[item.name] = int(raw) if isinstance(default, int) else raw.strip()
    return OssConfig(**values)
