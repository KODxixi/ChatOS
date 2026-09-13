from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..credential_store import (
    CredentialStore,
    CredentialStoreUnavailable,
)
from ..openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleUnavailable,
    validate_model_endpoint,
)


REMOTE_PRIVACY_POLICY_VERSION = "remote-model-v1"
DEEPSEEK_V4_FLASH_PRESET_ID = "deepseek-v4-flash"
DEEPSEEK_V4_FLASH_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_V4_FLASH_MODEL = "deepseek-v4-flash"

ProviderKind = Literal["lm-studio", "openai-compatible"]
AuthKind = Literal["none", "bearer"]
ProfileStatus = Literal["unknown", "ready", "offline"]
ConnectionFactory = Callable[[], sqlite3.Connection]
Clock = Callable[[], int]
IdFactory = Callable[[], str]


class ModelProfileError(RuntimeError):
    code = "MODEL_PROFILE_ERROR"


class ModelProfileNotFound(ModelProfileError):
    code = "MODEL_PROFILE_NOT_FOUND"


class ModelProfileValidationError(ModelProfileError):
    code = "MODEL_PROFILE_INVALID"


class RemotePrivacyAcknowledgementRequired(ModelProfileError):
    code = "REMOTE_PRIVACY_ACK_REQUIRED"


class ModelCredentialMissing(ModelProfileError):
    code = "MODEL_CREDENTIAL_MISSING"


class ModelProfileTestFailed(ModelProfileError):
    code = "MODEL_PROFILE_UNAVAILABLE"


class DefaultModelProfileDeleteForbidden(ModelProfileError):
    code = "DEFAULT_MODEL_PROFILE_DELETE_FORBIDDEN"


class ModelProfileInUse(ModelProfileError):
    code = "MODEL_PROFILE_IN_USE"


class ModelProfileDefaultNotReady(ModelProfileError):
    code = "MODEL_PROFILE_DEFAULT_NOT_READY"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    id: str
    name: str
    provider: ProviderKind
    base_url: str
    model: str | None
    auth_kind: AuthKind
    is_default: bool
    privacy_ack_version: str | None
    privacy_ack_fingerprint: str | None
    privacy_ack_at: int | None
    last_status: ProfileStatus
    last_checked_at: int | None
    created_at: int
    updated_at: int

    @property
    def endpoint(self):
        return validate_model_endpoint(self.base_url)

    def privacy_acknowledged(self) -> bool:
        if not self.endpoint.is_remote:
            return True
        return (
            self.privacy_ack_version == REMOTE_PRIVACY_POLICY_VERSION
            and self.privacy_ack_fingerprint
            == self.endpoint.privacy_fingerprint(
                REMOTE_PRIVACY_POLICY_VERSION
            )
        )


@dataclass(frozen=True, slots=True)
class ResolvedModelProfile:
    profile: ModelProfile
    credential: str | None


class ModelDiscoveryProvider(Protocol):
    def list_models(self) -> list[str]: ...


ProviderFactory = Callable[
    [ModelProfile, str | None],
    ModelDiscoveryProvider,
]


class ModelProfileService:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        credential_store: CredentialStore,
        *,
        provider_factory: ProviderFactory | None = None,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._credentials = credential_store
        self._provider_factory = (
            provider_factory or _default_provider_factory
        )
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def seed_default_local_profile(self, base_url: str) -> dict[str, object]:
        profiles = self._list_rows()
        if profiles:
            selected = next(
                (item for item in profiles if item.is_default),
                profiles[0],
            )
            return self._to_response(selected)
        return self._create_profile(
            profile_id="local-default",
            name="本地 LM Studio",
            provider="lm-studio",
            base_url=base_url,
            model=None,
            auth_kind="none",
            is_default=True,
        )

    def seed_default_deepseek_profile(self) -> dict[str, object]:
        """Ensure a DeepSeek profile exists without activating it prematurely."""
        existing = next(
            (
                item
                for item in self._list_rows()
                if _is_deepseek_v4_flash_profile(item)
            ),
            None,
        )
        if existing is not None:
            return self._to_response(existing)
        return self._create_profile(
            profile_id=DEEPSEEK_V4_FLASH_PRESET_ID,
            name="DeepSeek V4 Flash",
            provider="openai-compatible",
            base_url=DEEPSEEK_V4_FLASH_BASE_URL,
            model=DEEPSEEK_V4_FLASH_MODEL,
            auth_kind="bearer",
            is_default=False,
        )

    def list_profiles(self) -> list[dict[str, object]]:
        return [self._to_response(item) for item in self._list_rows()]

    def get_profile(self, profile_id: str) -> dict[str, object]:
        return self._to_response(self._get_row(profile_id))

    def create_profile(
        self,
        *,
        name: str,
        provider: ProviderKind,
        base_url: str,
        model: str | None,
        auth_kind: AuthKind | None = None,
        is_default: bool = False,
    ) -> dict[str, object]:
        resolved_auth = auth_kind or (
            "none" if provider == "lm-studio" else "bearer"
        )
        return self._create_profile(
            profile_id=self._id_factory(),
            name=name,
            provider=provider,
            base_url=base_url,
            model=model,
            auth_kind=resolved_auth,
            is_default=is_default,
        )

    def create_deepseek_v4_flash_preset(self) -> dict[str, object]:
        existing = next(
            (
                item
                for item in self._list_rows()
                if _is_deepseek_v4_flash_profile(item)
            ),
            None,
        )
        if existing is not None:
            return self._to_response(existing)
        return self._create_profile(
            profile_id=self._id_factory(),
            name="DeepSeek V4 Flash",
            provider="openai-compatible",
            base_url=DEEPSEEK_V4_FLASH_BASE_URL,
            model=DEEPSEEK_V4_FLASH_MODEL,
            auth_kind="bearer",
            is_default=False,
        )

    def update_profile(
        self,
        profile_id: str,
        updates: Mapping[str, object],
    ) -> dict[str, object]:
        if not updates:
            raise ModelProfileValidationError(
                "at least one profile field is required"
            )
        current = self._get_row(profile_id)
        allowed = {
            "name",
            "provider",
            "baseUrl",
            "model",
            "authKind",
            "isDefault",
        }
        if any(key not in allowed for key in updates):
            raise ModelProfileValidationError("profile field is invalid")

        name = updates.get("name", current.name)
        provider = updates.get("provider", current.provider)
        base_url = updates.get("baseUrl", current.base_url)
        model = updates.get("model", current.model)
        auth_kind = updates.get("authKind", current.auth_kind)
        is_default = updates.get("isDefault", current.is_default)
        normalized = _validate_profile_values(
            name=name,
            provider=provider,
            base_url=base_url,
            model=model,
            auth_kind=auth_kind,
            is_default=is_default,
        )
        endpoint_changed = normalized["base_url"] != current.base_url
        configuration_changed = any(
            (
                normalized["provider"] != current.provider,
                endpoint_changed,
                normalized["model"] != current.model,
                normalized["auth_kind"] != current.auth_kind,
            )
        )
        if bool(normalized["is_default"]) and _is_deepseek_v4_flash(
            provider=normalized["provider"],
            base_url=normalized["base_url"],
            model=normalized["model"],
        ):
            self._require_deepseek_default_readiness(
                current,
                configuration_changed=configuration_changed,
            )
        now = self._clock()

        with closing(self._connection_factory()) as connection:
            if bool(normalized["is_default"]):
                connection.execute(
                    "UPDATE model_profiles SET is_default = 0 "
                    "WHERE is_default = 1 AND id <> ?",
                    (current.id,),
                )
            connection.execute(
                """
                UPDATE model_profiles
                SET name = ?, provider = ?, base_url = ?, model = ?,
                    auth_kind = ?, is_default = ?,
                    privacy_ack_version = ?,
                    privacy_ack_fingerprint = ?,
                    privacy_ack_at = ?,
                    last_status = ?,
                    last_checked_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized["name"],
                    normalized["provider"],
                    normalized["base_url"],
                    normalized["model"],
                    normalized["auth_kind"],
                    int(bool(normalized["is_default"])),
                    (
                        None
                        if endpoint_changed
                        else current.privacy_ack_version
                    ),
                    (
                        None
                        if endpoint_changed
                        else current.privacy_ack_fingerprint
                    ),
                    None if endpoint_changed else current.privacy_ack_at,
                    "unknown" if configuration_changed else current.last_status,
                    (
                        None
                        if configuration_changed
                        else current.last_checked_at
                    ),
                    now,
                    current.id,
                ),
            )
            connection.commit()
        return self.get_profile(current.id)

    def set_credential(
        self,
        profile_id: str,
        secret: str,
    ) -> dict[str, object]:
        profile = self._get_row(profile_id)
        self._credentials.set(profile_id, secret)
        if _is_deepseek_v4_flash_profile(profile):
            now = self._clock()
            with closing(self._connection_factory()) as connection:
                connection.execute(
                    """
                    UPDATE model_profiles
                    SET is_default = 0, last_status = 'unknown',
                        last_checked_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, profile.id),
                )
                connection.commit()
        return self.get_profile(profile_id)

    def acknowledge_remote(
        self,
        profile_id: str,
        *,
        accepted: bool,
        policy_version: str,
    ) -> dict[str, object]:
        profile = self._get_row(profile_id)
        if (
            not accepted
            or policy_version != REMOTE_PRIVACY_POLICY_VERSION
            or not profile.endpoint.is_remote
        ):
            raise ModelProfileValidationError(
                "remote privacy acknowledgement is invalid"
            )
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            connection.execute(
                """
                UPDATE model_profiles
                SET privacy_ack_version = ?,
                    privacy_ack_fingerprint = ?,
                    privacy_ack_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    REMOTE_PRIVACY_POLICY_VERSION,
                    profile.endpoint.privacy_fingerprint(
                        REMOTE_PRIVACY_POLICY_VERSION
                    ),
                    now,
                    now,
                    profile.id,
                ),
            )
            connection.commit()
        return self.get_profile(profile.id)

    def agent_profile_metadata(
        self,
        profile_id: str | None,
    ) -> ModelProfile:
        return (
            self._get_row(profile_id)
            if profile_id is not None
            else self._default_row()
        )

    def preflight_for_agent(self, profile_id: str | None) -> ModelProfile:
        profile = self.agent_profile_metadata(profile_id)
        if profile.endpoint.is_remote and not profile.privacy_acknowledged():
            raise RemotePrivacyAcknowledgementRequired(
                "remote model privacy acknowledgement is required"
            )
        return profile

    def resolve_for_agent(self, profile_id: str | None) -> ResolvedModelProfile:
        profile = self.preflight_for_agent(profile_id)
        credential = None
        if profile.auth_kind == "bearer":
            try:
                credential = self._credentials.get(profile.id)
            except CredentialStoreUnavailable as exc:
                raise ModelCredentialMissing(
                    "model credential is unavailable"
                ) from exc
            if not credential:
                raise ModelCredentialMissing(
                    "model credential is required"
                )
        return ResolvedModelProfile(
            profile=profile,
            credential=credential,
        )

    def resolve_local_profile_for_background(
        self,
    ) -> ResolvedModelProfile | None:
        """Resolve a local profile without touching credentials for remotes."""

        profiles = self._list_rows()
        local_profiles = [
            profile
            for profile in profiles
            if not profile.endpoint.is_remote
        ]
        if not local_profiles:
            return None
        profile = next(
            (item for item in local_profiles if item.is_default),
            local_profiles[0],
        )
        credential = None
        if profile.auth_kind == "bearer":
            try:
                credential = self._credentials.get(profile.id)
            except CredentialStoreUnavailable as exc:
                raise ModelCredentialMissing(
                    "model credential is unavailable"
                ) from exc
            if not credential:
                raise ModelCredentialMissing(
                    "model credential is required"
                )
        return ResolvedModelProfile(
            profile=profile,
            credential=credential,
        )

    def test_profile(self, profile_id: str) -> dict[str, object]:
        resolved = self.resolve_for_agent(profile_id)
        checked_at = self._clock()
        try:
            models = self._provider_factory(
                resolved.profile,
                resolved.credential,
            ).list_models()
        except Exception as exc:
            self._record_status(profile_id, "offline", checked_at)
            raise ModelProfileTestFailed(
                "model profile is unavailable"
            ) from exc
        self._record_status(profile_id, "ready", checked_at)
        return {
            "profile": self.get_profile(profile_id),
            "models": models,
        }

    def delete_profile(self, profile_id: str) -> None:
        normalized_id = _required_text(profile_id, "profile id")
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM model_profiles WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ModelProfileNotFound("model profile was not found")
            profile = _profile_from_row(row)
            if profile.is_default:
                connection.rollback()
                raise DefaultModelProfileDeleteForbidden(
                    "default model profile cannot be deleted"
                )
            if _profile_has_agent_turns(connection, profile.id):
                connection.rollback()
                raise ModelProfileInUse("model profile is in use")
            try:
                deleted = connection.execute(
                    "DELETE FROM model_profiles WHERE id = ?",
                    (profile.id,),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ModelProfileInUse("model profile is in use") from exc
            if deleted.rowcount != 1:
                connection.rollback()
                raise ModelProfileNotFound("model profile was not found")
            credential = self._credentials.get(profile.id)
            try:
                if credential is not None:
                    self._credentials.delete(profile.id)
                connection.commit()
            except BaseException:
                connection.rollback()
                if credential is not None:
                    self._credentials.set(profile.id, credential)
                raise

    def _create_profile(
        self,
        *,
        profile_id: str,
        name: object,
        provider: object,
        base_url: object,
        model: object,
        auth_kind: object,
        is_default: object,
    ) -> dict[str, object]:
        normalized = _validate_profile_values(
            name=name,
            provider=provider,
            base_url=base_url,
            model=model,
            auth_kind=auth_kind,
            is_default=is_default,
        )
        if bool(normalized["is_default"]) and _is_deepseek_v4_flash(
            provider=normalized["provider"],
            base_url=normalized["base_url"],
            model=normalized["model"],
        ):
            raise ModelProfileDefaultNotReady(
                "DeepSeek preset must be configured and tested before default"
            )
        now = self._clock()
        with closing(self._connection_factory()) as connection:
            if bool(normalized["is_default"]):
                connection.execute(
                    "UPDATE model_profiles SET is_default = 0 "
                    "WHERE is_default = 1"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO model_profiles(
                        id, name, provider, base_url, model, auth_kind,
                        is_default, last_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?)
                    """,
                    (
                        profile_id,
                        normalized["name"],
                        normalized["provider"],
                        normalized["base_url"],
                        normalized["model"],
                        normalized["auth_kind"],
                        int(bool(normalized["is_default"])),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ModelProfileValidationError(
                    "model profile conflicts with existing data"
                ) from exc
            connection.commit()
        return self.get_profile(profile_id)

    def _record_status(
        self,
        profile_id: str,
        status: ProfileStatus,
        checked_at: int,
    ) -> None:
        with closing(self._connection_factory()) as connection:
            connection.execute(
                """
                UPDATE model_profiles
                SET last_status = ?, last_checked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, checked_at, checked_at, profile_id),
            )
            connection.commit()

    def _list_rows(self) -> list[ModelProfile]:
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT *
                FROM model_profiles
                ORDER BY is_default DESC, created_at ASC, id ASC
                """
            ).fetchall()
        return [_profile_from_row(row) for row in rows]

    def _get_row(self, profile_id: str) -> ModelProfile:
        normalized_id = _required_text(profile_id, "profile id")
        with closing(self._connection_factory()) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM model_profiles WHERE id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise ModelProfileNotFound("model profile was not found")
        return _profile_from_row(row)

    def _default_row(self) -> ModelProfile:
        profiles = self._list_rows()
        if not profiles:
            raise ModelProfileNotFound("default model profile was not found")
        return next(
            (item for item in profiles if item.is_default),
            profiles[0],
        )

    def _to_response(self, profile: ModelProfile) -> dict[str, object]:
        try:
            has_credential = self._credentials.has(profile.id)
        except CredentialStoreUnavailable:
            has_credential = False
        response: dict[str, object] = {
            "id": profile.id,
            "name": profile.name,
            "provider": profile.provider,
            "baseUrl": profile.base_url,
            "model": profile.model,
            "authKind": profile.auth_kind,
            "isDefault": profile.is_default,
            "isRemote": profile.endpoint.is_remote,
            "hasCredential": has_credential,
            "privacyAcknowledged": profile.privacy_acknowledged(),
            "status": profile.last_status,
            "lastCheckedAt": profile.last_checked_at,
            "createdAt": profile.created_at,
            "updatedAt": profile.updated_at,
        }
        if _is_deepseek_v4_flash_profile(profile):
            response.update(
                {
                    "presetId": DEEPSEEK_V4_FLASH_PRESET_ID,
                    "reasoningMode": "auto",
                }
            )
        return response

    def _require_deepseek_default_readiness(
        self,
        profile: ModelProfile,
        *,
        configuration_changed: bool,
    ) -> None:
        try:
            has_credential = self._credentials.has(profile.id)
        except CredentialStoreUnavailable:
            has_credential = False
        if (
            configuration_changed
            or not has_credential
            or not profile.privacy_acknowledged()
            or profile.last_status != "ready"
        ):
            raise ModelProfileDefaultNotReady(
                "DeepSeek preset must be configured and tested before default"
            )


def _validate_profile_values(
    *,
    name: object,
    provider: object,
    base_url: object,
    model: object,
    auth_kind: object,
    is_default: object,
) -> dict[str, object]:
    normalized_name = _required_text(name, "profile name")
    if provider not in {"lm-studio", "openai-compatible"}:
        raise ModelProfileValidationError("model provider is invalid")
    if not isinstance(base_url, str):
        raise ModelProfileValidationError("model endpoint is invalid")
    try:
        endpoint = validate_model_endpoint(base_url)
    except ValueError as exc:
        raise ModelProfileValidationError(
            "model endpoint is invalid"
        ) from exc
    if provider == "lm-studio" and endpoint.is_remote:
        raise ModelProfileValidationError(
            "LM Studio profile must use a local endpoint"
        )
    if auth_kind not in {"none", "bearer"}:
        raise ModelProfileValidationError(
            "model authentication kind is invalid"
        )
    if model is not None and not isinstance(model, str):
        raise ModelProfileValidationError("model name is invalid")
    normalized_model = model.strip() if isinstance(model, str) else None
    if normalized_model == "":
        normalized_model = None
    if not isinstance(is_default, bool):
        raise ModelProfileValidationError("default profile flag is invalid")
    return {
        "name": normalized_name,
        "provider": provider,
        "base_url": endpoint.base_url,
        "model": normalized_model,
        "auth_kind": auth_kind,
        "is_default": is_default,
    }


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelProfileValidationError(f"{label} must not be blank")
    return value.strip()


def is_deepseek_v4_flash_profile(profile: ModelProfile) -> bool:
    return _is_deepseek_v4_flash(
        provider=profile.provider,
        base_url=profile.base_url,
        model=profile.model,
    )


_is_deepseek_v4_flash_profile = is_deepseek_v4_flash_profile


def _is_deepseek_v4_flash(
    *,
    provider: object,
    base_url: object,
    model: object,
) -> bool:
    return (
        provider == "openai-compatible"
        and base_url == DEEPSEEK_V4_FLASH_BASE_URL
        and model == DEEPSEEK_V4_FLASH_MODEL
    )


def _profile_has_agent_turns(
    connection: sqlite3.Connection,
    profile_id: str,
) -> bool:
    if connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'agent_turns'
        """
    ).fetchone() is None:
        return False
    return connection.execute(
        """
        SELECT 1
        FROM agent_turns
        WHERE model_profile_id = ?
        LIMIT 1
        """,
        (profile_id,),
    ).fetchone() is not None


def _profile_from_row(row: sqlite3.Row) -> ModelProfile:
    return ModelProfile(
        id=str(row["id"]),
        name=str(row["name"]),
        provider=row["provider"],
        base_url=str(row["base_url"]),
        model=str(row["model"]) if row["model"] is not None else None,
        auth_kind=row["auth_kind"],
        is_default=bool(row["is_default"]),
        privacy_ack_version=(
            str(row["privacy_ack_version"])
            if row["privacy_ack_version"] is not None
            else None
        ),
        privacy_ack_fingerprint=(
            str(row["privacy_ack_fingerprint"])
            if row["privacy_ack_fingerprint"] is not None
            else None
        ),
        privacy_ack_at=(
            int(row["privacy_ack_at"])
            if row["privacy_ack_at"] is not None
            else None
        ),
        last_status=row["last_status"],
        last_checked_at=(
            int(row["last_checked_at"])
            if row["last_checked_at"] is not None
            else None
        ),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _default_provider_factory(
    profile: ModelProfile,
    credential: str | None,
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        profile.base_url,
        model=profile.model,
        credential=credential,
    )
