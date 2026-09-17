"""Round-3 todo state PATCH contract (Agent A).

Guards the PATCH /api/tasks/{id} status enum so the console kanban can persist
all four UI states (draft / pending / in_progress / completed) without the
gateway silently collapsing them back to ``todo`` — which is exactly what broke
the save -> reload round-trip before.

Historical contract preserved:
  * ``done`` stays accepted as an alias for completed (old rows use it).
  * ``ignored`` stays accepted as a deliberate "hide this item" choice.
  * unknown / garbage values stay rejected (a UI typo must not persist).

This exercises the real :class:`TaskPatch` pydantic model and the real
``TASK_STATUSES`` frozenset from ``wechat_ai.api.tasks``.  No database, no HTTP
server, no LLM are touched.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wechat_ai.api.tasks import TASK_STATUSES, TaskPatch


# The four console kanban states must each be a persistable gateway status, so a
# user setting a todo to draft / pending / in_progress / completed survives a
# save + reload and maps back to the SAME frontend state.
@pytest.mark.parametrize(
    "gateway_status",
    ["draft", "pending", "in_progress", "completed"],
)
def test_patch_accepts_kanban_states(gateway_status: str) -> None:
    assert gateway_status in TASK_STATUSES, (
        f"{gateway_status!r} must be a legal task status so the UI round-trips"
    )
    model = TaskPatch(status=gateway_status)
    assert model.status == gateway_status


def test_legacy_raw_lifecycle_still_accepted() -> None:
    # Raw extraction lifecycle + historical rows.
    for s in ("todo", "completed", "done", "ignored"):
        assert s in TASK_STATUSES
        assert TaskPatch(status=s).status == s


@pytest.mark.parametrize(
    "bad",
    ["bogus", "urgent", "in-progress", "donez", "completed "],
)
def test_patch_rejects_unknown_status(bad: str) -> None:
    with pytest.raises(ValidationError):
        TaskPatch(status=bad)
