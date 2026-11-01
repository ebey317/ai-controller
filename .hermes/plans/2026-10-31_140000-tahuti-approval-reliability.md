# Tahuti Approval Reliability Implementation Plan

**Goal:** Make Tahuti’s avatar-owned approval surface reliable in every import path, preserve requests that arrive while the card is active, expose real lifecycle states, and replace decorative Edit with an action-specific revision workflow.

**Architecture:** `approval_gate` remains the only owner of once/session grant policy. `scripts/tahuti_avatar.py` remains presentation only, but returns a structured decision for actions that explicitly support revisions. `scripts/tahuti_desktop.py` installs the native callback regardless of approval-gate import order, and each tool passes only its validated editable payload schema.

**Tech stack:** Python 3, GTK3/PyGObject, existing shared `approval_gate.py`, AI Controller’s `voice_loop.py`, pytest plus Broadway GTK integration tests.

**Scope rules:**
- Build first in `~/projects/ai-controller`, then sync only verified files into `~/ai-controller`.
- Do not change Xbox/AntiMicroX mappings.
- Do not add a generic text editor that can silently alter an arbitrary action.
- No permanent grant is added in this pass; the locked product card is Yes / Always for this session / Deny / Edit.

---

## Task 1: Fix callback registration for every import order

**Objective:** Ensure the native Tahuti card is installed whether `approval_gate` was imported before or after `tahuti_desktop`.

**Files:**
- Modify: `~/projects/ai-controller/scripts/tahuti_desktop.py:101-166`
- Modify: `~/projects/ai-controller/tests/test_tahuti_desktop.py`

**Steps:**
1. Extract callback installation into `_install_tahuti_callback(gate)`.
2. In `_load_approval_gate()`, retrieve either the existing canonical module or load it from the exact file path.
3. Call `_install_tahuti_callback(gate)` in both paths before returning the module.
4. Keep the exact-path load of `tahuti_avatar.py`; package imports must not determine whether the box appears.
5. Add a regression test that preloads a fake/existing `approval_gate`, calls `_approval()`, and verifies `set_explicit_callback()` received the Tahuti callback.

**Validation:**
```bash
cd ~/projects/ai-controller
/home/elijah/ai-controller/.venv/bin/python -m pytest tests/test_tahuti_desktop.py -q
```
Expected: callback registration tests pass for fresh and already-imported gate paths.

---

## Task 2: Preserve requests behind the one active card

**Objective:** Replace “second request denies” with a bounded FIFO queue while retaining exactly one visible card.

**Files:**
- Modify: `~/projects/ai-controller/scripts/tahuti_avatar.py:336-381`
- Modify: `~/projects/ai-controller/tests/tahuti_box_driver.py`
- Modify: `~/projects/ai-controller/tests/test_tahuti_avatar_box.py`

**Steps:**
1. Add a request record containing pattern key, description, extra data, completion event, and result.
2. Keep one GTK window and one active card. Subsequent requests enqueue in arrival order instead of opening another window.
3. After the active request resolves, show the next queued request and deliver each result only to its matching requester.
4. Add a small fixed queue limit. If it is full, deny the newest request and write a diagnostic log entry; never drop a request silently.
5. On shutdown or GTK failure, resolve all queued requests as deny.
6. Add Broadway integration coverage for two simultaneous requests: one top-level window, first decision resolves first request, second then appears and resolves independently.

**Validation:**
```bash
cd ~/projects/ai-controller
/home/elijah/ai-controller/.venv/bin/python -m pytest tests/test_tahuti_avatar_box.py -q
```
Expected: one-window test still passes; queued-request test proves requests are not silently discarded.

---

## Task 3: Make Tahuti’s visible state follow real voice-loop lifecycle events

**Objective:** Turn the static portrait/card into a truthfully state-driven companion without faking work progress.

**Files:**
- Modify: `~/projects/ai-controller/scripts/tahuti_avatar.py`
- Modify: `~/projects/ai-controller/scripts/voice_loop.py:570-643`
- Modify: `~/projects/ai-controller/tests/test_tahuti_avatar_box.py`
- Modify: `~/projects/ai-controller/tests/test_voice_loop.py`

**Steps:**
1. Define the shared state file/path once in a small helper or constant; do not duplicate string paths.
2. Have the avatar read the existing `voice_loop` payload state: idle, listening, thinking, speaking, working, done.
3. Map only real states to visuals:
   - listening: subtle emerald pulse
   - thinking: quiet gold orbit/pulse
   - speaking: reversible talk indicator synchronized to speaking state, not fake lip sync
   - working: scribe/work glyph motion
   - done: brief verified-result treatment, then fade according to profile
4. Use one GLib timer to compose all movement and visual state changes. Cap per-tick movement so the box cannot jitter.
5. Respect profile controls: `show_on_talk`, `stay_while_working`, `fade_when_done`, `popup_on_approval`, size, movement, and notification-corner exclusion.
6. Add pure tests for state-to-visual mapping and a Broadway test proving no additional top-level window appears during a lifecycle transition.

**Validation:**
```bash
cd ~/projects/ai-controller
/home/elijah/ai-controller/.venv/bin/python -m pytest tests/test_voice_loop.py tests/test_tahuti_avatar_box.py -q
```
Expected: state mapping and one-window lifecycle tests pass.

---

## Task 4: Add safe, action-specific Edit contracts

**Objective:** Make Edit real only for actions whose revised values can be validated and re-approved.

**Files:**
- Modify: `~/projects/ai-controller/scripts/tahuti_avatar.py`
- Modify: `~/projects/ai-controller/scripts/tahuti_desktop.py:198-211, 348-488`
- Modify: `~/projects/ai-controller/tests/test_tahuti_desktop.py`
- Modify: `~/projects/ai-controller/tests/test_tahuti_avatar_box.py`

**Initial supported edit schemas:**
- `type_text`: replacement text and delay, with existing length/type limits.
- `press_key`: replacement named key, validated against the existing key input rules.
- `move_cursor` / click: replacement x/y coordinates and button, validated as integers inside current screen bounds.

**Explicitly not editable in this pass:**
- `run_command` — command edits need a dedicated shell-risk policy and separate review.
- screenshot — no user payload.

**Steps:**
1. Define an `EditableApproval`/decision object with `verdict` and an optional validated replacement payload.
2. Let each calling action pass an explicit edit schema and original payload to `_require_approval()`.
3. On Edit, show only the fields that schema allows.
4. Validate changed values before showing them back to the user.
5. Re-present the complete card with the revised WHAT/WHERE/HOW/EFFECT and require a fresh Yes or session decision.
6. Return the revised payload only to the original caller; the caller executes that exact revised payload, not the original.
7. For non-editable actions, show “This action cannot be revised here; Deny it and issue a new request” instead of a false editor.

**Validation:**
```bash
cd ~/projects/ai-controller
/home/elijah/ai-controller/.venv/bin/python -m pytest tests/test_tahuti_desktop.py tests/test_tahuti_avatar_box.py -q
```
Expected: edit → revised card → fresh Yes executes revised `type_text`/key/click payload only; cancel/close/timeout leaves both original and revised actions unexecuted.

---

## Task 5: Add actionable failure receipts

**Objective:** Preserve fail-closed behavior while making UI failures diagnosable.

**Files:**
- Modify: `~/projects/ai-controller/scripts/tahuti_avatar.py`
- Modify: `~/projects/ai-controller/tests/test_tahuti_avatar_box.py`

**Steps:**
1. Replace broad silent exception swallowing with a small local logger that records timestamp, request pattern, outcome, and exception class/message to `~/.tahuti/approval-events.jsonl`.
2. Never write request payloads that may contain private screen/text data into the log; record metadata only.
3. Return deny after failures exactly as today.
4. Include a receipt ID in the refusal message returned to the caller so logs can be correlated without exposing content.
5. Test that a forced GTK/load failure returns deny and produces one sanitized receipt.

**Validation:**
```bash
cd ~/projects/ai-controller
/home/elijah/ai-controller/.venv/bin/python -m pytest tests/test_tahuti_avatar_box.py -q
```
Expected: failure remains deny; diagnostic receipt exists; sensitive action content is absent.

---

## Task 6: Full verification and live-tree sync

**Objective:** Verify the canonical implementation, then deploy only the verified avatar/approval files to the live controller tree.

**Files:**
- Sync after verification: `scripts/tahuti_avatar.py`, `scripts/tahuti_desktop.py`, relevant tests
- Update: `~/MD/HANDOFF.md` using Who / What / Where / When / Why / How format

**Steps:**
1. Run focused tests after each task.
2. Run the full canonical suite using the live venv if the canonical tree lacks one.
3. Sync only changed verified files to `~/ai-controller`—do not overwrite its unrelated runner/voice work.
4. Run the live full suite.
5. Submit two inert live requests through `approval_gate.request_approval()` to prove:
   - a pre-imported gate still receives the native callback;
   - one visible card processes two queued requests in order;
   - a Yes/session result reaches the original requester;
   - no desktop action executes during the inert proof.
6. Capture a screenshot during the live card display, inspect the displayed pixels, then terminate the inert test process.
7. Append the required handoff entry with real test output and the receipt/log behavior.

**Final validation:**
```bash
cd ~/ai-controller
.venv/bin/python -m pytest tests -q --disable-warnings
```
Expected: full suite green and two-request native gate test returns the selected verdicts.

---

## Risks and decisions

- The shared `approval_gate` only accepts verdicts today. Structured revision results require a narrow backwards-compatible extension; do not break existing terminal callers.
- A queued GTK request must never be serviced by the wrong action thread. Each request needs its own completion event and immutable payload snapshot.
- Editing shell commands is deliberately deferred. A generic command textbox would expand attack surface and falsely suggest safety.
- Visual animation must be driven from `voice_loop` state only. Do not add a simulated “working” animation that implies work occurred.
- The live tree has unrelated uncommitted work. Never use wholesale directory sync or reset.
