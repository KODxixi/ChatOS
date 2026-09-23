"""The portable, product-level Gchat agent preset.

This prompt is intentionally independent from a particular user's names,
directories, ports, channels, or specialist-agent roster.  The host runtime
decides which optional capabilities are actually available.
"""

from __future__ import annotations

from .rag_prompt import LOCAL_WECHAT_RAG_SYSTEM_PROMPT, load_system_prompt

GCHAT_PRESET_ID = "gchat"
GCHAT_PRESET_VERSION = "1.2.0"

GCHAT_PRESET = load_system_prompt("gchat-preset").replace(
    "{{LOCAL_WECHAT_RAG_SYSTEM_PROMPT}}", LOCAL_WECHAT_RAG_SYSTEM_PROMPT
)


def get_gchat_preset() -> dict[str, object]:
    """Return a stable, exportable representation of the built-in preset."""

    return {
        "id": GCHAT_PRESET_ID,
        "name": "Gchat",
        "version": GCHAT_PRESET_VERSION,
        "format": "markdown",
        "content": GCHAT_PRESET,
        "capabilities": {
            "inbound_channel": "runtime_configured",
            "chatbox_api": "self_service_with_read_only_default",
            "api_self_service": [
                "health",
                "runtime",
                "diagnostics",
                "model-profiles",
                "jobs",
                "sources",
                "conversations",
                "messages",
                "search",
                "knowledge",
                "graph",
                "assets",
                "projects",
                "gchat/decisions",
                "gchat/memories",
                "gchat/preset",
                "gchat/project-assignment-candidates",
            ],
            "chatbox_import": "owner_command_only",
            "knowledge_write": "runtime_configured_local_only",
            "memory_write": "runtime_configured_local_only",
            "acp_dispatch": "explicit_owner_command_only",
            "external_messaging": "active_conversation_only",
            "direct_database": "approval_required",
            "system_configuration": "approval_required",
        },
    }
