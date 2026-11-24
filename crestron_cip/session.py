"""
The CIP conversation as state machines with no sockets: feed each packet in, send whatever comes back.

    PanelSession       a touch panel / XPanel / CCC at one IP ID (what the processor's panel slot talks to)
    ProcessorSession   a processor's end of one panel connection (a fake processor for tests and offline benches)

Both keep the join tables, so `get` answers what the other side last said. Neither has a clock: the owner sends
`heartbeat()` every `HEARTBEAT_SECONDS` (python-cipclient sends one after 15 s idle; the processor answers each).

The sequence, as an RMC4 runs it (recorded on the wire):

    processor  0f 00 01 02                   registration request
    panel      01 ... <ipid> ...             register
    processor  02 00 04 00 00 00 1f          registered
    panel      05 .. 03 00                   update request
    processor  every join the slot holds, 05 .. 03 16, 05 .. 03 1c (end of query)
    panel      05 .. 03 1d                   end-of-query ack; then the panel's own joins
"""
import time

from . import protocol as p

HEARTBEAT_SECONDS = 10


class _Joins:
    def __init__(self):
        self.d, self.a, self.s = {}, {}, {}

    def table(self, kind):
        return {'d': self.d, 'a': self.a, 's': self.s}[kind[0]]

    def items(self):
        for kind, t in (('d', self.d), ('a', self.a), ('s', self.s)):
            for j, v in sorted(t.items()):
                yield kind, j, v


class _Session:
    def __init__(self):
        self.joins_in, self.joins_out = _Joins(), _Joins()
        self.listeners = []      # fn(kind, join, value) for every incoming join change
        self.slot_listeners = [] # fn(slot, kind, join, value) for a slot's joins (a multi-slot device, DM NVX)
        self.slots_in = {}       # (slot, kind, join) -> value
        self.events = []         # (monotonic time, text) notes of protocol steps, for logs and tests
        self.packets_in = 0

    def on_join(self, fn):
        self.listeners.append(fn)

    def on_slot_join(self, fn):
        self.slot_listeners.append(fn)

    def get_slot(self, slot, kind, join, default=None):
        """What the other side last sent on one of a slot's joins."""
        return self.slots_in.get((slot, kind[0], join), default)

    def get(self, kind, join, default=None):
        """What the other side last sent on a join ('d', 'a' or 's')."""
        return self.joins_in.table(kind).get(join, default)

    def note(self, text):
        self.events.append((time.monotonic(), text))

    def _incoming_joins(self, d):
        for kind, join, value in d.get('slot_joins', []):
            k = 'a' if kind == 'analog' else 's' if 'serial' in kind else 'd'
            key = (d['slot'], k, join)
            if k == 's' or self.slots_in.get(key) != value:   # a serial always: COM data repeats
                self.slots_in[key] = value
                for fn in self.slot_listeners:
                    fn(d['slot'], k, join, value)
        for kind, join, value in d.get('joins', []):
            k = 'a' if kind == 'analog' else 's' if 'serial' in kind else 'd'
            if kind.startswith('smart-object') or 'smart_object' in d:
                k = 'so{}:{}'.format(d.get('smart_object', '?'), k)
                self.note('smart object join {} {} = {!r}'.format(k, join, value))
                continue
            table = self.joins_in.table(k)
            if table.get(join) != value:
                table[join] = value
                for fn in self.listeners:
                    fn(k, join, value)

    def heartbeat(self):
        return p.heartbeat()


class PanelSession(_Session):
    """A panel at `ipid`. It signs on as a generic XPanel (`register_body`, the protocol.register template), or as a
    CCC when `device` is given: a dict of protocol.device_sign_on's arguments (mac, and optionally maker, model, name).
    status: 'connecting' -> 'registered' -> 'online'; 'refused' or 'disconnected' end it. A processor's CCC-110-T slot
    never sends end-of-query (it stops after 0x16, to a real CCC too), so there 'registered' is as far as it goes."""

    def __init__(self, ipid, register_body=p.XPANEL_REGISTER_BODY, device=None):
        super().__init__()
        self.ipid, self.register_body, self.device = ipid, register_body, device
        self.processor_name = None
        self.status = 'connecting'
        self.update_steps = []   # the processor's update-request steps, in order (the CCC slot stops after 0x16)

    def receive(self, raw):
        """One packet from the processor; returns the packets to send back."""
        self.packets_in += 1
        ptype, payload = raw[0], raw[3:]
        out = []
        if ptype == p.REGISTRATION_REQUEST:
            if self.device:
                out.append(p.device_sign_on(self.ipid, **self.device))
            else:
                out.append(p.register(self.ipid, self.register_body))
            self.note('registration request: signing on at 0x{:02X}{}'.format(self.ipid, ' as a device' if self.device else ''))
        elif ptype == p.DEVICE_SIGN_ON_RESULT:
            self.status, self.processor_name = 'registered', p.decode(raw).get('text')
            self.note('registered with {}'.format(self.processor_name))
            out.append(p.update_request())
        elif ptype == p.REGISTER_RESULT:
            if payload == p.REGISTERED_OK:
                self.status = 'registered'
                self.note('registered')
                out.append(p.update_request())
            else:
                self.status = 'refused'
                self.note('registration refused: ' + p.decode(raw)['result'])
        elif ptype == p.DISCONNECT:
            self.status = 'disconnected'
            self.note('processor disconnected')
        elif ptype == p.HEARTBEAT:
            out.append(p.heartbeat_response())
        elif ptype in (p.DATA, p.SERIAL):
            d = p.decode(raw)
            step = d.get('control')
            if step:
                self.update_steps.append(step)
                if step == 'end-of-query':
                    self.status = 'online'
                    self.note('update complete: online')
                    out.append(p.end_of_query_ack())
                    out.append(p.heartbeat())
                    out.extend(self._resend())
                elif step == 'update-request':
                    self.note('processor asked for an update')
                    out.extend(self._resend())
            self._incoming_joins(d)
        return out

    def _resend(self):
        items = list(self.joins_out.items())
        out = [p.digitals([(j, v) for k, j, v in items if k == 'd'])] if any(k == 'd' for k, _, _ in items) else []
        out += [p.analog(j, v) for k, j, v in items if k == 'a']
        out += [p.serial(j, v) for k, j, v in items if k == 's']
        return out

    # what the panel sends; each returns the packet (remembered, so a reconnect sends the state again)
    def digital(self, join, value, button=False):
        self.joins_out.d[join] = 1 if value else 0
        return p.digital(join, value, button)

    def press(self, join):
        return self.digital(join, 1, button=True)

    def release(self, join):
        return self.digital(join, 0, button=True)

    def analog(self, join, value):
        self.joins_out.a[join] = value
        return p.analog(join, value)

    def serial(self, join, text):
        self.joins_out.s[join] = text
        return p.serial(join, text)


class ProcessorSession(_Session):
    """A processor's end of one connection. `ipids` = the IP IDs it accepts. Set what the processor drives with
    digital/analog/serial (each returns the packet to send now, or None before the panel is online); `connect()` is
    the first packet. `penultimate_only` stops the update after 0x16, as the RMC4 does at a CCC-110-T slot."""

    def __init__(self, ipids, penultimate_only=False):
        super().__init__()
        self.ipids, self.penultimate_only = set(ipids), penultimate_only
        self.ipid, self.status = None, 'waiting'
        self.registrations = []  # the raw register packets received, for checking what a panel sends

    def connect(self):
        return p.registration_request()

    def receive(self, raw):
        self.packets_in += 1
        ptype = raw[0]
        out = []
        if ptype in (p.REGISTER, p.SIGN_ON, p.DEVICE_SIGN_ON):
            self.registrations.append(raw)
            ipid = p.decode(raw).get('ipid')
            if ipid in self.ipids:
                self.ipid, self.status = ipid, 'registered'
                out.append(p.device_sign_on_result(ipid) if ptype == p.DEVICE_SIGN_ON else p.register_result(True))
            else:
                out.append(p.register_result(False))   # what a CCC is told for an undefined IP ID is not measured
        elif ptype == p.HEARTBEAT:
            out.append(p.heartbeat_response())
        elif ptype in (p.DATA, p.SERIAL):
            d = p.decode(raw)
            step = d.get('control')
            if step == 'update-request':
                out.extend(self.update())
            elif step == 'end-of-query-ack':
                self.status = 'online'
            elif step in ('status-text', 'device-detail'):
                self.note('{}: {}'.format(step, d.get('text', '')))
            self._incoming_joins(d)
        return out

    def update(self):
        items = list(self.joins_out.items())
        out = []
        digs = [(j, v) for k, j, v in items if k == 'd']
        if digs:
            out.append(p.digitals(digs))
        if any(k == 'a' for k, _, _ in items):
            out.append(p.analogs([(j, v) for k, j, v in items if k == 'a']))
        out += [p.serial(j, v) for k, j, v in items if k == 's']
        out.append(p.control(p.CONTROL_UPDATE_PENULTIMATE))
        if not self.penultimate_only:
            out.append(p.end_of_query())
        return out

    def _drive(self, table, join, value, pkt):
        table[join] = value
        return pkt if self.status in ('registered', 'online') else None

    def digital(self, join, value):
        return self._drive(self.joins_out.d, join, 1 if value else 0, p.digital(join, value))

    def analog(self, join, value):
        return self._drive(self.joins_out.a, join, value, p.analog(join, value))

    def serial(self, join, text):
        return self._drive(self.joins_out.s, join, text, p.serial(join, text))
