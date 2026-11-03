#!/usr/bin/env python3
"""Desktop approval worker for Tahuti file requests. No shell execution."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

QUEUE = Path.home() / '.tahuti' / 'approvals'
GRANTS = QUEUE / 'ui-grants.json'


def scope(record):
    # Exact payload grants, never blanket permission to overwrite a path.
    payload = {k: record[k] for k in ('tool', 'path', 'content')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def validate(record):
    if record.get('tool') != 'write_file':
        raise ValueError('This interface only executes file-write requests.')
    path = Path(record['path'])
    if not path.is_absolute() or not isinstance(record['content'], str):
        raise ValueError('Invalid write request')
    resolved = path.resolve()
    if not resolved.is_relative_to(Path.home().resolve()):
        raise ValueError('Target outside home refused')
    # Never grant edits to credential stores or agent/security configuration.
    relative = resolved.relative_to(Path.home().resolve())
    if any(part.startswith('.') for part in relative.parts):
        raise ValueError('Hidden/configuration targets require manual handling')
    return resolved


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.approval-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def execute(record):
    path = validate(record)
    # Do not create parent trees or overwrite changed files invisibly.
    with path.open('w', encoding='utf-8') as f:
        f.write(record['content'])
    if path.read_text(encoding='utf-8') != record['content']:
        raise RuntimeError('Write verification failed')


def main():
    import fcntl

    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gdk, GLib, Gtk
    QUEUE.mkdir(parents=True, exist_ok=True)
    lock = (QUEUE / 'ui-worker.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    session = set()
    try:
        permanent = set(json.loads(GRANTS.read_text()))
    except (OSError, ValueError):
        permanent = set()
    busy = [False]
    css = Gtk.CssProvider()
    css.load_from_data(b'''window {background:#102128;color:#eff8f2;} label {color:#eff8f2;} button {background:#194a3b;color:#f3dfaa;border:1px solid #b69954;border-radius:12px;padding:14px;} textview text {background:#09181d;color:#dcece4;}''')
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 600)

    def poll():
        if busy[0]:
            return True
        for request in sorted(QUEUE.glob('*.pending.json')):
            try:
                raw = request.read_bytes()
                record = json.loads(raw)
                if record.get('tool') != 'write_file':
                    continue
                target = validate(record)
                key = scope(record)
            except Exception:
                continue
            busy[0] = True
            before = target.read_bytes() if target.exists() else None
            window = Gtk.Window(title='Thootie — Approval request')
            window.set_default_size(620, 500)
            window.set_accept_focus(True)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            box.set_border_width(24)
            window.add(box)
            for text in ('THOOTIE  /  YOUR DECISION',
                         'WHO: Tahuti agent',
                         'WHAT: Write file — ' + record.get('reason', ''),
                         'WHERE: ' + str(target),
                         'WHEN: After your approval · requested ' + record.get('timestamp', ''),
                         'HOW: write_file · exact contents shown below'):
                label = Gtk.Label(label=text)
                label.set_line_wrap(True)
                label.set_xalign(0)
                box.pack_start(label, False, False, 0)
            view = Gtk.TextView()
            view.set_editable(False)
            view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            view.get_buffer().set_text(record['content'])
            scroll = Gtk.ScrolledWindow()
            scroll.set_min_content_height(160)
            scroll.add(view)
            box.pack_start(scroll, True, True, 0)
            grid = Gtk.Grid(column_spacing=12, row_spacing=12)
            box.pack_start(grid, False, False, 0)
            finished = [False]
            timer = [None]

            def decide(verdict):
                if finished[0]:
                    return False
                finished[0] = True
                if timer[0]:
                    GLib.source_remove(timer[0])
                    timer[0] = None
                result = dict(record, decision=verdict)
                try:
                    if request.read_bytes() != raw:
                        raise RuntimeError('Request changed; approval invalidated')
                    if verdict != 'deny':
                        current = target.read_bytes() if target.exists() else None
                        if current != before:
                            raise RuntimeError('Target changed while awaiting approval')
                        execute(record)
                        result['status'] = 'executed'
                        if verdict == 'session':
                            session.add(key)
                        if verdict == 'always':
                            permanent.add(key)
                            save(GRANTS, sorted(permanent))
                    else:
                        result['status'] = 'denied'
                except Exception as exc:
                    result.update(status='failed', error=str(exc))
                save(request.with_name(request.name.replace('.pending.json', '.resolved.json')), result)
                request.unlink(missing_ok=True)
                print(json.dumps({'request': request.name, 'status': result['status'], 'decision': verdict}), flush=True)
                window.destroy()
                busy[0] = False
                return False

            def clicked(button, verdict):
                if verdict == 'always':
                    dialog = Gtk.MessageDialog(transient_for=window, modal=True, message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.YES_NO, text='Allow this exact file content and target permanently?')
                    dialog.format_secondary_text('Different content or a different target will still require approval.')
                    response = dialog.run()
                    dialog.destroy()
                    if response != Gtk.ResponseType.YES:
                        decide('deny')
                        return
                decide(verdict)

            for i, (label, value) in enumerate((('Allow once','once'), ('Allow for session','session'), ('Allow permanently','always'), ('Deny','deny'))):
                button = Gtk.Button(label=label)
                button.set_hexpand(True)
                button.connect('clicked', clicked, value)
                grid.attach(button, i % 2, i // 2, 1, 1)
            window.connect('delete-event', lambda *args: decide('deny') or True)
            window.connect('key-press-event', lambda w,e: decide('deny') if e.keyval == Gdk.KEY_Escape else False)
            if key in permanent or key in session:
                decide('once')
            else:
                window.show_all()
                timer[0] = GLib.timeout_add_seconds(120, lambda: decide('deny'))
            break
        return True

    GLib.timeout_add_seconds(1, poll)
    Gtk.main()


if __name__ == '__main__':
    main()
