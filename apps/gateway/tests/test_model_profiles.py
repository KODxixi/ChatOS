from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _create_profile_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE model_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                base_url TEXT NOT NULL,
                model TEXT,
                auth_kind TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                privacy_ack_version TEXT,
                privacy_ack_fingerprint TEXT,
                privacy_ack_at INTEGER,
                last_status TEXT NOT NULL DEFAULT 'unknown',
                last_checked_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX model_profiles_one_default
            ON model_profiles(is_default)
            WHERE is_default = 1;
            """
        )
    finally:
        connection.close()


class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    def has(self, profile_id: str) -> bool:
        return profile_id in self.values

    def get(self, profile_id: str) -> str | None:
        self.get_calls.append(profile_id)
        return self.values.get(profile_id)

    def set(self, profile_id: str, value: str) -> None:
        self.values[profile_id] = value

    def delete(self, profile_id: str) -> None:
        self.delete_calls.append(profile_id)
        self.values.pop(profile_id, None)


def _make_service(
    path: Path,
    *,
    credentials: FakeCredentialStore | None = None,
    provider_factory=None,
):
    from wechat_ai.services.model_profile_service import ModelProfileService

    return ModelProfileService(
        lambda: sqlite3.connect(path),
        credentials or FakeCredentialStore(),
        provider_factory=provider_factory,
        clock=lambda: 1_700_000_000_000,
        id_factory=lambda: "profile-generated",
    )


def test_default_local_profile_is_seeded_from_existing_lm_base_url(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    service = _make_service(database_path)

    profile = service.seed_default_local_profile(
        "http://127.0.0.1:1234/v1"
    )

    assert profile == {
        "id": "local-default",
        "name": "本地 LM Studio",
        "provider": "lm-studio",
        "baseUrl": "http://127.0.0.1:1234/v1",
        "model": None,
        "authKind": "none",
        "isDefault": True,
        "isRemote": False,
        "hasCredential": False,
        "privacyAcknowledged": True,
        "status": "unknown",
        "lastCheckedAt": None,
        "createdAt": 1_700_000_000_000,
        "updatedAt": 1_700_000_000_000,
    }
    assert service.seed_default_local_profile(
        "http://127.0.0.1:9999/v1"
    ) == profile


def test_default_deepseek_profile_is_seeded_without_local_model_profile(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.model_profile_service import (
        DEEPSEEK_V4_FLASH_BASE_URL,
        DEEPSEEK_V4_FLASH_MODEL,
    )

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    service = _make_service(database_path)

    profile = service.seed_default_deepseek_profile()

    assert profile["id"] == "deepseek-v4-flash"
    assert profile["name"] == "DeepSeek V4 Flash"
    assert profile["provider"] == "openai-compatible"
    assert profile["baseUrl"] == DEEPSEEK_V4_FLASH_BASE_URL
    assert profile["model"] == DEEPSEEK_V4_FLASH_MODEL
    assert profile["isDefault"] is False
    assert profile["hasCredential"] is False
    assert service.list_profiles() == [profile]


def test_remote_profile_requires_current_privacy_ack_before_credential_read(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
        RemotePrivacyAcknowledgementRequired,
    )

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    credentials.values["profile-generated"] = "synthetic-secret"
    service = _make_service(database_path, credentials=credentials)
    profile = service.create_profile(
        name="Remote model",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )

    with pytest.raises(RemotePrivacyAcknowledgementRequired):
        service.resolve_for_agent(profile["id"])

    assert credentials.get_calls == []

    acknowledged = service.acknowledge_remote(
        profile["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )
    resolved = service.resolve_for_agent(profile["id"])

    assert acknowledged["privacyAcknowledged"] is True
    assert resolved.profile.id == profile["id"]
    assert resolved.credential == "synthetic-secret"
    assert credentials.get_calls == [profile["id"]]


def test_background_local_resolver_ignores_remote_default_and_never_reads_it(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    connection = sqlite3.connect(database_path)
    try:
        connection.executemany(
            """
            INSERT INTO model_profiles(
                id, name, provider, base_url, model, auth_kind, is_default,
                privacy_ack_version, privacy_ack_fingerprint, privacy_ack_at,
                last_status, last_checked_at, created_at, updated_at
            ) VALUES (?, ?, 'openai-compatible', ?, 'model', ?, ?,
                      NULL, NULL, NULL, 'unknown', NULL, ?, ?)
            """,
            (
                (
                    "remote-default",
                    "Remote",
                    "https://models.example/v1",
                    "bearer",
                    1,
                    1,
                    1,
                ),
                (
                    "local-secondary",
                    "Local",
                    "http://127.0.0.1:1234/v1",
                    "none",
                    0,
                    2,
                    2,
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    service = _make_service(database_path, credentials=credentials)

    resolved = service.resolve_local_profile_for_background()

    assert resolved is not None
    assert resolved.profile.id == "local-secondary"
    assert resolved.credential is None
    assert credentials.get_calls == []


def test_remote_endpoint_change_invalidates_existing_privacy_ack(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
        RemotePrivacyAcknowledgementRequired,
    )

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    credentials.values["profile-generated"] = "synthetic-secret"
    service = _make_service(database_path, credentials=credentials)
    profile = service.create_profile(
        name="Remote model",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )
    service.acknowledge_remote(
        profile["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )

    updated = service.update_profile(
        profile["id"],
        {"baseUrl": "https://other.example/v1"},
    )

    assert updated["privacyAcknowledged"] is False
    with pytest.raises(RemotePrivacyAcknowledgementRequired):
        service.resolve_for_agent(profile["id"])
    assert credentials.get_calls == []


def test_profile_credential_is_stored_outside_profile_and_never_serialized(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    service = _make_service(database_path, credentials=credentials)
    profile = service.create_profile(
        name="Local compatible",
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model="local-model",
        auth_kind="bearer",
    )

    result = service.set_credential(profile["id"], "synthetic-secret")

    assert credentials.values == {profile["id"]: "synthetic-secret"}
    assert result["hasCredential"] is True
    assert "synthetic-secret" not in str(result)
    connection = sqlite3.connect(database_path)
    try:
        assert "synthetic-secret" not in str(
            connection.execute("SELECT * FROM model_profiles").fetchall()
        )
    finally:
        connection.close()


def test_explicit_profile_test_updates_cached_status_and_returns_models(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    observed_profiles: list[str] = []

    class Provider:
        def list_models(self) -> list[str]:
            return ["model-a", "model-b"]

    def provider_factory(profile, credential):
        observed_profiles.append(profile.id)
        assert credential is None
        return Provider()

    service = _make_service(
        database_path,
        provider_factory=provider_factory,
    )
    profile = service.create_profile(
        name="Local model",
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model=None,
        auth_kind="none",
    )

    result = service.test_profile(profile["id"])

    assert observed_profiles == [profile["id"]]
    assert result["models"] == ["model-a", "model-b"]
    assert result["profile"]["status"] == "ready"
    assert result["profile"]["lastCheckedAt"] == 1_700_000_000_000


def test_model_profile_api_crud_and_mutations_return_safe_dtos(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.model_profiles import create_model_profiles_router
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()

    class Provider:
        def list_models(self) -> list[str]:
            return ["remote-model"]

    service = _make_service(
        database_path,
        credentials=credentials,
        provider_factory=lambda _profile, _credential: Provider(),
    )
    app = FastAPI()
    app.include_router(
        create_model_profiles_router(service),
        prefix="/api",
    )
    client = TestClient(app)

    created = client.post(
        "/api/model-profiles",
        json={
            "name": "Remote",
            "provider": "openai-compatible",
            "baseUrl": "https://models.example/v1",
            "model": "remote-model",
            "authKind": "bearer",
            "isDefault": True,
        },
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert client.get("/api/model-profiles").json() == {
        "items": [created.json()]
    }

    patched = client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"name": "Remote renamed"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Remote renamed"

    credential = client.put(
        f"/api/model-profiles/{profile_id}/credential",
        json={"secret": "synthetic-secret"},
    )
    assert credential.status_code == 200
    assert credential.json()["hasCredential"] is True
    assert "synthetic-secret" not in credential.text

    acknowledged = client.post(
        f"/api/model-profiles/{profile_id}/acknowledge-remote",
        json={
            "accepted": True,
            "policyVersion": REMOTE_PRIVACY_POLICY_VERSION,
        },
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["privacyAcknowledged"] is True

    tested = client.post(f"/api/model-profiles/{profile_id}/test")
    assert tested.status_code == 200
    assert tested.json()["models"] == ["remote-model"]
    assert tested.json()["profile"]["status"] == "ready"


def test_deepseek_v4_flash_preset_requires_full_readiness_before_default(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.model_profiles import create_model_profiles_router
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "deepseek-preset.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()

    class Provider:
        def list_models(self) -> list[str]:
            return ["deepseek-v4-flash"]

    service = _make_service(
        database_path,
        credentials=credentials,
        provider_factory=lambda _profile, _credential: Provider(),
    )
    app = FastAPI()
    app.include_router(
        create_model_profiles_router(service),
        prefix="/api",
    )
    client = TestClient(app)

    created = client.post(
        "/api/model-profiles/presets/deepseek-v4-flash"
    )

    assert created.status_code == 201
    assert created.json() == {
        "id": "profile-generated",
        "name": "DeepSeek V4 Flash",
        "provider": "openai-compatible",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "authKind": "bearer",
        "isDefault": False,
        "isRemote": True,
        "hasCredential": False,
        "privacyAcknowledged": False,
        "status": "unknown",
        "lastCheckedAt": None,
        "createdAt": 1_700_000_000_000,
        "updatedAt": 1_700_000_000_000,
        "presetId": "deepseek-v4-flash",
        "reasoningMode": "auto",
    }
    profile_id = created.json()["id"]

    not_ready = client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"isDefault": True},
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == (
        "MODEL_PROFILE_DEFAULT_NOT_READY"
    )

    assert client.put(
        f"/api/model-profiles/{profile_id}/credential",
        json={"secret": "synthetic-secret"},
    ).status_code == 200
    assert client.post(
        f"/api/model-profiles/{profile_id}/acknowledge-remote",
        json={
            "accepted": True,
            "policyVersion": REMOTE_PRIVACY_POLICY_VERSION,
        },
    ).status_code == 200

    still_not_tested = client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"isDefault": True},
    )
    assert still_not_tested.status_code == 409
    assert client.post(
        f"/api/model-profiles/{profile_id}/test"
    ).status_code == 200

    selected = client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"isDefault": True},
    )
    assert selected.status_code == 200
    assert selected.json()["isDefault"] is True


def test_model_profile_api_returns_stable_remote_privacy_error(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.model_profiles import create_model_profiles_router

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    service = _make_service(database_path)
    profile = service.create_profile(
        name="Remote",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="none",
    )
    app = FastAPI()
    app.include_router(
        create_model_profiles_router(service),
        prefix="/api",
    )

    response = TestClient(app).post(
        f"/api/model-profiles/{profile['id']}/test"
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "REMOTE_PRIVACY_ACK_REQUIRED",
            "message": "需要先确认远程模型隐私提示。",
            "retryable": False,
            "details": {},
        }
    }


def test_deleting_non_default_profile_also_deletes_credential(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.model_profile_service import ModelProfileNotFound

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    service = _make_service(database_path, credentials=credentials)
    service.seed_default_local_profile("http://127.0.0.1:1234/v1")
    profile = service.create_profile(
        name="Secondary",
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model="local-model",
        auth_kind="bearer",
    )
    service.set_credential(profile["id"], "synthetic-secret")

    service.delete_profile(profile["id"])

    assert profile["id"] not in credentials.values
    with pytest.raises(ModelProfileNotFound):
        service.get_profile(profile["id"])


def test_deleting_profile_without_secret_is_idempotent(tmp_path: Path) -> None:
    from wechat_ai.services.model_profile_service import ModelProfileNotFound

    class StrictCredentialStore(FakeCredentialStore):
        def delete(self, profile_id: str) -> None:
            self.delete_calls.append(profile_id)
            if profile_id not in self.values:
                raise RuntimeError("secret does not exist")
            del self.values[profile_id]

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = StrictCredentialStore()
    service = _make_service(database_path, credentials=credentials)
    service.seed_default_local_profile("http://127.0.0.1:1234/v1")
    profile = service.create_profile(
        name="No secret",
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model=None,
        auth_kind="none",
    )

    service.delete_profile(profile["id"])

    assert credentials.delete_calls == []
    with pytest.raises(ModelProfileNotFound):
        service.get_profile(profile["id"])


def test_profile_in_use_is_rejected_before_secret_deletion(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.model_profile_service import ModelProfileInUse

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    service = _make_service(database_path, credentials=credentials)
    service.seed_default_local_profile("http://127.0.0.1:1234/v1")
    profile = service.create_profile(
        name="Used profile",
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model="local-model",
        auth_kind="bearer",
    )
    service.set_credential(profile["id"], "synthetic-secret")
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE agent_turns (
                id TEXT PRIMARY KEY,
                model_profile_id TEXT REFERENCES model_profiles(id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO agent_turns(id, model_profile_id)
            VALUES ('turn-used', ?)
            """,
            (profile["id"],),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ModelProfileInUse):
        service.delete_profile(profile["id"])

    assert credentials.values[profile["id"]] == "synthetic-secret"
    assert credentials.delete_calls == []
    assert service.get_profile(profile["id"])["id"] == profile["id"]


def test_database_delete_failure_never_removes_secret(tmp_path: Path) -> None:
    from wechat_ai.services.model_profile_service import ModelProfileInUse

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    service = _make_service(database_path, credentials=credentials)
    service.seed_default_local_profile("http://127.0.0.1:1234/v1")
    profile = service.create_profile(
        name="Blocked delete",
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model="local-model",
        auth_kind="bearer",
    )
    service.set_credential(profile["id"], "synthetic-secret")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER block_profile_delete
            BEFORE DELETE ON model_profiles
            BEGIN
                SELECT RAISE(FAIL, 'profile delete blocked');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ModelProfileInUse):
        service.delete_profile(profile["id"])

    assert credentials.values[profile["id"]] == "synthetic-secret"
    assert credentials.delete_calls == []
    assert service.get_profile(profile["id"])["id"] == profile["id"]


def test_default_profile_cannot_be_deleted(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.model_profile_service import (
        DefaultModelProfileDeleteForbidden,
    )

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    service = _make_service(database_path)
    profile = service.seed_default_local_profile(
        "http://127.0.0.1:1234/v1"
    )

    with pytest.raises(DefaultModelProfileDeleteForbidden):
        service.delete_profile(profile["id"])

    assert service.get_profile(profile["id"])["isDefault"] is True


def test_model_profile_delete_api_returns_no_content(
    tmp_path: Path,
) -> None:
    from wechat_ai.api.model_profiles import create_model_profiles_router

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    service = _make_service(database_path)
    service.seed_default_local_profile("http://127.0.0.1:1234/v1")
    profile = service.create_profile(
        name="Secondary",
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model=None,
        auth_kind="none",
    )
    app = FastAPI()
    app.include_router(create_model_profiles_router(service), prefix="/api")

    response = TestClient(app).delete(
        f"/api/model-profiles/{profile['id']}"
    )

    assert response.status_code == 204
    assert response.content == b""


def test_model_profile_delete_api_reports_in_use_conflict() -> None:
    from wechat_ai.api.model_profiles import create_model_profiles_router
    from wechat_ai.services.model_profile_service import ModelProfileInUse

    class InUseService:
        def delete_profile(self, _profile_id: str) -> None:
            raise ModelProfileInUse("model profile is in use")

    app = FastAPI()
    app.include_router(
        create_model_profiles_router(InUseService()),  # type: ignore[arg-type]
        prefix="/api",
    )

    response = TestClient(app).delete("/api/model-profiles/profile-used")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "MODEL_PROFILE_IN_USE",
        "message": "模型配置仍被 Agent 记录使用，不能删除。",
        "retryable": False,
        "details": {},
    }


def test_agent_preflight_checks_remote_ack_without_reading_credential(
    tmp_path: Path,
) -> None:
    from wechat_ai.services.model_profile_service import (
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    credentials = FakeCredentialStore()
    credentials.values["profile-generated"] = "synthetic-secret"
    service = _make_service(database_path, credentials=credentials)
    profile = service.create_profile(
        name="Remote model",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )
    service.acknowledge_remote(
        profile["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )

    metadata = service.preflight_for_agent(profile["id"])

    assert metadata.id == profile["id"]
    assert metadata.endpoint.is_remote is True
    assert credentials.get_calls == []


def test_unavailable_credential_store_keeps_local_profiles_listable(
    tmp_path: Path,
) -> None:
    from wechat_ai.credential_store import UnavailableCredentialStore
    from wechat_ai.services.model_profile_service import (
        ModelCredentialMissing,
        REMOTE_PRIVACY_POLICY_VERSION,
    )

    database_path = tmp_path / "profiles.sqlite"
    _create_profile_database(database_path)
    service = _make_service(
        database_path,
        credentials=UnavailableCredentialStore(),
    )
    local = service.seed_default_local_profile(
        "http://127.0.0.1:1234/v1"
    )
    assert local["hasCredential"] is False
    assert service.list_profiles() == [local]

    remote = service.create_profile(
        name="Remote",
        provider="openai-compatible",
        base_url="https://models.example/v1",
        model="remote-model",
        auth_kind="bearer",
    )
    service.acknowledge_remote(
        remote["id"],
        accepted=True,
        policy_version=REMOTE_PRIVACY_POLICY_VERSION,
    )
    with pytest.raises(ModelCredentialMissing):
        service.resolve_for_agent(remote["id"])
