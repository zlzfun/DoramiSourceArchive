"""Resolve the selected speech provider through the existing credential registry."""

from dataclasses import fields
import config
from config_bailian import BailianSpeechConfig
from services import credentials, aliyun_isi_config

_DEFAULTS = BailianSpeechConfig()
NAMESPACE = credentials.CredentialNamespace(
    name="bailian_speech",
    ini_section="bailian_speech",
    fields=tuple(
        credentials.CredentialField(
            f.name,
            f"bailian_speech_{f.name}",
            secret=f.name == "api_key",
            kind=(
                "bool"
                if isinstance(getattr(_DEFAULTS, f.name), bool)
                else (
                    "int"
                    if isinstance(getattr(_DEFAULTS, f.name), int)
                    else (
                        "csv"
                        if isinstance(getattr(_DEFAULTS, f.name), tuple)
                        else "str"
                    )
                )
            ),
            env_var=f"DORAMI_BAILIAN_{f.name.upper()}",
        )
        for f in fields(_DEFAULTS)
        if f.name != "enabled"
    ),
)


def resolve_bailian(session):
    return BailianSpeechConfig(
        enabled=config.settings.bailian_speech.enabled,
        **credentials.resolve_values(
            session, NAMESPACE, config.settings.bailian_speech
        ),
    )


def resolve_config(session):
    if config.settings.bailian_speech.enabled:
        return resolve_bailian(session)
    return aliyun_isi_config.resolve_config(session)


def namespace():
    return (
        NAMESPACE
        if config.settings.bailian_speech.enabled
        else credentials.ALIYUN_ISI_NAMESPACE
    )


def field_sources(session):
    return credentials.field_sources(session, namespace())
