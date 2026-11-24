"""
The device model every profile builds on.

A profile is a `Device` subclass for one protocol family (NEC projector binary, LG RS-232 "ka 01 01",
...). The models it covers are data (`models`), and its knobs are `options`. A profile supplies:

    reset()              initial state, in self.state
    frames(buf)          split complete commands off the front of a byte buffer
    handle(frame, link)  one command in, reply bytes out (None = the real device stays silent)
    controls()           the front panel / remote shown in the UI
    panel(key, value)    a front-panel / remote action (a Button sends value True)

and, when the real device needs them:

    connected(link)      a new connection; return a greeting (PJLink, SIS banner) or None
    ssh_login(user, pw)  an SSH login (devices with default_ssh_port): a level, or None to refuse
    disconnected(link)   a connection closed
    partial_timeout / partial_timed_out(link)
                         a command left incomplete this long gets this reply (NEC NULL)

Each link (a TCP client, an SSH session, or a COM port) has `link.kind` ('tcp', 'ssh' or 'serial') and `link.state`, a
dict for per-connection protocol state such as authentication or verbose mode. `push(data)` sends unsolicited output to every link,
or to `links=`, for devices that really do that. Timers (`after`) drive timed behavior like
warm-up, and the fault switches let the UI break the device on purpose.

Threading: transports call in from their own threads, timers fire on theirs, and the UI reads
`state` from the event loop. `lock` serializes everything that touches `state`. Nothing is sent
while it's held: output is queued, and one sender thread per device applies the reply delay and
corruption faults and writes it out, in order.
"""
import collections
import itertools
import queue
import threading
import time

PROFILES = {}  # profile_id -> Device subclass, filled as profile modules are imported


class Control:
    """One UI element, declared by a profile. `key` names a state (or option) entry."""

    def __init__(self, kind, key, label, choices=None, lo=None, hi=None, step=1, default=None, get=None,
                 labels=None):
        self.kind, self.key, self.label = kind, key, label
        self.choices, self.lo, self.hi, self.step, self.default = choices, lo, hi, step, default
        self.get, self.labels = get, labels

    def options(self):
        """What a select shows: the choices, or {choice: label} when labels are given."""
        if self.labels:
            return {c: self.labels.get(c, c) for c in self.choices}
        return self.choices


# Front-panel controls (key = a state entry; changes go through Device.panel)
def Choice(key, label, choices, labels=None):
    """choices are the state values; labels, if given, maps a value to the text shown for it."""
    return Control('choice', key, label, choices=list(choices), labels=labels)

def Toggle(key, label):
    return Control('toggle', key, label)

def Level(key, label, lo, hi, step=1):
    return Control('level', key, label, lo=lo, hi=hi, step=step)

def Readout(key, label, get=None):
    """Shows state[key], or get() when given (a computed value such as elapsed time)."""
    return Control('readout', key, label, get=get)

def Button(key, label):
    """A momentary press; Device.panel(key, True) is called."""
    return Control('button', key, label)


# Options (key = an entry in Device.opts; saved with the bench)
def NumberOption(key, label, default, lo=0, hi=100000, step=1):
    return Control('number', key, label, lo=lo, hi=hi, step=step, default=default)

def FlagOption(key, label, default=False):
    return Control('toggle', key, label, default=default)

def TextOption(key, label, default=''):
    return Control('text', key, label, default=default)

def ChoiceOption(key, label, choices, default):
    return Control('choice', key, label, choices=list(choices), default=default)


class Device:
    profile_id = None       # stable id used in bench files; setting it registers the profile
    profile_name = ''       # shown in the UI
    kind = 'display'        # device type, for grouping: display, projector, bluray, ...
    models = {}             # model name -> model data (input tables, timings, ...)
    default_tcp_port = None
    default_ssh_port = None # set for devices with an SSH control port (SSH needs paramiko)
    serial_defaults = {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    tcp_idle_timeout = 0    # seconds of silence before the real device drops a TCP client (0 = never)
    partial_timeout = 0     # seconds before an incomplete command gets partial_timed_out() (0 = never)
    log_as = 'hex'          # how the traffic log shows this protocol by default: 'hex' or 'ascii'
    options = []            # Controls of the option kinds
    notes = ''              # fidelity notes: what's confirmed and what's a best guess

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.profile_id:
            PROFILES[cls.profile_id] = cls

    def __init__(self, name, model=None, options=None, design=None):
        self.name = name
        self.design = design    # structured configuration a profile may take (a Tesira's blocks)
        self.model = model if model in self.models else next(iter(self.models))
        self.spec = self.models[self.model]
        self.opts = {o.key: o.default for o in self.options}
        self.opts.update({k: v for k, v in (options or {}).items() if k in self.opts})
        self.lock = threading.RLock()
        self.state = {}
        self.links = set()
        self.transports = []
        self.faults = {'offline': False, 'silent': False, 'corrupt': False, 'delay': 0.0, 'boot': 5.0}
        self.booting = False
        self.start_offline = False   # the bench loads it with Offline on (bench.py)
        self.entries = collections.deque(maxlen=1000)
        self._seq = itertools.count(1)
        self._timers = {}
        self._out = queue.Queue()
        threading.Thread(target=self._sender, daemon=True).start()
        with self.lock:
            self.reset()

    # ----- profile hooks
    def reset(self):
        pass

    def frames(self, buffer):
        """Return (complete frames, remainder). A None frame logs 'bad frame'; the profile decides
        what remainder to keep (resync, or b'' to clear)."""
        return [], buffer

    def handle(self, frame, link):
        return None

    def controls(self):
        return []

    def panel(self, key, value):
        """A front-panel / remote action from the UI. Profiles override to apply device rules."""
        self.set(key, value)

    def connected(self, link):
        return None

    def disconnected(self, link):
        pass

    def partial_timed_out(self, link):
        return None

    def ssh_login(self, username, password):
        """An SSH login: return something truthy (kept as link.state['ssh_level']) to let it in,
        or None to refuse. The default lets anyone in."""
        return 'admin'

    def lan_open(self):
        """False while the real device's LAN control port is closed (many close it in standby).
        Call lan_changed() after the state that decides it changes."""
        return True

    def option_changed(self, key, value):
        pass

    # ----- helpers for profiles
    def set(self, key, value, log=True):
        with self.lock:
            if self.state.get(key) != value:
                self.state[key] = value
                if log:
                    self.log('state', '{} -> {}'.format(key, value))

    def after(self, name, seconds, fn):
        """Run fn (under the lock) after seconds; restarting a name cancels its pending run."""
        self.cancel(name)

        def fire():
            with self.lock:
                if self._timers.get(name) is timer:
                    del self._timers[name]
                    fn()
        timer = threading.Timer(seconds, fire)
        timer.daemon = True
        self._timers[name] = timer
        timer.start()

    def cancel(self, name):
        timer = self._timers.pop(name, None)
        if timer:
            timer.cancel()

    def pending(self, name):
        return name in self._timers

    def push(self, data, links=None):
        """Unsolicited output, to every link or to `links`, for devices that really send it."""
        if self.is_offline() or self.faults['silent']:
            return
        for link in list(self.links if links is None else links):
            self._send(link, data, 'push')

    def hang_up(self, link):
        """Close this client's connection, as the real device does. Called from handle(), it closes after that
        handle()'s reply has gone out; called elsewhere, after anything already queued."""
        if getattr(link, 'handling', False):
            link.hang_up_after_reply = True
        else:
            self._out.put((link, None, 'close'))

    # ----- plumbing (transports call these)
    def link_opened(self, link):
        self.links.add(link)
        self.log('info', 'client connected', link)
        with self.lock:
            greeting = self.connected(link)
        if greeting:
            self._send(link, greeting, 'push')

    def link_closed(self, link):
        if link in self.links:
            self.links.discard(link)
            self._cancel_partial(link)
            self.log('info', 'client disconnected', link)
            with self.lock:
                self.disconnected(link)

    def receive(self, link, data):
        if self.is_offline():
            return
        link.buffer += data
        frames, link.buffer = self.frames(link.buffer)
        for frame in frames:
            if frame is None:
                self.log('warn', 'bad frame dropped', link)
                continue
            self.log('rx', frame, link)
            if self.faults['silent']:
                continue
            with self.lock:
                link.handling = True
                try:
                    reply = self.handle(frame, link)
                finally:
                    link.handling = False
            if reply:
                self._send(link, reply, 'tx')
            if getattr(link, 'hang_up_after_reply', False):
                link.hang_up_after_reply = False
                self._out.put((link, None, 'close'))
        self._arm_partial(link)

    def _arm_partial(self, link):
        self._cancel_partial(link)
        if link.buffer and self.partial_timeout:
            def fire():
                if link.partial_timer is not timer or not link.buffer:
                    return
                self.log('info', 'incomplete command timed out: {}'.format(link.buffer.hex(' ')), link)
                link.buffer = b''
                with self.lock:
                    reply = self.partial_timed_out(link)
                if reply and not self.faults['silent']:
                    self._send(link, reply, 'tx')
            timer = threading.Timer(self.partial_timeout, fire)
            timer.daemon = True
            link.partial_timer = timer
            timer.start()

    @staticmethod
    def _cancel_partial(link):
        timer, link.partial_timer = link.partial_timer, None
        if timer:
            timer.cancel()

    def _send(self, link, data, kind):
        self._out.put((link, data, kind))

    def _sender(self):
        while True:
            item = self._out.get()
            if item is None:
                return
            link, data, kind = item
            if kind == 'close':
                if link in self.links and link.close:
                    self.log('info', 'closing the connection (as the real device does)', link)
                    link.close()
                continue
            if self.faults['delay']:
                time.sleep(self.faults['delay'])
            if self.faults['corrupt'] and data:
                i = len(data) // 2
                data = data[:i] + bytes([data[i] ^ 0x5A]) + data[i + 1:]
            if link not in self.links:
                continue   # the connection closed while this waited
            self.log(kind, data, link)
            try:
                link.send(data)
            except OSError as e:
                self.log('warn', 'send failed: {}'.format(e), link)

    def log(self, kind, payload, link=None):
        self.entries.append((next(self._seq), time.strftime('%H:%M:%S'), kind, payload,
                             link.label if link else ''))

    # ----- faults and power
    def is_offline(self):
        return self.faults['offline'] or self.booting

    def _apply_offline(self):
        offline = self.is_offline()
        for t in self.transports:
            t.set_offline(offline or (t.kind == 'tcp' and not self.lan_open()))

    def lan_changed(self):
        self._apply_offline()

    def set_fault(self, key, value):
        self.faults[key] = value
        self.log('info', 'fault {} -> {}'.format(key, value))
        if key == 'offline':
            self._apply_offline()

    def set_option(self, key, value):
        with self.lock:
            self.opts[key] = value
            self.log('info', 'option {} -> {}'.format(key, value))
            self.option_changed(key, value)

    def drop_clients(self):
        for t in self.transports:
            t.drop_clients()

    def power_cycle(self):
        """Mains off/on: connections drop, the device is unreachable for the boot time, then it
        comes back in its power-up state."""
        with self.lock:
            for name in list(self._timers):
                self.cancel(name)
            self.state.clear()
            self.reset()
        boot = self.faults['boot']
        self.log('info', 'power cycled; booting {}s'.format(boot))
        if not boot:
            self.drop_clients()
            return
        self.booting = True
        self._apply_offline()

        def booted():
            self.booting = False
            self._apply_offline()
            self.log('info', 'boot complete')
        self.after('boot', boot, booted)

    def add_transport(self, transport):
        self.transports.append(transport)
        transport.start()
        self._apply_offline()

    def remove_transport(self, transport):
        transport.stop()
        self.transports.remove(transport)

    def close(self):
        for t in list(self.transports):
            self.remove_transport(t)
        with self.lock:
            for name in list(self._timers):
                self.cancel(name)
        self._out.put(None)

    def to_config(self):
        config = {
            'profile': self.profile_id, 'model': self.model, 'name': self.name,
            'options': dict(self.opts),
            'transports': [t.to_config() for t in self.transports],
        }
        if self.start_offline:
            config['start_offline'] = True
        return config
