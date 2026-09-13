from __future__ import annotations

import pytest


class FakeWindowsVault:
    __module__ = "keyring.backends.Windows"

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, secret: str) -> None:
        self.values[(service, username)] = secret

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_windows_credential_adapter_uses_profile_scoped_target() -> None:
    from wechat_ai.credential_store import (
        MODEL_PROFILE_CREDENTIAL_SERVICE,
        KeyringCredentialStore,
    )

    backend = FakeWindowsVault()
    store = KeyringCredentialStore(
        backend=backend,
        platform_name="win32",
    )

    store.set("profile-alpha", "synthetic-secret")

    assert backend.values == {
        (
            MODEL_PROFILE_CREDENTIAL_SERVICE,
            "profile-alpha",
        ): "synthetic-secret"
    }
    assert store.has("profile-alpha") is True
    assert store.get("profile-alpha") == "synthetic-secret"

    store.delete("profile-alpha")

    assert store.has("profile-alpha") is False


def test_wechat_credential_adapter_uses_database_scoped_windows_target() -> None:
    from wechat_ai.credential_store import (
        WECHAT_CREDENTIAL_USERNAME,
        KeyringTargetCredentialStore,
    )

    backend = FakeWindowsVault()
    store = KeyringTargetCredentialStore(
        backend=backend,
        platform_name="win32",
    )
    target = "Gchat/WeChat/message/message_0.db"

    store.set(target, "synthetic-wechat-key")

    assert backend.values == {
        (target, WECHAT_CREDENTIAL_USERNAME): "synthetic-wechat-key"
    }
    assert store.get(target) == "synthetic-wechat-key"
    store.delete(target)
    assert store.has(target) is False


@pytest.mark.parametrize(
    ("platform_name", "backend"),
    [
        ("linux", FakeWindowsVault()),
        ("win32", object()),
    ],
)
def test_credential_backend_unavailable_fails_closed(
    platform_name: str,
    backend: object,
) -> None:
    from wechat_ai.credential_store import (
        CredentialStoreUnavailable,
        KeyringCredentialStore,
    )

    with pytest.raises(CredentialStoreUnavailable):
        KeyringCredentialStore(
            backend=backend,
            platform_name=platform_name,
        )


def test_credential_adapter_sanitizes_backend_failures() -> None:
    from wechat_ai.credential_store import (
        CredentialStoreUnavailable,
        KeyringCredentialStore,
    )

    class FailingWindowsVault(FakeWindowsVault):
        __module__ = "keyring.backends.Windows"

        def get_password(self, service: str, username: str) -> str | None:
            raise RuntimeError("synthetic-secret backend details")

    store = KeyringCredentialStore(
        backend=FailingWindowsVault(),
        platform_name="win32",
    )

    with pytest.raises(
        CredentialStoreUnavailable,
        match="credential store is unavailable",
    ) as captured:
        store.get("profile-alpha")

    assert "synthetic-secret" not in str(captured.value)


def test_unavailable_fallback_is_constructible_and_fails_closed() -> None:
    from wechat_ai.credential_store import (
        CredentialStoreUnavailable,
        UnavailableCredentialStore,
        create_credential_store,
    )

    store = create_credential_store(
        backend=object(),
        platform_name="win32",
    )

    assert isinstance(store, UnavailableCredentialStore)
    with pytest.raises(CredentialStoreUnavailable):
        store.has("profile-alpha")
    with pytest.raises(CredentialStoreUnavailable):
        store.get("profile-alpha")
    with pytest.raises(CredentialStoreUnavailable):
        store.set("profile-alpha", "synthetic-secret")
    with pytest.raises(CredentialStoreUnavailable):
        store.delete("profile-alpha")
