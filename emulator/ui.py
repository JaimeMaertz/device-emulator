"""
The web UI (NiceGUI). One card per emulated device:
- how it's reached (TCP port and client count, COM port), updated live
- the front panel / remote, generated from the profile's controls(): what someone in the room
  would do, so the processor sees changes it didn't make
- faults: offline, silent, corrupt replies, reply delay, drop connections, mains power cycle
- the profile's options and fidelity notes
- a traffic log: rx = from the processor, tx = replies, push = unsolicited, state = changes

Devices live in DEVICES for the whole process; every browser tab shows the same set.
"""
import os
import re

from nicegui import app, ui

from . import bench
from .core import PROFILES

DEVICES = []
VIEW = {'mode': 'auto', 'bench': None}   # log view: auto (the profile's log_as), hex, or ascii

LOG_STYLE = {
    'rx': ('RX', 'text-sky-700'), 'tx': ('TX', 'text-green-700'), 'push': ('PUSH', 'text-purple-700'),
    'state': ('STATE', 'text-amber-700'), 'info': ('INFO', 'text-gray-500'), 'warn': ('WARN', 'text-red-600'),
}
LOG_LINE_STYLE = 'white-space: pre-wrap; overflow-wrap: break-word'   # wrap at spaces: hex bytes stay whole
BAUDS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]


def render(payload, log_as):
    if not isinstance(payload, bytes):
        return str(payload)
    mode = log_as if VIEW['mode'] == 'auto' else VIEW['mode']
    if mode == 'hex':
        return payload.hex(' ')
    return ''.join(chr(c) if 32 <= c < 127 else {13: '\\r', 10: '\\n'}.get(c, '<{:02X}>'.format(c))
                   for c in payload)


def log_line(dev, entry):
    seq, stamp, kind, payload, link = entry
    tag, classes = LOG_STYLE[kind]
    text = '{} {:<5} {}{}'.format(stamp, tag, '[{}] '.format(link) if link else '', render(payload, dev.log_as))
    return text, classes


def show(value):
    return {True: 'On', False: 'Off'}.get(value, value) if isinstance(value, bool) else value


def used_tcp_ports():
    return {t.port for d in DEVICES for t in d.transports if t.kind in ('tcp', 'ssh')}


def next_port(start):
    used, port = used_tcp_ports(), start
    while port in used:
        port += 1
    return port


def serial_ports():
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except ImportError:
        return []


def marker(dev, key):
    """A per-device element marker (for tests): 'Projector_1.power'."""
    return '{}.{}'.format(re.sub(r'\W+', '_', dev.name), key)


def clear_devices():
    for d in DEVICES:
        d.close()
    DEVICES.clear()


def root():
    ui.page_title('Device Emulator')
    seen, logs, live = {}, {}, []   # per tab: last log seq shown, log elements, labels to refresh

    # ----- one device
    def control(dev, c):
        st = dev.state

        def changed(e, key=c.key):
            if e.value is None or e.value == st.get(key):
                return
            dev.panel(key, e.value)
            if st.get(key) != e.value:   # the device refused it: show what it really is
                e.sender.value = st.get(key)

        mark = marker(dev, c.key)
        if c.kind == 'readout' and c.get:   # computed on each UI tick
            label = ui.label().classes('font-medium').mark(mark)
            live.append((label, lambda c=c: '{}: {}'.format(c.label, show(c.get())), lambda: False))
        elif c.kind == 'readout':
            ui.label().bind_text_from(st, c.key, backward=lambda v, l=c.label: '{}: {}'.format(l, show(v))) \
                .classes('font-medium').mark(mark)
        elif c.kind == 'choice':
            ui.select(c.options(), label=c.label, on_change=changed).bind_value_from(st, c.key) \
                .classes('w-full').mark(mark)
        elif c.kind == 'toggle':
            ui.switch(c.label, on_change=changed).bind_value_from(st, c.key).props('dense').mark(mark)
        elif c.kind == 'level':
            with ui.row().classes('w-full items-center no-wrap'):
                ui.label(c.label).classes('w-20')
                ui.slider(min=c.lo, max=c.hi, step=c.step, on_change=changed).bind_value_from(st, c.key) \
                    .classes('grow').mark(mark)
                ui.label().bind_text_from(st, c.key).classes('w-8 text-right')

    def button_row(dev, buttons):
        with ui.row().classes('gap-1'):
            for b in buttons:
                ui.button(b.label, on_click=lambda k=b.key: dev.panel(k, True)) \
                    .props('outline dense no-caps size=sm').mark(marker(dev, b.key))

    def option(dev, o):
        def changed(e, key=o.key):
            if e.value is None:
                return
            value = int(e.value) if isinstance(o.default, int) and not isinstance(o.default, bool) else e.value
            dev.set_option(key, value)

        value = dev.opts[o.key]
        if o.kind == 'number':
            ui.number(o.label, value=value, min=o.lo, max=o.hi, step=o.step, on_change=changed).classes('w-full')
        elif o.kind == 'toggle':
            ui.switch(o.label, value=value, on_change=changed)
        elif o.kind == 'choice':
            ui.select(o.options(), label=o.label, value=value, on_change=changed).classes('w-full')
        elif o.kind == 'text':
            ui.input(o.label, value=value, on_change=changed).classes('w-full')

    def device_card(dev):
        with ui.card().classes('w-[27rem]'):
            with ui.row().classes('w-full items-center no-wrap'):
                with ui.column().classes('gap-0 grow'):
                    ui.label(dev.name).classes('text-lg font-bold')
                    ui.label('{} · {}'.format(dev.profile_name, dev.model)).classes('text-xs text-gray-500')
                ui.button(icon='restart_alt', on_click=dev.power_cycle).props('flat round dense') \
                    .tooltip('Mains power cycle: drop connections, back to power-up state')
                ui.button(icon='delete', on_click=lambda: remove(dev)).props('flat round dense color=negative') \
                    .mark(marker(dev, 'remove')) \
                    .tooltip('Remove device')
            for t in dev.transports:
                with ui.row().classes('items-center gap-1 text-sm'):
                    ui.icon({'tcp': 'lan', 'ssh': 'lock', 'cip': 'call_made'}.get(t.kind, 'cable'))
                    label = ui.label(t.describe()).classes('text-red-600' if t.error else '')
                    live.append((label, t.describe, lambda t=t: bool(t.error)))
            ui.separator()

            # Buttons (remote keys) go in one row, right under the power readout when there is one
            controls = dev.controls()
            buttons = [c for c in controls if c.kind == 'button']
            for c in controls:
                if c.kind != 'button':
                    control(dev, c)
                    if c.kind == 'readout' and c.key == 'power' and buttons:
                        button_row(dev, buttons)
                        buttons = []
            if buttons:
                button_row(dev, buttons)

            with ui.expansion('Faults', icon='bolt').classes('w-full'):
                for key, text in (('offline', 'Offline (network / serial dead)'),
                                  ('silent', 'Silent (receives, never replies)'),
                                  ('corrupt', 'Corrupt replies (one byte flipped)')):
                    ui.switch(text, value=dev.faults[key], on_change=lambda e, k=key: dev.set_fault(k, e.value)) \
                        .mark(marker(dev, 'fault_' + key))
                ui.number('Reply delay (s)', value=dev.faults['delay'], min=0, max=10, step=0.1,
                          on_change=lambda e: dev.set_fault('delay', e.value or 0)).classes('w-full')
                ui.number('Boot time after a power cycle (s)', value=dev.faults['boot'], min=0, max=300, step=1,
                          on_change=lambda e: dev.set_fault('boot', e.value or 0)).classes('w-full') \
                    .tooltip('How long the device is unreachable after the power-cycle button')
                ui.button('Drop TCP connections', icon='link_off', on_click=dev.drop_clients).props('outline dense')
            if dev.options:
                with ui.expansion('Options', icon='tune').classes('w-full'):
                    for o in dev.options:
                        option(dev, o)
            with ui.expansion('Fidelity notes', icon='info').classes('w-full'):
                ui.label(dev.notes.strip()).classes('text-xs whitespace-pre-wrap')

            log = ui.log(max_lines=400).classes('w-full h-56 text-xs font-mono')
            logs[dev] = log
            seen[dev] = 0
            for entry in list(dev.entries)[-150:]:
                text, classes = log_line(dev, entry)
                log.push(text, classes=classes, style=LOG_LINE_STYLE)
                seen[dev] = entry[0]
            ui.button('Clear log', on_click=log.clear).props('flat dense size=sm no-caps')

    @ui.refreshable
    def grid():
        logs.clear()
        live.clear()
        if not DEVICES:
            ui.label('No devices yet. Add one, or load a bench.').classes('text-gray-500')
        with ui.row().classes('w-full items-start gap-4'):
            for dev in list(DEVICES):
                device_card(dev)

    def remove(dev):
        dev.close()
        DEVICES.remove(dev)
        grid.refresh()

    def tick():
        for dev, log in list(logs.items()):
            last = seen.get(dev, 0)
            for entry in list(dev.entries):
                if entry[0] > last:
                    text, classes = log_line(dev, entry)
                    log.push(text, classes=classes, style=LOG_LINE_STYLE)
                    last = entry[0]
            seen[dev] = last
        for label, text_of, is_error in live:
            text = text_of()
            if label.text != text:
                label.text = text
                label.classes(replace='font-medium text-red-600' if is_error() else 'font-medium')

    # ----- add device
    def open_add():
        with ui.dialog() as dialog, ui.card().classes('w-[34rem]'):
            ui.label('Add device').classes('text-lg font-bold')
            choices = {pid: '{}: {}'.format(cls.kind.title(), cls.profile_name)
                       for pid, cls in sorted(PROFILES.items(), key=lambda kv: (kv[1].kind, kv[1].profile_name))}
            profile = ui.select(choices, value=next(iter(choices)), label='Profile',
                                on_change=lambda e: profile_changed()).classes('w-full').mark('add_profile')
            model = ui.select([], label='Model').classes('w-full').mark('add_model')
            name = ui.input('Name').classes('w-full').mark('add_name')
            with ui.row().classes('items-center'):
                use_tcp = ui.checkbox('TCP server', value=True).mark('add_tcp')
                tcp_port = ui.number('Port', min=1, max=65535, step=1, format='%d').mark('add_port') \
                    .bind_visibility_from(use_tcp, 'value')
            with ui.row().classes('items-center') as ssh_row:
                use_ssh = ui.checkbox('SSH server').mark('add_ssh')
                ssh_port = ui.number('Port', min=1, max=65535, step=1, format='%d').mark('add_ssh_port') \
                    .bind_visibility_from(use_ssh, 'value')
            use_serial = ui.checkbox('Serial (local COM port)').mark('add_serial')
            with ui.column().classes('w-full').bind_visibility_from(use_serial, 'value'):
                com = ui.select(serial_ports(), label='COM port', with_input=True,
                                new_value_mode='add-unique').classes('w-full')
                with ui.row().classes('w-full no-wrap'):
                    baud = ui.select(BAUDS, label='Baud').classes('grow')
                    bits = ui.select([7, 8], label='Data').classes('w-20')
                    parity = ui.select({'N': 'None', 'E': 'Even', 'O': 'Odd'}, label='Parity').classes('w-24')
                    stop = ui.select([1, 2], label='Stop').classes('w-20')

            def profile_changed():
                cls = PROFILES[profile.value]
                model.set_options(list(cls.models), value=next(iter(cls.models)))
                count = sum(1 for d in DEVICES if d.profile_id == cls.profile_id)
                name.value = '{} {}'.format(cls.profile_name.split(' (')[0], count + 1)
                tcp_port.value = next_port(cls.default_tcp_port or 7000)
                ssh_row.set_visibility(bool(cls.default_ssh_port))
                use_ssh.value = False
                if cls.default_ssh_port:
                    ssh_port.value = next_port(cls.default_ssh_port)
                sd = cls.serial_defaults
                baud.value, bits.value, parity.value, stop.value = sd['baudrate'], sd['bytesize'], sd['parity'], sd['stopbits']

            def create():
                transports = []
                if use_tcp.value:
                    if int(tcp_port.value or 0) in used_tcp_ports():
                        ui.notify('TCP port {} is already used by another device'.format(int(tcp_port.value)), type='warning')
                        return
                    transports.append({'type': 'tcp', 'port': int(tcp_port.value)})
                if use_ssh.value:
                    if int(ssh_port.value or 0) in used_tcp_ports() or \
                            (use_tcp.value and int(ssh_port.value or 0) == int(tcp_port.value or 0)):
                        ui.notify('Port {} is already used'.format(int(ssh_port.value or 0)), type='warning')
                        return
                    transports.append({'type': 'ssh', 'port': int(ssh_port.value)})
                if use_serial.value:
                    if not com.value:
                        ui.notify('Pick a COM port', type='warning')
                        return
                    transports.append({'type': 'serial', 'port': com.value, 'baudrate': baud.value,
                                       'bytesize': bits.value, 'parity': parity.value, 'stopbits': stop.value})
                if not transports:
                    ui.notify('Choose TCP, SSH or serial', type='warning')
                    return
                dev = bench.build({'profile': profile.value, 'model': model.value, 'name': name.value,
                                   'transports': transports})
                DEVICES.append(dev)
                errors = [t.describe() for t in dev.transports if t.error]
                if errors:
                    ui.notify('; '.join(errors), type='negative', multi_line=True, timeout=0, close_button=True)
                dialog.close()
                grid.refresh()

            profile_changed()
            with ui.row().classes('w-full justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Add', icon='add', on_click=create).mark('add_create')
        # Closed by Add, Cancel, or a click outside: remove it, so dialogs don't pile up in the page
        dialog.on_value_change(lambda e: None if e.value else dialog.delete())
        dialog.open()

    # ----- benches
    typed = {'text': None}   # what's in the bench field, picked from the list or typed

    def current_bench():
        """The field's text: a typed name counts without pressing Enter."""
        text = typed['text'] if typed['text'] is not None else bench_name.value
        return (text or '').strip()

    def bench_options(keep=None):
        """Saved benches, re-read from benches/ each time, plus the current entry if it's new."""
        names = bench.list_benches()
        if keep and keep not in names:
            names.append(keep)
        return names

    def bench_typed(e):
        typed['text'] = e.args if isinstance(e.args, str) else ''

    def bench_picked(e):
        typed['text'] = e.value or ''

    def refresh_bench_list():
        bench_name.set_options(bench_options(bench_name.value), value=bench_name.value)

    def remember_bench(name):
        VIEW['bench'] = name
        typed['text'] = name
        bench_name.set_options(bench_options(name), value=name)

    def load_bench():
        name = current_bench()
        if not name:
            return
        try:
            configs = bench.load(name)
        except (OSError, ValueError, KeyError) as e:
            ui.notify('Could not load: {}'.format(e), type='negative')
            return
        clear_devices()
        for config in configs:
            DEVICES.append(bench.build(config))
        remember_bench(name)
        grid.refresh()
        ui.notify('Loaded {} ({} devices)'.format(name, len(DEVICES)))

    def save_bench():
        name = current_bench()
        if not name:
            ui.notify('Type a bench name first', type='warning')
            return
        path = bench.bench_path(name)
        if os.path.exists(path):
            with ui.dialog() as confirm, ui.card():
                ui.label('Overwrite {}?'.format(path))
                with ui.row().classes('w-full justify-end'):
                    ui.button('Cancel', on_click=confirm.close).props('flat')
                    ui.button('Overwrite', on_click=lambda: (confirm.close(), write_bench(name))) \
                        .props('color=negative').mark('bench_overwrite')
            confirm.on_value_change(lambda e: None if e.value else confirm.delete())
            confirm.open()
            return
        write_bench(name)

    def write_bench(name):
        bench.save(name, DEVICES)
        remember_bench(name)
        ui.notify('Saved {}'.format(bench.bench_path(name)))

    def view_changed(e):
        VIEW['mode'] = e.value
        seen.clear()
        grid.refresh()

    with ui.header().classes('items-center gap-3'):
        ui.label('Device Emulator').classes('text-xl font-bold')
        ui.button('Add device', icon='add', on_click=open_add).props('color=white text-color=primary').mark('add_device')
        ui.space()
        ui.toggle({'auto': 'Auto', 'hex': 'Hex', 'ascii': 'ASCII'}, value=VIEW['mode'], on_change=view_changed) \
            .props('dense color=white text-color=primary toggle-color=primary toggle-text-color=white')
        # A searchable list of saved benches under the field; typing filters it, and a new name
        # (to save) or a path to a .json file works too
        bench_name = ui.select(bench_options(VIEW['bench']), label='Bench', value=VIEW['bench'],
                               with_input=True, new_value_mode='add-unique', on_change=bench_picked) \
            .props('dense dark standout') \
            .classes('w-56').mark('bench_name') \
            .tooltip('Pick a saved bench, type a new name to save, or a path to a .json file')
        bench_name.on('input-value', bench_typed)
        # Re-read benches/ when the field is clicked, before any typing: refreshing when the list
        # opens would reset the type-to-filter view, which opens the list too
        bench_name.on('focus', refresh_bench_list)
        ui.button('Load', icon='folder_open', on_click=load_bench).props('flat color=white').mark('bench_load')
        ui.button('Save', icon='save', on_click=save_bench).props('flat color=white').mark('bench_save')

    grid()
    ui.timer(0.25, tick)


def start(bench_name=None, port=8080, show=True, host=None):
    if bench_name:
        for config in bench.load(bench_name):
            DEVICES.append(bench.build(config))
        VIEW['bench'] = bench_name
    app.on_shutdown(clear_devices)
    ui.run(root, title='Device Emulator', port=port, host=host, show=show, reload=False, favicon='🖥️')
