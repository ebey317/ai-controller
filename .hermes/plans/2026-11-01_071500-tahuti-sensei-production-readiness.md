# Tahuti–Sensei Production-Readiness Implementation Plan

> **For Hermes:** Use subagent-driven-development task-by-task. Build in `/home/elijah/projects/ai-controller`; sync only verified files to `/home/elijah/ai-controller`. Master AI changes land in `/home/elijah/master-ai-cli`. Do not change controller mappings.

**Goal:** Make controller-triggered voice requests produce exactly one owned response, bridge Tahuti to Master AI Sensei through an explicit contract, serialize speech, repair the test environment, and prove the complete production path with repeatable tests.

**Architecture:** PTT becomes the ingress and assigns a request ID. The voice bridge classifies and transcribes but does not independently answer requests handed to Tahuti. Tahuti becomes the sole owner of action requests and requests Sensei through an explicit adapter rather than launching a terminal as an integration substitute. A single speech broker owns queueing, priority, cancellation, active voice selection, and playback.

**Tech stack:** Python 3.10, FastAPI, systemd user services, GTK3 overlay, pytest, Master AI Sensei HTTP bridge on `127.0.0.1:8791`.

**Release definition:** private-beta ready only after every release gate in Task 8 passes. Public/paid launch remains blocked until the beta runbook succeeds with real operator acceptance tests.

---

### Task 0: Protect current work and establish the test baseline

**Objective:** Preserve unrelated in-progress edits and make the active test environment reproducible before behavior changes.

**Files:**
- Inspect only before approval: `/home/elijah/projects/ai-controller/requirements.txt`, `pyproject.toml`, `.venv/`
- Modify: the project dependency lock/declarations only if the declared environment cannot produce the pinned runtime
- Test: `/home/elijah/projects/ai-controller/tests/conftest.py` only if needed to remove accidental interpreter leakage

**Steps:**
1. Record `git diff --name-only` and preserve unrelated changes; do not overwrite `README.md`, hero assets, avatar work, or Master AI overlay motion work.
2. Reproduce the `pydantic_core._pydantic_core` failure using the project Python.
3. Identify why Python 3.10 is importing packages from the Hermes Python 3.14 environment.
4. Correct environment isolation through the declared project dependency mechanism; do not install unpinned packages ad hoc.
5. Add a regression probe that fails when project Python imports dependencies outside its own `.venv`.

**Acceptance:**
- `ai-controller/.venv/bin/python -c 'import fastapi, pydantic_core'` succeeds.
- `pytest tests/test_voice_bridge.py -q` collects and runs.
- No unrelated uncommitted files are modified or deleted.

### Task 1: Define the request envelope and one response owner

**Objective:** Establish a typed request contract so one spoken input has one accountable execution and response owner.

**Files:**
- Create: `/home/elijah/projects/ai-controller/scripts/tahuti_request.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/ptt_pynput.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_bridge.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/tahuti_goal_runner.py`
- Create: `/home/elijah/projects/ai-controller/tests/test_tahuti_request.py`
- Modify: `/home/elijah/projects/ai-controller/tests/test_ptt_notify.py`
- Modify: `/home/elijah/projects/ai-controller/tests/test_voice_bridge.py`
- Modify: `/home/elijah/projects/ai-controller/tests/test_tahuti_goal_runner.py`

**Steps:**
1. Write failing tests for immutable `request_id`, transcript, source, route, response owner, created timestamp, and parent/correlation fields.
2. Generate one request ID at PTT capture completion and carry it through bridge output and any goal JSON.
3. Define routes explicitly: `dictation`, `conversation`, `action`, and `system`.
4. Require exactly one response owner: direct bridge/Sensei for conversation; Tahuti runner for action; none for plain dictation.
5. Reject malformed or duplicated request IDs fail-closed without speech.

**Acceptance:**
- Every persisted goal and receipt has a request ID.
- A request can be traced from PTT to final outcome using one ID.
- Tests prove no route has more than one response owner.

### Task 2: Stop duplicate voice responses at the ingress

**Objective:** Ensure a voice conversation is never answered by both the bridge and Tahuti.

**Files:**
- Modify: `/home/elijah/projects/ai-controller/scripts/ptt_pynput.py:1336-1400`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_bridge.py:558-655`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_goal_schema.py`
- Test: `/home/elijah/projects/ai-controller/tests/test_voice_bridge.py`
- Create: `/home/elijah/projects/ai-controller/tests/test_voice_request_e2e.py`

**Steps:**
1. Write a failing end-to-end test proving a non-task conversation does not create a Tahuti goal after the bridge/Sensei responds.
2. Write a failing test proving an action request does not receive a full conversational answer from the voice bridge before Tahuti owns it.
3. Change PTT to enqueue only `action` routes, not every agent take with a response.
4. Change the bridge’s action route to return structured accepted/queued data without unowned TTS; any spoken acknowledgment must be issued by the speech broker as one explicit low-priority event.
5. Preserve duplicate protection for bridge-created task goals, but replace boolean `routed_task` with the request envelope and owner contract.

**Acceptance:**
- One conversation produces one model call and one final spoken reply.
- One action produces one queued job and one final spoken outcome; acknowledgment behavior is explicit and tested.
- Replaying the same request ID does not create another goal or another spoken answer.

### Task 3: Build one serialized Tahuti speech broker

**Objective:** Prevent overlapping voices and remove direct uncontrolled TTS calls from the bridge and runner.

**Files:**
- Create: `/home/elijah/projects/ai-controller/scripts/tahuti_speech.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_bridge.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/tahuti_goal_runner.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/ptt_pynput.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/tts_control.py`
- Test: `/home/elijah/projects/ai-controller/tests/test_tahuti_speech.py`

**Steps:**
1. Write failing tests for FIFO normal speech, priority interruption, deduplication by request ID, cancellation/barge-in, and no concurrent player spawn.
2. Implement one local broker with a worker queue and explicit priorities: interrupt, approval, final result, acknowledgment.
3. Read the configured Tahuti/AI Controller voice profile on every utterance; do not hardcode Sarah, Lessac, Aria, or espeak as hidden alternatives.
4. Make bridge `/speak` a compatibility entry point that enqueues through the broker; runner and PTT must also enqueue rather than call TTS directly.
5. Replace automatic `spd-say` fallback with a logged failed speech receipt; only configured quality voices may speak.
6. Reap child playback processes and test no zombie remains after playback completion/cancellation.

**Acceptance:**
- Two simultaneous final responses are serialized, not mixed.
- RT barge-in cancels current queued/playback speech and no canceled utterance starts later.
- Every spoken utterance has a request ID, source, priority, and outcome receipt.

### Task 4: Implement the real Tahuti ↔ Sensei bridge

**Objective:** Replace terminal-launch-as-integration with an explicit local API adapter while preserving a visible “open Sensei” app command.

**Files:**
- Create: `/home/elijah/projects/ai-controller/scripts/tahuti_sensei_client.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/tahuti_goal_runner.py`
- Modify: `/home/elijah/master-ai-cli/sensei_bridge.py`
- Modify: `/home/elijah/master-ai-cli/sensei_mcp_server.py` only if a shared endpoint adapter is needed
- Test: `/home/elijah/projects/ai-controller/tests/test_tahuti_sensei_client.py`
- Test: `/home/elijah/master-ai-cli/tests/test_sensei_bridge_tahuti.py`

**Steps:**
1. Write contract tests for authenticated localhost `POST /agent/chat` or a new narrow `/tahuti/execute` endpoint: request ID, transcript, route, allowed context, reply, action proposals, and structured failure.
2. Use the existing Sensei bridge as the orchestration interface for conversational/agent reasoning. Do not send browser or desktop actions directly without Tahuti’s policy/approval gate.
3. Preserve `open Sensei` as an explicit GUI-launch command only; it must not masquerade as API integration.
4. Add bounded timeouts, status/model/route logging, and a deterministic fallback policy.
5. Propagate request ID to Sensei audit records and Tahuti receipts.

**Acceptance:**
- Tahuti action flow reaches Sensei through a real request, not a terminal window.
- Sensei response carries the same request ID back to Tahuti.
- Sensei cannot bypass Tahuti approval policy for desktop actions.
- Lost Sensei bridge fails closed with one spoken error, not a duplicate fallback answer.

### Task 5: Consolidate approval and outcome receipts

**Objective:** Make voice, Sensei, Tahuti, and approval outcomes auditable and unambiguous.

**Files:**
- Create or complete: `/home/elijah/projects/ai-controller/scripts/tahuti_receipts.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/tahuti_goal_runner.py`
- Modify: `/home/elijah/master-ai-cli/ninja/avatar_approval.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/tahuti_desktop.py`
- Test: `/home/elijah/projects/ai-controller/tests/test_tahuti_receipts.py`

**Steps:**
1. Test receipts for accepted, denied, timeout, UI error, Sensei unavailable, speech canceled, action succeeded, and action failed.
2. Record request ID, owner, route, approval scope, redacted effect, model/worker, timestamps, and final status atomically.
3. Keep close/timeout/UI errors fail-closed and distinguish them from operator Deny.
4. Reference approval and speech receipt IDs from archived goals.

**Acceptance:**
- Every request has one terminal receipt chain.
- No raw secrets, raw model prompts, or unsafe command payloads are written to receipts.
- A support operator can distinguish denial, failure, cancellation, and completion.

### Task 6: Retire competing voice surfaces and harden service ownership

**Objective:** Leave one authoritative live TTS path and eliminate unnecessary exposed/stale services.

**Files:**
- Modify: `/home/elijah/.config/systemd/user/voice-bridge.service` only after source tests pass
- Modify: `/home/elijah/.config/systemd/user/master-ai-tts.service` only after explicit compatibility verification
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_bridge.py`
- Modify: `/home/elijah/master-ai-cli/tts_server.py` if retained
- Update: `/home/elijah/projects/ai-controller/README.md`

**Steps:**
1. Inventory all live callers of ports 8002 and 5050 before disabling or rebinding any service.
2. Make the speech broker the only owner for Tahuti/controller speech.
3. Either retire `master-ai-tts.service` after proving no caller needs it, or bind it localhost with an explicit authenticated compatibility-only contract. Do not leave it exposed on `0.0.0.0` without a justification.
4. Remove automatic espeak/speech-dispatcher fallback from Tahuti paths.
5. Verify systemd units use only valid environment files and survive three controlled restarts.

**Acceptance:**
- There is one authoritative Tahuti speech owner.
- No accidental external listener on port 5050.
- No duplicated TTS player processes after a speech test.
- All service restarts have zero warnings and zero restart-loop increments.

### Task 7: Complete safety, packaging, and test gates

**Objective:** Make the build reproducible and safe enough for private beta.

**Files:**
- Modify: `/home/elijah/projects/ai-controller/scripts/tahuti_desktop.py`
- Modify: `/home/elijah/projects/ai-controller/systemd/ai-controller-goal-runner.service`
- Modify/create: `/home/elijah/projects/ai-controller/tests/test_tahuti_e2e.py`
- Modify/create: `/home/elijah/projects/ai-controller/tests/test_tahuti_headless_smoke.py`
- Update: `/home/elijah/projects/ai-controller/README.md`, `TASKS.md`, and operator runbook

**Steps:**
1. Remove or constrain model-influenced `shell=True` behavior behind typed validated commands.
2. Verify production approval uses the actual Master AI avatar overlay, not duplicate local GTK paths.
3. Add E2E tests for controller request → route → Sensei/Tahuti owner → approval → speech receipt → action result.
4. Add failure tests for lost display, unavailable Sensei, unavailable model, malformed goal, canceled TTS, repeated request ID, and service restart.
5. Add clean-install/dependency bootstrap documentation and a one-command preflight verifier.

**Acceptance:**
- Full test suite passes from a fresh project environment.
- No arbitrary shell execution reaches the model route.
- An inert real-overlay approval test passes without action execution.
- A clean-machine operator can install, start, diagnose, and stop the product from documentation.

### Task 8: Release validation and beta gate

**Objective:** Prove production behavior in the real desktop workflow before claiming readiness.

**Steps:**
1. Run the entire AI Controller suite, Master AI bridge contract tests, static compilation, and systemd unit verification.
2. Run five real inert voice scenarios: dictation, conversation, action denied, action approved, Sensei unavailable.
3. For each scenario, verify exactly one model owner, zero duplicate playback, a complete receipt chain, and correct card lifecycle.
4. Restart all production services three times, then repeat the action-approved scenario.
5. Produce a redacted release evidence report containing exact commands, outputs, service status, test totals, and remaining known limitations.

**Release gates:**
- No duplicate speech in the five scenario matrix.
- Exact one-owner contract verified across PTT, bridge, Sensei, runner, and TTS.
- Full tests green in project-owned environments.
- Clean service restarts with no warnings or loops.
- Single real approval overlay and fail-closed semantics verified.
- No exposed unauthenticated TTS endpoint.
- Working setup/support/runbook complete.

**Risk controls:**
- Do not overwrite unrelated in-progress work in either AI Controller tree or Master AI’s `calm-avatar-motion` branch.
- No controller-map changes.
- No destructive systemd/service changes until callers are proven migrated.
- No claim of public/paid market readiness before the release evidence report passes every gate.
