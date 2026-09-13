"""Credentials plugin: Windows Credential Manager store for WeChat keys.

The model-profile credential store is an override seam (tests inject a fake)
and is consumed by the ``models`` plugin; the WeChat source key store is
always created locally.
"""

from __future__ import annotations


def apply(ctx) -> None:
    from ..credential_store import create_wechat_credential_store

    ctx.provide("credentials.wechat", create_wechat_credential_store())
