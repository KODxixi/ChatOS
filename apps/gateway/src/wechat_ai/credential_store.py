from __future__ import annotations

import sys
from typing import Never, Protocol, runtime_checkable


MODEL_PROFILE_CREDENTIAL_SERVICE = "wechat-ai-local:model-profiles"
WECHAT_CREDENTIAL_USERNAME = "Gchat"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStoreUnavailable(CredentialStoreError):
    pass


@runtime_checkable
class CredentialStore(Protocol):
    def has(self, profile_id: str) -> bool: ...

    def get(self, profile_id: str) -> str | None: ...

    def set(self, profile_id: str, value: str) -> None: ...

    def delete(self, profile_id: str) -> None: ...

class UnavailableCredentialStore:
    def has(self, profile_id: str) -> bool:
        self._unavailable(profile_id)

    def get(self, profile_id: str) -> str | None:
        self._unavailable(profile_id)

    def set(self, profile_id: str, value: str) -> None:
        del value
        self._unavailable(profile_id)

    def delete(self, profile_id: str) -> None:
        self._unavailable(profile_id)

    @staticmethod
    def _unavailable(profile_id: str) -> Never:
        _profile_id(profile_id)
        raise CredentialStoreUnavailable(
            "Windows credential store is unavailable."
        )


class _KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(
        self,
        service: str,
        username: str,
        password: str,
    ) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class KeyringCredentialStore:
    def __init__(
        self,
        *,
        backend: object | None = None,
        platform_name: str | None = None,
    ) -> None:
        platform = platform_name or sys.platform
        resolved_backend = backend if backend is not None else _load_keyring_backend()
        if platform != "win32" or not _is_windows_backend(resolved_backend):
            raise CredentialStoreUnavailable(
                "Windows credential store is unavailable."
            )
        self._backend = resolved_backend

    def has(self, profile_id: str) -> bool:
        return self.get(profile_id) is not None

    def get(self, profile_id: str) -> str | None:
        normalized_id = _profile_id(profile_id)
        try:
            value = self._backend.get_password(
                MODEL_PROFILE_CREDENTIAL_SERVICE,
                normalized_id,
            )
        except Exception as exc:
            raise CredentialStoreUnavailable(
                "Windows credential store is unavailable."
            ) from exc
        return value if isinstance(value, str) and value else None

    def set(self, profile_id: str, value: str) -> None:
        normalized_id = _profile_id(profile_id)
        if not isinstance(value, str) or not value:
            raise ValueError("credential must be a non-empty string")
        try:
            self._backend.set_password(
                MODEL_PROFILE_CREDENTIAL_SERVICE,
                normalized_id,
                value,
            )
        except Exception as exc:
            raise CredentialStoreUnavailable(
                "Windows credential store is unavailable."
            ) from exc

    def delete(self, profile_id: str) -> None:
        normalized_id = _profile_id(profile_id)
        try:
            self._backend.delete_password(
                MODEL_PROFILE_CREDENTIAL_SERVICE,
                normalized_id,
            )
        except Exception as exc:
            raise CredentialStoreUnavailable(
                "Windows credential store is unavailable."
            ) from exc


class KeyringTargetCredentialStore(KeyringCredentialStore):
    """Stores each identifier as the Windows Credential Manager target."""

    def get(self, profile_id: str) -> str | None:
        target = _profile_id(profile_id)
        try:
            value = self._backend.get_password(
                target,
                WECHAT_CREDENTIAL_USERNAME,
            )
        except Exception as exc:
            raise CredentialStoreUnavailable(
                "Windows credential store is unavailable."
            ) from exc
        return value if isinstance(value, str) and value else None

    def set(self, profile_id: str, value: str) -> None:
        target = _profile_id(profile_id)
        if not isinstance(value, str) or not value:
            raise ValueError("credential must be a non-empty string")
        try:
            self._backend.set_password(
                target,
                WECHAT_CREDENTIAL_USERNAME,
                value,
            )
        except Exception as exc:
            raise CredentialStoreUnavailable(
                "Windows credential store is unavailable."
            ) from exc

    def delete(self, profile_id: str) -> None:
        target = _profile_id(profile_id)
        try:
            self._backend.delete_password(
                target,
                WECHAT_CREDENTIAL_USERNAME,
            )
        except Exception as exc:
            raise CredentialStoreUnavailable(
                "Windows credential store is unavailable."
            ) from exc

def create_credential_store(
    *,
    backend: object | None = None,
    platform_name: str | None = None,
) -> CredentialStore:
    try:
        return KeyringCredentialStore(
            backend=backend,
            platform_name=platform_name,
        )
    except CredentialStoreUnavailable:
        return UnavailableCredentialStore()


def create_wechat_credential_store(
    *,
    backend: object | None = None,
    platform_name: str | None = None,
) -> CredentialStore:
    try:
        return KeyringTargetCredentialStore(
            backend=backend,
            platform_name=platform_name,
        )
    except CredentialStoreUnavailable:
        return UnavailableCredentialStore()



def _load_keyring_backend() -> object:
    try:
        import keyring
    except ImportError as exc:
        raise CredentialStoreUnavailable(
            "Windows credential store is unavailable."
        ) from exc
    try:
        return keyring.get_keyring()
    except Exception as exc:
        raise CredentialStoreUnavailable(
            "Windows credential store is unavailable."
        ) from exc


def _is_windows_backend(backend: object) -> bool:
    module_name = type(backend).__module__.casefold()
    return module_name.startswith("keyring.backends.windows")


def _profile_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("profile id must be a non-empty string")
    return value.strip()
