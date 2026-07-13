# AI Controller — Error Log

Continuous log of bugs found, root causes diagnosed, and fixes applied.
Append new entries to the bottom. Keep the most recent at the top for
quick reference.

---

## 2026-07-13 — F13 / Right Trigger pipeline failure (3 root causes)

**Symptom:** Right trigger (RT) not triggering dictation. PTT service
running but never seeing F13 key events.

### Root Cause 1: xmodmap keycode 191 not mapped to F13

**Problem:** The X server's default keymap assigns keycode 191 to
`XF86Tools`, not `F13`. AntiMicroX sends F13 via keycode 191, but X
translates it to XF86Tools, so the pynput F13 listener never fires.

**Fix:** Added `keycode 191 = F13` to `ensure_f13_keymap()` in
`controller-profile-switcher.sh`. Previously only keycode 202 was
mapped. Both 191 and 202 must map to F13 because AntiMicroX may use
either keycode depending on the X server state.

**File changed:** `scripts/controller-profile-switcher.sh` (line 56)

**Note:** This overlay gets wiped on every AntiMicroX restart or
controller hotplug. The `ensure_f13_keymap()` function must be called
after every backend reload — it already runs on script start and in
`watch_controller()`'s "present" branch.

### Root Cause 2: AntiMicroX profile keysym confusion (false fix reverted)

**What happened:** During debugging, the trigger keysym in the AntiMicroX
profile was changed from `0x100003c` to `0x1000032` based on an incorrect
assumption about AntiMicroX's key encoding.

**The encoding:** AntiMicroX uses a sequential offset from F1:
- F1 = 0x1000030
- F2 = 0x1000031
- F3 = 0x1000032
- ...
- F13 = 0x100003c (0x1000030 + 12)

The original value `0x100003c` was CORRECT for F13. Changing it to
`0x1000032` made RT send F3 instead of F13, which the PTT listener
ignored.

**Fix:** Reverted to `0x100003c` in
`profiles/dont delete .gamecontroller.amgp`.

**Lesson:** AntiMicroX key codes are NOT X11 keysyms. They are a
sequential Qt-based offset where F1=0x1000030. Do not confuse them with
X11 keysym values (where F13=0xffca).

### Root Cause 3: PTT pynput service cannot access antimicrox virtual devices

**Problem:** `evdev.list_devices()` in the PTT script returned 23
devices when run from an SSH shell, but only 1 device when run inside
the systemd user service. The antimicrox virtual keyboard (event20)
was invisible to the service.

**Root cause:** `evdev.list_devices()` calls `evdev.is_device()` which
checks `os.access(path, os.R_OK | os.W_OK)` — requiring BOTH read and
write permission. The antimicrox uinput devices are created with
`root:input` mode `0660`. In an SSH shell, the user has the `input`
group supplementary group, so write access succeeds. In a systemd user
service, supplementary groups are NOT applied, so the `input` group
write access is lost.

The physical Xbox controller (event19) works because it gets a
`user:elijah:rw-` ACL from logind/udev via the `uaccess` tag.
AntiMicroX virtual devices had no such rule, so no ACL was applied.

**Fix:** Created udev rule `/etc/udev/rules.d/90-antimicrox.rules`:
```
SUBSYSTEM=="input", ATTRS{name}=="antimicrox*", TAG+="uaccess"
```
This makes udev/logind apply `user:<username>:rw-` ACLs to antimicrox
virtual devices at creation time, giving the systemd user service
read-write access.

**Important:** After every AntiMicroX restart, the uinput devices are
recreated. The ACL must be re-applied by running:
```bash
sudo udevadm trigger --action=add --subsystem-match=input
```
Then restart the PTT service:
```bash
systemctl --user restart ptt-pynput.service
```

**File added:** `udev/90-antimicrox.rules` (installed to
`/etc/udev/rules.d/90-antimicrox.rules`)

### Additional issue: Duplicate AntiMicroX processes

**Problem:** Multiple antimicrox AppImage processes were running
simultaneously, causing input conflicts.

**Fix:** The `controller-profile-switcher.sh` already has a dedup
mechanism (added 2026-07-03). If duplicates appear, kill the extra
process and restart via `systemctl --user restart
antimicrox-autoload.service`.

### Verification (2026-07-13 15:57)

- `xmodmap -pk | grep -E '^\s+(191|202)\s'` → both show `0xffca (F13)`
- `getfacl /dev/input/event20` → `user:elijah:rw-` present
- `journalctl --user -u ptt-pynput.service` → "Evdev fallback listening
  on /dev/input/event20 (antimicrox Keyboard Emulation) for F13"
- RT press → dictation triggered, transcribed, typed text with emojis

### Files changed in this session

1. `scripts/controller-profile-switcher.sh` — added `keycode 191 = F13`
   to `ensure_f13_keymap()`
2. `profiles/dont delete .gamecontroller.amgp` — keysym reverted to
   correct `0x100003c` (F13)
3. `udev/90-antimicrox.rules` — NEW file, uaccess tag for antimicrox
   virtual devices
4. `scripts/ptt_pynput.py` — debug logging added and removed during
   diagnosis (no net change)

---

## How to add new entries

Copy the template below, fill in details, append to the TOP of this file
(above the most recent entry).

```
## YYYY-MM-DD — Short title

**Symptom:** What the user observed.

**Root cause:** What was actually broken.

**Fix:** What was changed and where.

**Verification:** How it was confirmed working.

**Files changed:** List of modified/added files.
```