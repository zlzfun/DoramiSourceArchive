"""Optional private database/paid-speech backups, independent of media OSS."""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field, fields


@dataclass(frozen=True)
class BackupConfig:
    enabled: bool = False
    destination: str = "local"
    interval_hours: int = 24
    local_dir: str = "data/backups"
    retain_local: int = 7
    minimum_free_mb: int = 1024
    bucket: str = ""
    region: str = ""
    endpoint: str = ""
    prefix: str = "backups"
    credential_provider: str = "static"
    ecs_role_name: str = ""
    access_key_id: str = field(default="", repr=False)
    access_key_secret: str = field(default="", repr=False)
    security_token: str = field(default="", repr=False)
    timeout_seconds: int = 60

    def __post_init__(self):
        if not self.enabled:
            return
        if self.destination not in {"local", "oss"}:
            raise ValueError("Backup destination must be local or oss")
        if self.interval_hours < 1 or self.retain_local < 1 or self.minimum_free_mb < 0:
            raise ValueError("Invalid backup interval, retention or reserve")
        if not self.local_dir.strip() or self.timeout_seconds < 1:
            raise ValueError("Backup staging directory and positive timeout required")
        if self.destination == "oss":
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket):
                raise ValueError("Backup OSS bucket required")
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", self.region):
                raise ValueError("Backup OSS region required")
            if self.endpoint not in {f"https://oss-{self.region}.aliyuncs.com",
                                    f"https://oss-{self.region}-internal.aliyuncs.com"}:
                raise ValueError("Backup OSS endpoint must be HTTPS and match region")
            # Separate top-level namespace; cannot accidentally use prod/media or podcast.
            if not re.fullmatch(r"backups(?:/[A-Za-z0-9_-]+)*", self.prefix):
                raise ValueError("Backup prefix must start with backups")
            if self.credential_provider not in {"static", "ecs_role"}:
                raise ValueError("Backup credential provider must be static or ecs_role")
            if self.credential_provider == "ecs_role" and not re.fullmatch(r"[A-Za-z0-9.@_-]{1,64}", self.ecs_role_name):
                raise ValueError("Backup ECS role name required")
            if self.credential_provider == "static" and (not self.access_key_id or not self.access_key_secret):
                raise ValueError("Separate backup credentials must be injected via environment")


def load_backup_config(parser: configparser.ConfigParser) -> BackupConfig:
    def read(name, default):
        raw = os.getenv(f"DORAMI_BACKUP_{name.upper()}", "").strip()
        return raw or parser.get("backup", name, fallback=str(default)).strip()

    enabled = read("enabled", False).lower()
    if enabled not in configparser.ConfigParser.BOOLEAN_STATES:
        raise ValueError("Invalid backup enabled flag")
    if not configparser.ConfigParser.BOOLEAN_STATES[enabled]:
        # Disabled means no parsing/validation/dependency on unrelated cloud settings.
        return BackupConfig()
    defaults = BackupConfig()
    values = {"enabled": True}
    for item in fields(defaults):
        if item.name == "enabled":
            continue
        default = getattr(defaults, item.name)
        if item.name in {"access_key_id", "access_key_secret", "security_token"}:
            values[item.name] = os.getenv(f"DORAMI_BACKUP_{item.name.upper()}", "").strip()
        elif item.name in {"bucket", "region", "endpoint", "prefix"} and read("destination", "local") == "local":
            values[item.name] = default
        else:
            raw = read(item.name, default)
            values[item.name] = int(raw) if isinstance(default, int) else raw
    return BackupConfig(**values)
