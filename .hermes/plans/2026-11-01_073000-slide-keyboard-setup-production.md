# Slide Keyboard Setup Production Implementation Plan

> **For Hermes:** Use subagent-driven-development task-by-task. Build and test in `/home/elijah/projects/ai-controller`; sync only verified exact files to `/home/elijah/ai-controller`. Preserve controller mappings and unrelated committed work.

**Goal:** Turn the slide keyboard’s SETUP button into a polished, fully interactive control center for the AI Controller framework: controller input, audio, voice, Tahuti, approvals, Sensei/Master AI bridge, services, diagnostics, and integrations. It must read real backend health, perform only real configured actions, and prove that saved choices are consumed by live services.

**Architecture:** The GTK slide keyboard remains a thin launcher. `controller_setup.py` becomes the authoritative setup backend with explicit read-only health probes, validated writes, and narrow real service actions. It returns structured results so both terminal wizard and a future graphical surface report the same backend truth. No setup menu item may be displayed unless it has an implementation and post-action verification.

**Interactive setup sections:**
- **Controller:** device detection, current mapping/profile visibility, PTT mode/target, permissions, and non-destructive mapping diagnostics. Existing mappings are displayed and tested but never changed without explicit confirmation.
- **Audio and voice:** microphone/sink detection, selected output, Aria/Joe/Tahuti voice profiles, real preview/playback, barge-in verification, and only supported engine options.
- **Tahuti:** goal runner, avatar lifecycle, real approval overlay, grant/receipt health, configured voice engine, and safe inert end-to-end checks.
- **Sensei / Master AI:** bridge health, active route/model metadata, browser/extension readiness where applicable, and explicit connection tests from Tahuti to Sensei.
- **Services:** every framework user service, its status, last failure summary, controlled start/restart action, and verified post-action state.
- **Integrations:** Telegram, optional MCP/browser capabilities, and only integrations that have actual configured backends. Credentials remain masked and setup never invents or stores secret values.
- **Diagnostics:** redacted support bundle, health history, test status, and clear repair instructions.

**Tech stack:** Python 3.10, GTK3, systemd user services, JSON configuration, pytest, existing `voice_profiles.py`, `voice_toggle.py`, `voice_bridge.py`, `tahuti_goal_runner.py`, and Master AI Sensei bridge.

---

### Task 1: Map every setup control to its live consumer

**Objective:** Establish the backend contract before adding UI polish.

**Files:**
- Create: `/home/elijah/projects/ai-controller/scripts/setup_contract.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/controller_setup.py`
- Test: `/home/elijah/projects/ai-controller/tests/test_setup_contract.py`

**Steps:**
1. Write failing tests for each existing setup feature: keyboard voice, Tahuti avatar voice, avatar lifecycle, voice board, Telegram command menu, PTT/voice bridge, Tahuti runner, and Sensei bridge.
2. Build a declarative setup-feature registry containing: user-facing label, backend source of truth, live consumer(s), health probe, action handler, and verification handler.
3. Reject any menu item missing an action or verifier.
4. Use the registry to generate terminal wizard labels and doctor status rows so descriptions cannot drift from implementation.

**Acceptance:**
- Every visible SETUP choice maps to a real backend path and a live consumer.
- A test fails if a menu action becomes a prompt-only or dead feature.
- The setup report names the actual file/service/API responsible for every feature.

### Task 2: Make setup report live backend connectivity, not static promises

**Objective:** Extend doctor into a real readiness/status report for the services behind the slide keyboard.

**Files:**
- Modify: `/home/elijah/projects/ai-controller/scripts/controller_setup.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/ai_controller_paths.py` only if shared endpoint lookup is required
- Test: `/home/elijah/projects/ai-controller/tests/test_controller_setup.py`

**Steps:**
1. Add failing tests for service liveness, ownership, expected endpoint behavior, profile loadability, and active consumer reachability.
2. Probe actual service states for slide keyboard, PTT, voice bridge, Tahuti runner, Sensei bridge, and only retained TTS services.
3. Probe localhost endpoints with defined expected responses rather than treating an open port or nonempty prompt as success.
4. Verify that the active voice/Tahuti profile is loadable by its real consumer process, not merely parseable by setup.
5. Report failures with one concrete repair command and no fabricated green status.

**Acceptance:**
- Doctor distinguishes “configured,” “service active,” “endpoint answering,” and “consumer actually using configuration.”
- A dead or wrong backend produces FAIL/WARN, never OK.
- No probe exposes secret values.

### Task 3: Repair the real setup blockers before advertising them as ready

**Objective:** Fix the backend failures exposed by the current setup doctor.

**Files:**
- Modify: `/home/elijah/projects/ai-controller` dependency/bootstrap configuration as required by declared dependencies
- Modify: `/home/elijah/projects/ai-controller/scripts/controller_setup.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_loop.py` only if its current consumer probe is incorrect
- Test: `/home/elijah/projects/ai-controller/tests/test_controller_setup.py`
- Test: relevant voice/Tahuti tests

**Steps:**
1. Repair the project Python environment so it imports its own `pydantic_core`, not Hermes Python 3.14 packages.
2. Repair the Kokoro runtime or change the product contract so Tahuti cannot be labeled “ready” when its selected voice engine cannot import NumPy/Kokoro.
3. Verify the selected Tahuti voice profile against the actual runner/voice owner after the broader Tahuti TTS consolidation work.
4. Add deterministic tests for failed engine imports and clear actionable diagnostics.

**Acceptance:**
- `controller_setup.py doctor` has no hard failures on the supported live configuration.
- The selected Tahuti voice can produce a safe local test render through the real configured path.
- The project test suite collects in its own environment.

### Task 4: Make the slide keyboard SETUP entry polished and truth-aware

**Objective:** Replace a blind terminal launcher with a small native setup status/action surface while preserving keyboard focus behavior.

**Files:**
- Modify: `/home/elijah/projects/ai-controller/scripts/slide_keyboard.py`
- Create: `/home/elijah/projects/ai-controller/scripts/setup_panel.py`
- Test: `/home/elijah/projects/ai-controller/tests/test_setup_panel.py`
- Test: `/home/elijah/projects/ai-controller/tests/test_slide_keyboard_setup.py`

**Steps:**
1. Write failing tests for setup panel rendering from structured backend results, including loading, healthy, warning, failure, and action-complete states.
2. Change SETUP from a blind terminal-only launch into a compact native status card with real categories: Voice, Tahuti, Controller/Dictation, Sensei, Services, and Diagnostics.
3. Include only real actions: refresh health, open focused terminal wizard, open voice board, start/restart a failed service after explicit user action, and copy/view a redacted diagnostic result.
4. Keep the keyboard popup non-focus-stealing and preserve controller navigation/mappings.
5. Use the current theme and clear visual states; no fake progress or decorative controls.

**Acceptance:**
- Clicking SETUP presents live backend state inside the keyboard workflow.
- Every action calls a real backend function and reports its verified result.
- The old terminal wizard remains available as an advanced/full configuration path.
- No GUI test relies solely on a mock prompt without backend contract tests.

### Task 5: Make writes apply to real consumers and verify readback

**Objective:** Ensure every setup change reaches its intended backend rather than only changing a JSON file.

**Files:**
- Modify: `/home/elijah/projects/ai-controller/scripts/controller_setup.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_profiles.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_toggle.py`
- Modify: `/home/elijah/projects/ai-controller/scripts/voice_bridge.py` and `/home/elijah/projects/ai-controller/scripts/tahuti_goal_runner.py` only where they must reload the shared contract
- Test: `/home/elijah/projects/ai-controller/tests/test_controller_setup.py`
- Test: `/home/elijah/projects/ai-controller/tests/test_voice_profiles.py`

**Steps:**
1. Write failing tests for each change: profile save, consumer reload/restart policy, consumer readback, and failed rollback behavior.
2. Use atomic configuration writes with schema validation before commit.
3. Define which settings are hot-reloaded and which require a service restart; show that truth in setup before applying.
4. After every successful action, query the relevant live process/service/config reader to verify effective state.
5. Fail safely: if readback does not match, report failure and preserve the prior working profile.

**Acceptance:**
- Voice and avatar settings are confirmed by the actual speech/overlay consumer.
- No setup success screen appears without verified readback.
- Rollback/invalid-profile behavior is tested.

### Task 6: Release-quality verification, documentation, and sync

**Objective:** Prove the setup feature works in the live tree and document support paths.

**Files:**
- Update: `/home/elijah/projects/ai-controller/README.md`
- Update: `/home/elijah/projects/ai-controller/TASKS.md` if needed
- Update: `/home/elijah/projects/ai-controller/AGENTS.md` only if the workflow contract changes
- Sync verified files to: `/home/elijah/ai-controller/`

**Steps:**
1. Run focused setup, slide keyboard, profile, voice bridge, and Tahuti tests in the repaired project environment.
2. Run the full AI Controller suite.
3. Run a real non-mutating setup-panel health check through the live GTK path; verify live services and endpoint responses.
4. Exercise one safe configuration update, verify real consumer readback, then restore the original value.
5. Sync only exact verified source/test/docs files to live tree and repeat focused checks.
6. Record the release evidence: test totals, service/endpoint states, configuration readback, and known limitations.

**Release gates:**
- Every setup control has a real backend action and post-action verifier.
- Doctor has no unsupported green checks or hard failures for the supported configuration.
- SETUP card reports live state and works without stealing focus.
- Configuration changes are atomic, validated, and confirmed by live consumers.
- Focused and full tests pass from project-owned dependencies.
- No controller mapping changes.
