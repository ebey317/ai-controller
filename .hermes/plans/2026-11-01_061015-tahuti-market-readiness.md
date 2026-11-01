# Tahuti Market-Readiness Implementation Plan

> For Hermes: use subagent-driven-development task-by-task, with spec review then quality review. Do not alter controller mappings.

Goal: turn the real Tahuti desktop approval flow into one stable, auditable, safely executable production path suitable for private beta, then prove it with repeatable tests.

Architecture: The shipping UI remains `/home/elijah/master-ai-cli/ninja/avatar_approval.py` plus its `ninja/state_machine.py` overlay. `/home/elijah/ai-controller/scripts/tahuti_goal_runner.py` remains the execution runner. The AI Controller duplicate GTK approval modules become thin adapters or are retired; they do not own a second production window. Shared grants and receipts live in AI Controller because the runner owns desktop actions.

Tech stack: Python 3.10, GTK3, systemd user services, pytest, Broadway GTK test display.

---

### Task 1: Remove unsafe persistent grants and gate custom pins

Objective: eliminate background bypasses for destructive verbs and make operator-defined shell pins require an explicit Tahuti decision.

Files:
- Modify: `/home/elijah/ai-controller/scripts/tahuti_goal_runner.py`
- Modify/delete data only after backup and review: `~/.tahuti/grants.json`
- Test: `/home/elijah/ai-controller/tests/test_tahuti_goal_runner.py`

Steps:
1. Add failing tests showing `tahuti:delete:background` and `tahuti:install:background` never bypass approval solely because a broad stored grant exists.
2. Add failing test for a named custom pin: deny must prevent its command from running.
3. Narrow persistent grant matching to immutable, typed actions; block persistent grants for destructive verbs unless a future explicit per-target schema exists.
4. Make custom pins use a dedicated approval pattern containing the pin name before launch.
5. Run focused tests, then all runner tests.

Acceptance:
- A denied delete/install request creates no mutation.
- A denied custom pin creates no side effect.
- Built-in visible app opening retains its existing intended behavior.

### Task 2: Establish one approval UI and one verdict contract

Objective: all production approvals use the operator-approved avatar overlay and have consistent once/session/always meanings.

Files:
- Modify: `/home/elijah/master-ai-cli/ninja/avatar_approval.py`
- Modify: `/home/elijah/master-ai-cli/ninja/state_machine.py` only if needed for verdict/UI plumbing
- Create: `/home/elijah/ai-controller/scripts/tahuti_grants.py`
- Modify: `/home/elijah/ai-controller/scripts/tahuti_goal_runner.py`
- Modify: `/home/elijah/ai-controller/scripts/tahuti_desktop.py`
- Modify/retire: `/home/elijah/ai-controller/scripts/tahuti_avatar.py`, `/home/elijah/ai-controller/scripts/tahuti_approval_ui.py`
- Test: `/home/elijah/ai-controller/tests/test_tahuti_avatar_box.py`, `/home/elijah/ai-controller/tests/test_tahuti_desktop.py`, new grant tests

Steps:
1. Write tests defining exactly: `once` affects one request; `session` expires on process restart; `always` persists only for the exact typed pattern.
2. Make `avatar_approval.py` return only `once`, `session`, `always`, or `deny`.
3. Implement one file-backed grant store at `~/.tahuti/grants.json` with atomic writes and restrictive permissions. Session grants stay in memory.
4. Make both the runner and CUA bridge use that store and the same avatar overlay adapter.
5. Turn local AI Controller GTK approval code into a compatibility adapter to the overlay or delete it only after its callers and tests are migrated.
6. Keep exactly one top-level visible approval surface. Do not change the visual card Lijah approved.

Acceptance:
- Both a goal-runner approval and a CUA request show the same approved card.
- Once re-prompts; session expires after restart; always survives restart for only its exact pattern.
- No production path invokes the retired standalone worker or a competing window.

### Task 3: Add auditable failure-safe receipts

Objective: every approval produces a redacted, machine-readable outcome record rather than silently returning deny.

Files:
- Create: `/home/elijah/ai-controller/scripts/tahuti_receipts.py`
- Modify: `/home/elijah/ai-controller/scripts/tahuti_goal_runner.py`
- Modify: `/home/elijah/ai-controller/scripts/tahuti_desktop.py`
- Test: new `/home/elijah/ai-controller/tests/test_tahuti_receipts.py`

Steps:
1. Write tests for yes, deny, timeout, no-display, and UI exception outcomes.
2. Add atomic JSON receipt writes under `~/.tahuti/receipts/` with timestamp, request ID, caller, typed pattern, redacted description, verdict/reason, duration, and execution result.
3. Retain existing resolved files for backward compatibility, but point them to the receipt ID.
4. Replace broad exception swallowing in the overlay bridge with logged diagnostic reason plus fail-closed verdict.

Acceptance:
- Every decision has one receipt.
- No secret, raw command output, or full model prompt appears in a receipt.
- UI failures deny safely and are distinguishable from a user Deny.

### Task 4: Constrain desktop execution and fix runner lifecycle

Objective: remove arbitrary model-influenced shell parsing and make avatar/runner work bounded and recoverable.

Files:
- Modify: `/home/elijah/ai-controller/scripts/tahuti_desktop.py`
- Modify: `/home/elijah/ai-controller/scripts/tahuti_mcp.py`
- Modify: `/home/elijah/ai-controller/scripts/tahuti_goal_runner.py`
- Test: `/home/elijah/ai-controller/tests/test_tahuti_desktop.py`, `/home/elijah/ai-controller/tests/test_tahuti_goal_runner.py`

Steps:
1. Write failing tests rejecting shell operators, substitutions, redirects, pipes, and destructive binaries.
2. Replace `shell=True` with parsed argv and a small read-only probe allowlist. Return a clear refusal for unsupported execution.
3. Add a model tool-turn maximum and controlled exhaustion result.
4. Serialize caption writes with a lock and atomic replace.
5. Replace unconditional pulse `SIGKILL` with SIGTERM, bounded wait, then SIGKILL only as final cleanup.
6. Add tests for every bounded failure path.

Acceptance:
- No `shell=True` remains in the CUA desktop executor.
- Infinite model tool calls stop at a tested ceiling.
- Parallel captions remain valid JSON.
- Stopping the avatar leaves no zombie process.

### Task 5: Repair service startup and create a release test gate

Objective: make first-run/service behavior reliable, portable enough for beta, and provable without manual detective work.

Files:
- Modify: `/home/elijah/ai-controller/systemd/ai-controller-goal-runner.service`
- Create or modify: `/home/elijah/scripts/goal_runner_env.sh`
- Test: new `/home/elijah/ai-controller/tests/test_goal_runner_env.sh`
- Test: new `/home/elijah/ai-controller/tests/test_tahuti_headless_smoke.py`
- Test: new `/home/elijah/ai-controller/tests/test_tahuti_e2e.py`
- Update: `/home/elijah/ai-controller/AGENTS.md`, `TASKS.md`, and README where applicable

Steps:
1. Write tests for a systemd-compatible generated env file: no `export` prefixes, restricted permissions, known key allowlist, deterministic precedence.
2. Make the unit use that generated env file rather than loading shell syntax directly.
3. Resolve the capability-sandbox failure without weakening unrelated controller services; verify the unit file with `systemd-analyze --user verify`.
4. Add a headless smoke test and an end-to-end temp-directory test covering goal creation, approval stub, action outcome, receipt, archive, and TTS stub.
5. Preserve manual enablement as the default for beta; document the optional enable-on-login step rather than silently enabling startup.
6. Add a release command/runbook that runs all tests, Broadway tests when installed, unit verification, and a real inert approval callback.

Acceptance:
- Three consecutive runner restarts produce no invalid-environment warnings or crash loop.
- Clean headless tests pass without a display.
- Broadway test proves the real overlay remains one visible window.
- Repository unit matches the installed unit after explicit sync.

### Task 6: Private-beta release audit

Objective: validate the product on its actual desktop path before public sale.

Files:
- Update: `/home/elijah/ai-controller/README.md` or a new beta runbook

Steps:
1. Perform a clean-user install rehearsal.
2. Validate controller → voice → goal → real card → each verdict → receipt → visible result.
3. Verify recovery after lost display, lost network, model 400/503, and service restart.
4. Generate a redacted support bundle and write the operator troubleshooting flow.
5. Only then recruit 5–10 private-beta users and track setup success, approval comprehension, crashes, accidental-action rate, and recovery time.

Release gates:
- One production card: Master AI avatar overlay.
- No broad destructive persistent grants or unapproved custom-pin launch.
- No arbitrary shell execution from the model path.
- Every approval decision and execution outcome has a redacted receipt.
- Full test suite runs in project-owned environments; no dependency-collection failure.
- Runner survives three restarts and a reboot rehearsal.
- Clean install and E2E checklist succeed without developer intervention.
