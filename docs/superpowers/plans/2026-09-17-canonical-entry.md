# Canonical Source and Runtime Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for execution and review; do not spawn further subagents.

**Goal:** Stop agents opening different ChatOS UIs and establish reusable repository truth rules.
**Architecture:** Existing PATHS registry routes to repo AGENTS and native build/start configuration; promote approved UI to tracked formal source without rewriting its API adapter.
**Tech Stack:** Markdown, PowerShell, Node/TypeScript/Vite, FastAPI.
**Spec:** `docs/superpowers/specs/2026-09-17-canonical-entry-design.md`

## Global Constraints

- Preserve user changes and raw SQLite data. No push, no broad deletes, no unrelated processes killed.
- Formal UI source is apps/chatos-console. UI 3108, gateway API 8912; old gateway browser entry opens the same new UI. One start script.
- No visual redesign; preserve e9d78e9 UI. Prototype/simulated features must remain explicitly identified.
- Reuse current configs and governance tooling. No parallel global registry, no second generic framework.
- Every nontrivial change needs a runnable regression check; real runtime claims require live verification.

## Task 1: Promote ChatOS UI and unify entry

Ownership: ChatOS files only, except docs/superpowers and shared-governance staging used by controller. Work in current canonical checkout on task branch; source path correctness is the object of this migration, so no extra unregistered checkout. Baselines: root 972971162855c297c52b51ecb0a3aa85298275d5; prototype e9d78e9c3f1c216c668a4810fe890d764131b88a.

- [x] Preserve exact old frontend and prototype baselines with Git archives/bundles under ignored var/backups; report locations. Keep original prototype repo intact, mark it retired with forwarding README after integration.
- [x] Import only tracked source/config/assets/verification scripts from artifacts/chatos-ui-aistudio into apps/chatos-console. Exclude .git, node_modules, secrets, screenshots, runtime data. Archive superseded formal frontend files, don't leave duplicate active component trees.
- [x] Keep existing Express read-only adapter and UI. Correct DB config lookup for new location. Keep missing backend/prototype status honest. No new direct writes to raw tables. Mark the currently mixed real-conversation/demo-module scope visibly, without changing the visual design.
- [x] Test-first source/build/runtime identity and occupied-port rejection. Native package scripts build both UI/server plus build metadata with source-content identity, canonical relative source and origin commit; identity changes when source changes, not just timestamp. Expose runtime identity read-only, including source root/build identity and actual runtime code startup identity; avoid reading a replaced manifest and claiming old process is new.
- [x] Add a repo verification command (scripts/verify-entry.mjs, optionally --live), checking formal source Git tracking, build freshness/output identity and runtime contract; failures nonzero. Source checks should work with staged files before initial commit. Wire formal launcher to this gate.
- [x] Unify scripts/start_chatos.bat through the existing Node runtime: start required gateway and UI hidden, exact cwd/config, reuse only verified identical services, refuse unrelated/stale occupied ports with actionable error. Never kill unknown process. Replace existing prototype 3108 only after validating executable, cwd/source identity.
- [x] Gateway browser entry must stop serving the old UI and send users to canonical UI 3108, preserving API behavior and session protection. Reuse existing frontend plugin configuration; regression test API routes unaffected. No hardcoded external redirects or open redirect input.
- [x] Update repo README/AGENTS + console AGENTS, native config examples, startup docs so all active instructions agree. Historical evidence may remain identified as historical. Declare formal vs dev vs retired, build/start/verify/data/logs boundaries. No new overarching registry needed.
- [x] Run relevant regression checks, typecheck/build, live HTTP/assets identity, backend frontend/runtime targeted tests. Leave running services only if verified safe; browser review is controller responsibility. Existing CSP assertion failure is disclosed, not counted as passing.
- [x] Commit only Task 1 changes on task branch, report full tests/TDD evidence and limitations to .superpowers/sdd/2026-09-17-canonical-entry/task-1-report.md.

## Task 2: Shared standards (controller)

- [x] Extend PATHS.md with repository minimum source/build/runtime contract and promotion guardrails. Reuse AGENTS/readme/config, allow polyglot/monorepo/worktree differences; no uniform speculative folder scaffolding.
- [x] Reduce duplicated ChatOS runtime details in global index/architecture to repo-entry pointers, preserving unrelated content.
- [x] Extend existing verify-paths with optional repo-scoped check that invokes a declared repo validation entry safely and never scans or rewrites all repos by default. Add minimal behavior test for missing declarations/failure propagation.
- [x] Snapshot exact shared targets and use approved scoped patch; run audit/index/deploy plan and only needed canonical projection deployment. Preserve C:/AI unrelated staged modifications.

## Task 3: Review and evidence

- [x] Independent task review then whole-change review; fix load-bearing findings and rerun targeted checks. Final scoped re-review of a8901f7 closed the last finding.
- [ ] Browser visual acceptance blocked: canonical URL verified by live HTTP/assets checks; browser transport returns nodeRepl.fetch failure and opening is only queued. No visual/interaction pass claimed.
- [x] Record canonical source, launcher, shared rule link, commits, verification and remaining limits in docs/superpowers/2026-09-17-canonical-entry-verification.md.
