"""Models plugin: model profiles, default status and the profiles API."""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.model_profiles import create_model_profiles_router
    from ..credential_store import create_credential_store
    from ..services.model_profile_service import ModelProfileService

    credential_store = (
        ctx.override("credentials.store") or create_credential_store()
    )
    model_profile_service = ModelProfileService(
        ctx.get("db.connection_factory"),
        credential_store,
    )
    ctx.provide("models.service", model_profile_service)
    if ctx.get("db.initialized"):
        model_profile_service.seed_default_deepseek_profile()
    ctx.provide("models.default_status", _default_model_status(model_profile_service))
    ctx.add_router(
        "/api",
        create_model_profiles_router(model_profile_service),
    )


def _default_model_status(model_profile_service) -> object:
    def default_model_status() -> dict[str, object]:
        profiles = model_profile_service.list_profiles()
        profile = next(
            (
                item
                for item in profiles
                if item.get("isDefault") is True
                and item.get("model") == "deepseek-v4-flash"
            ),
            None,
        )
        if profile is None:
            return {
                "profileId": None,
                "ready": False,
                "privacyConfirmed": False,
            }
        return {
            "profileId": profile.get("id"),
            "ready": profile.get("status") == "ready",
            "privacyConfirmed": profile.get("privacyAcknowledged") is True,
        }

    return default_model_status
