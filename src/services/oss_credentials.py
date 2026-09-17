"""Refresh ECS role credentials via IMDSv2, without proxying or logging secrets.

The endpoint is fixed: caller-supplied URLs and redirects are never accepted.
https://help.aliyun.com/zh/ecs/user-guide/view-instance-metadata
"""
from __future__ import annotations

import datetime as dt
import threading
import time

import httpx
from oss2.credentials import Credentials, CredentialsProvider


class EcsRoleCredentialsProvider(CredentialsProvider):
    def __init__(self, role_name: str, *, transport=None):
        self.role_name = role_name
        self.transport = transport
        self._lock = threading.Lock()
        self._credentials = None
        self._expires_at = 0.0

    def get_credentials(self):
        with self._lock:
            if self._credentials is not None and self._expires_at > time.time() + 300:
                return self._credentials
            try:
                with httpx.Client(timeout=5, trust_env=False, follow_redirects=False,
                                  transport=self.transport) as client:
                    base = "http://100.100.100.200/latest"
                    token = client.put(base + "/api/token", headers={
                        "X-aliyun-ecs-metadata-token-ttl-seconds": "60",
                    })
                    token.raise_for_status()
                    response = client.get(base + "/meta-data/ram/security-credentials/" + self.role_name,
                                          headers={"X-aliyun-ecs-metadata-token": token.text})
                    response.raise_for_status()
                    data = response.json()
                expires_at = dt.datetime.fromisoformat(data["Expiration"].replace("Z", "+00:00")).timestamp()
                if (data.get("Code") != "Success" or expires_at <= time.time() + 60
                        or not all(data.get(k) for k in ("AccessKeyId", "AccessKeySecret", "SecurityToken"))):
                    raise ValueError("invalid credentials")
                self._credentials = Credentials(data["AccessKeyId"], data["AccessKeySecret"], data["SecurityToken"])
                self._expires_at = expires_at
                return self._credentials
            except Exception:
                # No stale fallback, response body, token or credential in logs/errors.
                raise RuntimeError("object_storage_ecs_credentials_unavailable") from None
