"""PWA preview guards and emitted deployment configuration; no live deployment."""
import configparser
import os
from pathlib import Path
import re
import subprocess

import pytest

from scripts.check_mobile_reader_e2e import configure
from scripts.preview_pwa import run, validate_named_tunnel, write_guest_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("minutes", [-1, 0, 241])
def test_public_preview_rejects_unbounded_lifetime(minutes):
    with pytest.raises(ValueError, match="lifetime"):
        run(minutes)


@pytest.mark.parametrize("hostname", ["https://preview.example.com", "*.example.com", "example.com/path", "-bad.example.com"])
def test_named_preview_rejects_non_exact_hosts(tmp_path, hostname):
    credentials = tmp_path / "tunnel.json"
    credentials.touch()
    with pytest.raises(ValueError, match="exact DNS hostname"):
        validate_named_tunnel(hostname, "preview", credentials)


def test_named_preview_requires_complete_explicit_configuration(tmp_path):
    with pytest.raises(ValueError, match="supplied together"):
        validate_named_tunnel("preview.example.com", None, None)
    credentials = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="does not exist"):
        validate_named_tunnel("preview.example.com", "preview", credentials)
    credentials.touch()
    validate_named_tunnel("preview.example.com", "preview", credentials)
    validate_named_tunnel(None, None, None)


def test_guest_proxy_configuration_is_private_and_preview_only(tmp_path):
    config = write_guest_config(tmp_path, 'session="test"')
    assert config.parent == tmp_path
    assert config.stat().st_mode & 0o777 == 0o600
    assert "preview:" in config.read_text()
    assert "'/api':" in config.read_text()
    assert 'session=\\"test\\"' in config.read_text()
    assert "cookie" not in (ROOT / "frontend/vite.config.js").read_text()


def test_sandbox_signing_keys_are_random_per_run(tmp_path):
    keys = []
    for name in ("first", "second"):
        directory = tmp_path / name
        directory.mkdir()
        configure(directory, 19001)
        parser = configparser.ConfigParser()
        parser.read(directory / "backend.ini")
        keys.append(parser["auth"]["secret"])
    assert keys[0] != keys[1]
    assert all(len(key) >= 40 for key in keys)


def assert_pwa_locations(config):
    for path in ("sw.js", "manifest.webmanifest"):
        match = re.search(r"location = /" + re.escape(path) + r" \{([\s\S]+?)\n    \}", config)
        assert match, path
        assert "expires -1;" in match[1]
        assert "try_files $uri =404;" in match[1]
        assert "add_header" not in match[1]  # keep inherited security headers
    assert "types { application/manifest+json webmanifest; }" in config


@pytest.mark.parametrize("ssl,redirect", [("false", "false"), ("true", "false"), ("true", "true")])
def test_deploy_renders_pwa_locations_in_all_modes(tmp_path, ssl, redirect):
    source = (ROOT / "deploy.sh").read_text()
    # Execute the real writer only. Stub machine paths/inclusion; never source the deployment entry.
    function = source[source.index("write_nginx_site_config() {"):source.index("resolve_nginx_main_conf() {")]
    cert = tmp_path / "test.pem"
    cert.touch()
    destination = tmp_path / "site.conf"
    script = f'''set -eu
truthy() {{ [ "$1" = true ]; }}
fail() {{ echo "$*" >&2; exit 1; }}
resolve_nginx_site_file() {{ NGINX_SITE_FILE="$OUTPUT"; NGINX_SITE_ENABLED_FILE="$OUTPUT"; }}
ensure_site_included() {{ :; }}
{function}
write_nginx_site_config 127.0.0.1 8088
'''
    env = {**os.environ, "OUTPUT": str(destination), "SUDO": "", "NGINX_ENABLE_SSL": ssl,
           "NGINX_SSL_REDIRECT": redirect, "NGINX_SERVER_NAME": "reader.example", "NGINX_ENABLE_HSTS": "true",
           "NGINX_SSL_CERT_FILE": str(cert), "NGINX_SSL_KEY_FILE": str(cert), "NGINX_LISTEN_PORT": "80",
           "NGINX_SSL_LISTEN_PORT": "443", "NGINX_HTML_DIR": "/tmp/site"}
    subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True, text=True)
    assert_pwa_locations(destination.read_text())


def test_docker_configuration_and_negative_guard():
    config = (ROOT / "docker/nginx.conf").read_text()
    assert_pwa_locations(config)
    for broken in (config.replace("try_files $uri =404;", "try_files $uri /index.html;"),
                   config.replace("expires -1;", "expires 1y;")):
        with pytest.raises(AssertionError):
            assert_pwa_locations(broken)
