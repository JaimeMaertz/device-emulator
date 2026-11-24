"""
Biamp Tesira (SERVER, SERVER-IO, TesiraFORTÉ) over the Tesira Text Protocol: SSH 22, Telnet 23,
RS-232 115200.

What the Tesira answers for is its **design**: the blocks in the configuration file, by instance
tag. Here the design comes from the bench (`"design"` on the device):

    "design": {
        "ProgramLevel": {"block": "Level Control", "channels": 3},
        "MicMute": {"block": "Mute Control", "channels": 7},
        "RoomCombiner": {"block": "Room Combiner", "rooms": 3, "walls": [[1, 2], [2, 3]],
                         "values": {"wallState 2": false}}
    }

`block` names a type in `emulator/tesira_blocks/` (one module per type, each its attribute table
from Biamp's Tesira Text Protocol attribute tables); the other keys are the type's parameters;
`values` sets starting values by `"<attribute> <index>..."`. A tag that isn't in the design gets
the real "address not found" error, so a mistyped instance tag in room code fails here the way
it fails on site.

Sources, per behavior:
- Documented (Tesira help, System Control): command syntax, case sensitivity, quoted tags,
  verbose / non-verbose replies, the error lines for an unknown tag, a bad verb, an unknown
  attribute, missing parameters, an unparseable value and an index out of range; subscription
  replies (a publish of the current value, then +OK) and their loss on reboot or a new
  configuration; the Telnet option sequence and "Welcome to the Tesira Text Protocol Server";
  CR LF line endings; float32 values printed to six decimals.
- Extron catalog module (biam_dsp_TesiraSeries_v1_18_3_0): every command is echoed before its
  reply (its reply patterns all start with the echo); `-ERR ALREADY_SUBSCRIBED` for a label that's
  already subscribed and `-ERR NOT_SUBSCRIBED` for unsubscribing one that isn't; the
  activeFaultList reply's opening; `DEVICE get version` as a quoted dotted string.
- Biamp's Crestron module help (Command Processor IP v3.0, revision note for v2.2): firmware
  3.1.0.144 and later wants SSH keyboard-interactive authentication.
- Biamp's Crestron library (BiampTesiraLib3 3.3), read from its code and run on a VC-4: it waits
  for a line with "Welcome" and misses one sent the instant its shell opens; `SESSION get alias`
  answers the block's address, `[device classCode instance]` when not verbose, with the class
  codes that library names.
- Measured on a SERVER-IO on firmware 5.7.1.4, over SSH: the
  server is `SSH-2.0-OpenSSH_10.4` and offers publickey and keyboard-interactive only, so
  "none" and a plain password are refused (clients fall back to keyboard-interactive); the
  shell prints `Last login: <date> from <client IP>` 8 ms after the shell request and the
  welcome 0.78 s after it; new sessions start verbose; `SESSION get aliases` is every tag plus
  `DEVICE` in plain ASCII order; a verb an attribute doesn't take answers
  `-ERR operation set not supported on attribute: numChannels`; a Level subscription publishes
  once at subscribe, then `+OK`, then only on change (none in 5 s at a 100 ms rate). Protected
  (the `ssh_user` / `ssh_password` options): the configured user is a controller; 'default' still
  logs in with any password and every block command answers `-ERR PERMISSION_DENIED`, `get` too,
  after the verb check; SESSION and DEVICE still answer.
- Guessed (marked so a real unit can confirm): the keyboard-interactive prompt text;
  the error for a value out of range (option: clamp instead); group numbering in the Room
  Combiner; meters publishing every interval; what a reboot restores (the design's starting
  values); the SSH host key type (RSA here, ed25519 on the unit).

Not emulated: user levels between none and controller (observer, admin), more than one
configured user, protection over Telnet and RS-232, the dialer and call state, audio itself.
"""
import itertools
import re
import time

from ..core import Device, Button, Readout, FlagOption, TextOption, ChoiceOption
from ..tesira_blocks import Block, TtpError, fmt, lookup

BANNER = b'\r\nWelcome to the Tesira Text Protocol Server...\r\n'
# Block class codes, from the constants in Biamp's Crestron library (BiampTesiraLib3 3.3,
# TesiraClassCodes). A type it has no constant for answers UNKNOWN_CLASS_CODE, a made-up number.
CLASS_CODES = {'Audio Input': 1024, 'Audio Output': 1025, 'Standard Mixer': 1537, 'Matrix Mixer': 1538,
               'Room Combiner': 1539, 'Router': 4096, 'Source Selector': 4097, 'Level Control': 5120}
UNKNOWN_CLASS_CODE = 9000
VERBS = ('get', 'set', 'increment', 'decrement', 'toggle', 'subscribe', 'unsubscribe')

IAC, WILL, WONT, DO, DONT, SB, SE = 0xFF, 0xFB, 0xFC, 0xFD, 0xFE, 0xFA, 0xF0
# The documented negotiation, as (command, option), in order
NEGOTIATION = [(DO, 0x18), (DO, 0x20), (DO, 0x23), (DO, 0x27), (DO, 0x24), (WILL, 0x03), (DO, 0x01),
               (DO, 0x22), (DO, 0x1F), (WILL, 0x05), (DO, 0x21), (WILL, 0x01), (DO, 0x06), (DO, 0x00)]

DEVICE_SERVICES = (
    'clearEventLogs', 'clearLogs', 'clearWorkplace', 'defaultLogConfig', 'deleteConfigData',
    'getLocalCopyOfFile', 'injectDanteLicenseKey', 'manualFailover', 'reboot', 'rebootERD',
    'recallPreset', 'recallPresetByName', 'recallPresetShowFailures', 'resetWebServerCredentials',
    'restoreERDToFactoryDefaults', 'savePreset', 'savePresetByName', 'sleep', 'startAudio',
    'startMedia', 'startPartitionAudio', 'startPartitionMedia', 'stopAudio', 'stopMedia',
    'stopPartitionAudio', 'stopPartitionMedia', 'syncERDFirmware', 'verifyDanteLicenseKey', 'wake')
DEVICE_ATTRS = (
    'activeFaultList', 'avbPDelayThreshold', 'blockInfo', 'clientLogLevel', 'danteInfo', 'deviceInfo',
    'discoveredServers', 'dnsConfig', 'dnsStatus', 'enableDuplicateSuppression', 'ERDavbPDelayThreshold',
    'facilityLogLevel', 'hostname', 'hostTable', 'httpsEnabled', 'igmpEnabled', 'ipConfig', 'ipStatus',
    'knownRedundantDeviceStates', 'mDNSEnabled', 'msrpInfo', 'networkPortInfo', 'networkPortMode',
    'networkStatus', 'poeEnabled', 'poeInfo', 'productRevision', 'ptpInfo', 'rstpEnabled',
    'serialNumber', 'sshDisabled', 'telnetDisabled', 'version', 'VoIP2NetworkStatus')
DEVICE_FLAGS = ('enableDuplicateSuppression', 'httpsEnabled', 'igmpEnabled', 'mDNSEnabled', 'rstpEnabled',
                'sshDisabled', 'telnetDisabled')
SESSION_ATTRS = ('alias', 'aliases', 'detailedResponse', 'sessionID', 'ttpLoggingEnable', 'verbose')
FAULTS = {
    'none': ('INDICATOR_NONE_IN_DEVICE', 'No fault in device'),
    'minor': ('INDICATOR_MINOR_IN_DEVICE', 'Minor Fault in Device'),
    'major': ('INDICATOR_MAJOR_IN_DEVICE', 'Major Fault in Device'),
}

# A design to play with when a Tesira is added from the UI without one
DEMO_DESIGN = {
    'Level1': {'block': 'Level Control', 'channels': 4},
    'Mute1': {'block': 'Mute Control', 'channels': 3},
    'Mixer1': {'block': 'Matrix Mixer', 'inputs': 4, 'outputs': 4},
    'RoomCombiner1': {'block': 'Room Combiner', 'rooms': 3, 'walls': 2},
}


class Tok:
    def __init__(self, text, pos, quoted=False):
        self.text, self.pos, self.quoted = text, pos, quoted


def tokenize(line):
    """Space-separated fields; "double quoted" fields may hold spaces and may abut ("1""1");
    a [list] or {object} is one field."""
    toks, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c in ' \t':
            i += 1
        elif c == '"':
            j = line.find('"', i + 1)
            if j < 0:
                raise TtpError('-ERR Parse error at {}: could not parse value'.format(i))
            toks.append(Tok(line[i + 1:j], i, True))
            i = j + 1
        elif c in '[{':
            close, depth, j = {'[': ']', '{': '}'}[c], 0, i
            while j < n:
                if line[j] == c:
                    depth += 1
                elif line[j] == close:
                    depth -= 1
                    if not depth:
                        break
                j += 1
            toks.append(Tok(line[i:j + 1], i))
            i = j + 1
        else:
            j = i
            while j < n and line[j] not in ' \t"':
                j += 1
            toks.append(Tok(line[i:j], i))
            i = j
    return toks


def not_enough(line):
    return TtpError('-ERR Parse error at {}: not enough parameters supplied'.format(len(line)))


def bad_value(tok):
    return TtpError('-ERR Parse error at {}: could not parse value'.format(tok.pos))


class Tesira(Device):
    profile_id = 'biamp_tesira'
    profile_name = 'Biamp Tesira'
    kind = 'dsp'
    log_as = 'ascii'
    default_tcp_port = 23
    default_ssh_port = 22
    # Measured on a SERVER-IO, firmware 5.7.1.4: the server offers publickey and
    # keyboard-interactive only (no password, no "none"); a client asking for password falls back
    # to keyboard-interactive, as paramiko and the Extron processors do.
    ssh_auths = 'publickey,keyboard-interactive'
    ssh_version = 'SSH-2.0-OpenSSH_10.4'
    ssh_greeting_delay = 0.78   # seconds after the shell opens before the welcome line (measured)

    def ssh_shell_greeting(self, address):
        """What the shell prints at once, before the welcome: the OpenSSH last-login line, 8 ms
        after the shell request on the real unit."""
        now = time.localtime()
        stamp = '{} {:>2} {}'.format(time.strftime('%a %b', now), now.tm_mday, time.strftime('%H:%M:%S %Y', now))
        return 'Last login: {} from {}\r\r\n\r\n\r\n'.format(stamp, address[0]).encode()
    serial_defaults = {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    models = {'Tesira SERVER-IO': {}, 'Tesira SERVER': {}, 'TesiraFORTE': {}}
    options = [
        FlagOption('telnet', 'Telnet option negotiation on TCP (off = raw TCP, banner at once)', True),
        FlagOption('echo', 'Echo each command before its reply', True),
        FlagOption('verbose_default', 'New sessions start verbose', True),
        FlagOption('clamp', 'Clamp out-of-range values instead of refusing them', False),
        TextOption('ssh_user', "Protected system: the user the control system logs in as (blank = unprotected, "
                               "'default' with any password)", ''),
        TextOption('ssh_password', 'Protected system: that user\'s password', ''),
        TextOption('firmware', 'Firmware version', '4.5.0.10'),
        TextOption('serial_number', 'Serial number', '01842224'),
        ChoiceOption('fault', 'Fault indicator', list(FAULTS), 'none'),
    ]
    notes = __doc__

    def __init__(self, name, model=None, options=None, design=None):
        self.presets = {}
        self._ids = itertools.count(1)
        self._current, self._tail = None, []
        super().__init__(name, model, options, design if design is not None else dict(DEMO_DESIGN))

    # ----- the design
    def reset(self):
        self._loading = True
        self.blocks, self._keys = {}, {}
        for tag, spec in (self.design or {}).items():
            btype = lookup(spec.get('block', ''))
            if btype is None:
                self.log('warn', 'design: {} has unknown block type {!r}; left out'.format(tag, spec.get('block')))
                continue
            params = {k: v for k, v in spec.items() if k not in ('block', 'values')}
            block = Block(tag, btype, params, self.state, self._changed)
            self.blocks[tag] = block
            for code, a in btype.attrs.items():
                for index in block.all_indexes(a):
                    self._keys[block.key(code, index)] = (block, a, index)
            for where, value in (spec.get('values') or {}).items():
                parts = where.split()
                a = btype.attrs.get(parts[0])
                if a is None:
                    self.log('warn', 'design: {} has no attribute {}'.format(tag, parts[0]))
                    continue
                block.apply('set', a, tuple(int(p) for p in parts[1:]), value, clamp=True)
        self.state.update(audio=True, subscriptions=0)
        self._loading = False

    def to_config(self):
        config = super().to_config()
        config['design'] = self.design
        return config

    # ----- connections
    def _session(self, link):
        st = link.state
        if 'verbose' not in st:
            st.update(verbose=self.opts['verbose_default'], subs={}, negotiating=None, iac=b'',
                      detailedResponse=False, ttpLoggingEnable=False, sessionID=next(self._ids))
        return st

    def connected(self, link):
        st = self._session(link)
        if link.kind == 'tcp' and self.opts['telnet']:
            st['negotiating'] = set(NEGOTIATION)
            return b''.join(bytes([IAC, c, o]) for c, o in NEGOTIATION)
        if link.kind in ('tcp', 'ssh'):
            return BANNER
        return None

    def _alias_address(self, st, tag):
        """`SESSION get alias <tag>`: the block's address (device, class code, instance number).

        Biamp's Crestron library (BiampTesiraLib3 3.3) asks this for every tag before anything
        else, with verbose off, takes the text between [ and ], splits it on spaces, wants
        exactly three parts and keeps the second as the class code; any other reply quarantines
        the control module. So the non-verbose form is `+OK [1 5120 3]`. The verbose form is a
        guess shaped like the address in the "address not found" error."""
        names = list(self.blocks)
        instance = names.index(tag) + 1 if tag in names else 0
        code = CLASS_CODES.get(self.blocks[tag].type.name, UNKNOWN_CLASS_CODE) if tag in self.blocks else 0
        if st['verbose']:
            return '+OK "value":{{"deviceId":1 "classCode":{} "instanceNum":{}}}'.format(code, instance)
        return '+OK [1 {} {}]'.format(code, instance)

    def ssh_login(self, username, password):
        """Unprotected (no `ssh_user`): 'default' with any password is a controller. Protected: the
        configured user with its password is a controller, and 'default' still gets in with any
        password but with no access to blocks (measured on a SERVER-IO, 5.7.1.4)."""
        user = self.opts['ssh_user'].strip()
        if not user:
            return 'controller' if username == 'default' else None
        if username == user and password == self.opts['ssh_password']:
            return 'controller'
        if username == 'default':
            return 'none'
        return None

    def disconnected(self, link):
        for sub in link.state.get('subs', {}).values():
            self.cancel(sub['name'])
        self._count_subs()

    def receive(self, link, data):
        st = self._session(link)
        if link.kind == 'tcp' and (st['negotiating'] or IAC in data or st['iac']):
            data = self._telnet(link, st, st['iac'] + data)
        super().receive(link, data)

    def _telnet(self, link, st, data):
        """Strip Telnet commands from the stream, noting answers to the negotiation; when the last
        one is answered, the session goes ahead with the banner."""
        out, i, st['iac'] = bytearray(), 0, b''
        while i < len(data):
            b = data[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= len(data):
                st['iac'] = data[i:]
                break
            cmd = data[i + 1]
            if cmd == IAC:
                out.append(IAC)
                i += 2
            elif cmd in (WILL, WONT, DO, DONT):
                if i + 2 >= len(data):
                    st['iac'] = data[i:]
                    break
                opt = data[i + 2]
                asked = DO if cmd in (WILL, WONT) else WILL
                if st['negotiating']:
                    st['negotiating'].discard((asked, opt))
                i += 3
            elif cmd == SB:
                end = data.find(bytes([IAC, SE]), i)
                if end < 0:
                    st['iac'] = data[i:]
                    break
                i = end + 2
            else:
                i += 2
        if st['negotiating'] is not None and not st['negotiating']:
            st['negotiating'] = None
            self.log('info', 'Telnet negotiation complete', link)
            self._send(link, BANNER, 'push')
        return bytes(out)

    def frames(self, buffer):
        out = []
        while True:
            m = re.search(rb'[\r\n]', buffer)
            if not m:
                return out, buffer
            line, buffer = buffer[:m.start()], buffer[m.end():]
            if line.strip():
                out.append(line)

    # ----- one command
    def handle(self, frame, link):
        st = self._session(link)
        if st['negotiating']:
            self.log('info', 'command before Telnet negotiation finished: ignored', link)
            return None
        line = frame.decode('latin-1')
        out = [line] if self.opts['echo'] else []
        self._current, self._tail = link, []
        try:
            out += self.command(line, st, link)
        except TtpError as e:
            out.append(str(e))
            self.log('info', str(e), link)
        finally:
            tail, self._current, self._tail = self._tail, None, []
        return ''.join(l + '\r\n' for l in out + tail).encode('latin-1')

    @staticmethod
    def ok(st, value, kind='float'):
        """A get's reply: `+OK "value":x` verbose, `+OK x` not."""
        text = fmt(value, kind)
        return ['+OK "value":' + text if st['verbose'] else '+OK ' + text]

    def command(self, line, st, link):
        toks = tokenize(line)
        if not toks:
            return []
        tag = toks[0].text
        if tag == 'SESSION':
            return self.session_command(line, toks, st)
        if tag == 'DEVICE':
            return self.device_command(line, toks, st, link)
        block = self.blocks.get(tag)
        if block is None:
            raise TtpError('-ERR address not found: {"deviceId":0 "classCode":0 "instanceNum":0}')
        if len(toks) < 2:
            raise not_enough(line)
        verb = toks[1]
        if verb.text not in VERBS:
            raise TtpError('-ERR Parse error at {}: verb was not one of the commands supported by Services'
                           .format(verb.pos))
        if len(toks) < 3:
            raise not_enough(line)
        a = block.type.attrs.get(toks[2].text)
        if a is None:
            raise TtpError("-ERR '{}' is not supported by {}::Attributes".format(toks[2].text, block.type.ttp_class))
        if verb.text not in a.commands:
            raise TtpError('-ERR operation {} not supported on attribute: {}'.format(verb.text, a.code))
        # On a protected system the 'default' user gets in but may not touch a block, get included;
        # the verb check above still answers first (measured, SERVER-IO 5.7.1.4).
        if link is not None and link.state.get('ssh_level') == 'none':
            raise TtpError('-ERR PERMISSION_DENIED')
        rest = toks[3:]
        index = self.indexes(line, block, a, rest)
        rest = rest[len(a.indexes):]
        if verb.text == 'get':
            value = block.get(a, index)
            if a.kind == 'opaque' and value is None:
                self.log('warn', '{} {} is not emulated (no value in its block type)'.format(block.tag, a.code), link)
                raise TtpError('-GENERAL_FAILURE')
            return self.ok(st, value, kind=self.kind_of(a))
        if verb.text == 'subscribe':
            return self.subscribe(link, st, block, a, index, rest)
        if verb.text == 'unsubscribe':
            return self.unsubscribe(st, block, a, index, rest)
        value = None
        if verb.text in ('set', 'increment', 'decrement'):
            if not rest:
                raise not_enough(line)
            value = self.value(a, rest[0], numeric=verb.text != 'set')
        block.apply(verb.text, a, index, value, clamp=self.opts['clamp'])
        return ['+OK']

    @staticmethod
    def kind_of(a):
        return a.element.kind if a.kind == 'array' else a.kind

    @staticmethod
    def indexes(line, block, a, toks):
        index = []
        for n, name in enumerate(a.indexes):
            if n >= len(toks):
                raise not_enough(line)
            tok = toks[n]
            if not re.fullmatch(r'\d+', tok.text):
                raise bad_value(tok)
            i, count = int(tok.text), block.count(name)
            if i < 1 or (count is not None and i > count):
                field = re.sub(r' (\w)', lambda m: m.group(1).upper(), name) + 'Index'
                raise TtpError('-ERR INVALID_PARAMETER Index out of range:{} min:1 max:{} received:{}'.format(
                    field, count, i))
            index.append(i)
        return tuple(index)

    def value(self, a, tok, numeric=False):
        text = tok.text
        if numeric or a.kind in ('float', 'int'):
            if a.kind not in ('float', 'int'):
                raise bad_value(tok)
            try:
                return float(text)
            except ValueError:
                raise bad_value(tok)
        if a.kind == 'bool':
            # Any capitalisation: Biamp's Crestron library sends `set mute 1 True` and `False`
            # (.NET's bool text), and its mutes work on real units.
            if text.lower() in ('true', 'false') and not tok.quoted:
                return text.lower() == 'true'
            raise bad_value(tok)
        if a.kind == 'enum':
            if text in a.choices:
                return text
            raise bad_value(tok)
        if a.kind == 'string':
            return text
        self.log('warn', 'set {} is not emulated (its value type is not modeled)'.format(a.code))
        raise TtpError('-GENERAL_FAILURE')

    # ----- subscriptions
    def subscribe(self, link, st, block, a, index, rest):
        label = rest[0].text if rest else ''
        interval = 0
        if len(rest) > 1:
            if not re.fullmatch(r'\d+', rest[1].text):
                raise bad_value(rest[1])
            interval = int(rest[1].text)
        key = (block.tag, a.code, index, label)
        if key in st['subs']:
            raise TtpError('-ERR ALREADY_SUBSCRIBED')
        sub = {'link': link, 'block': block, 'attr': a, 'index': index, 'label': label, 'interval': interval,
               'verbose': st['verbose'], 'name': 'sub {}'.format(next(self._ids)), 'dirty': False,
               'metered': a.code in block.type.metered}
        st['subs'][key] = sub
        self._count_subs()
        if sub['metered'] and interval:
            self.after(sub['name'], interval / 1000.0, lambda s=sub: self._tick(s))
        return [self._publish_line(sub), '+OK']

    def unsubscribe(self, st, block, a, index, rest):
        key = (block.tag, a.code, index, rest[0].text if rest else '')
        sub = st['subs'].pop(key, None)
        if sub is None:
            raise TtpError('-ERR NOT_SUBSCRIBED')
        self.cancel(sub['name'])
        self._count_subs()
        return ['+OK']

    def _count_subs(self):
        self.set('subscriptions', sum(len(l.state.get('subs', {})) for l in self.links), log=False)

    def _publish_line(self, sub):
        value = fmt(sub['block'].get(sub['attr'], sub['index']), self.kind_of(sub['attr']))
        if sub['verbose']:
            return '! "publishToken":"{}" "value":{}'.format(sub['label'], value)
        return '! "{}" {}'.format(sub['label'], value)

    def _emit(self, sub):
        line = self._publish_line(sub)
        if sub['link'] is self._current:
            self._tail.append(line)
        else:
            self.push((line + '\r\n').encode('latin-1'), links=[sub['link']])

    def _alive(self, sub):
        return sub['link'].state.get('subs', {}).get(
            (sub['block'].tag, sub['attr'].code, sub['index'], sub['label'])) is sub

    def _tick(self, sub):
        """A meter subscription: publish every interval."""
        if self._alive(sub):
            self._emit(sub)
            self.after(sub['name'], sub['interval'] / 1000.0, lambda: self._tick(sub))

    def _cooled(self, sub):
        """The end of a subscription's interval: publish what changed during it."""
        if self._alive(sub) and sub['dirty']:
            sub['dirty'] = False
            self._emit(sub)
            self.after(sub['name'], sub['interval'] / 1000.0, lambda: self._cooled(sub))

    def _changed(self, block, code, index, value):
        if getattr(self, '_loading', True):
            return
        links = set(self.links)
        if self._current is not None:
            links.add(self._current)
        for link in links:
            for sub in list(link.state.get('subs', {}).values()):
                a = sub['attr']
                if sub['block'] is not block:
                    continue
                if not ((a.code == code and sub['index'] == index) or (a.element and a.element.code == code)):
                    continue
                if sub['metered'] and sub['interval']:
                    continue
                if self.pending(sub['name']):
                    sub['dirty'] = True
                    continue
                self._emit(sub)
                if sub['interval']:
                    self.after(sub['name'], sub['interval'] / 1000.0, lambda s=sub: self._cooled(s))

    # ----- SESSION
    def session_command(self, line, toks, st):
        if len(toks) < 2:
            raise not_enough(line)
        verb = toks[1]
        if verb.text not in ('get', 'set', 'toggle'):
            raise TtpError('-ERR Parse error at {}: verb was not one of the commands supported by Services'
                           .format(verb.pos))
        if len(toks) < 3:
            raise not_enough(line)
        attr = toks[2].text
        if attr not in SESSION_ATTRS:
            raise TtpError("-ERR '{}' is not supported by TextSession::Attributes".format(attr))
        if attr == 'aliases' and verb.text == 'get':
            names = sorted(list(self.blocks) + ['DEVICE'])
            text = '[' + ' '.join('"{}"'.format(n) for n in names) + ']'
            return ['+OK "list":' + text if st['verbose'] else '+OK ' + text]
        if attr == 'alias' and verb.text == 'get':
            if len(toks) < 4:
                raise not_enough(line)
            if toks[3].text not in self.blocks and toks[3].text != 'DEVICE':
                raise TtpError('-ERR address not found: {"deviceId":0 "classCode":0 "instanceNum":0}')
            return [self._alias_address(st, toks[3].text)]
        if attr == 'sessionID' and verb.text == 'get':
            return self.ok(st, st['sessionID'], kind='int')
        if attr in ('verbose', 'detailedResponse', 'ttpLoggingEnable'):
            if verb.text == 'get':
                return self.ok(st, st[attr])
            if verb.text == 'toggle':
                st[attr] = not st[attr]
                return ['+OK']
            if len(toks) < 4:
                raise not_enough(line)
            if toks[3].text not in ('true', 'false'):
                raise bad_value(toks[3])
            st[attr] = toks[3].text == 'true'
            return ['+OK']
        raise TtpError("-ERR Parse error at {}: '{}' is not supported by {}".format(verb.pos, verb.text, attr))

    # ----- DEVICE
    def device_command(self, line, toks, st, link):
        if len(toks) < 2:
            raise not_enough(line)
        verb = toks[1]
        if verb.text in DEVICE_SERVICES:
            return self.device_service(line, verb.text, toks[2:], link)
        if verb.text not in ('get', 'set', 'toggle', 'increment', 'decrement', 'subscribe', 'unsubscribe'):
            raise TtpError('-ERR Parse error at {}: verb was not one of the commands supported by Services'
                           .format(verb.pos))
        if len(toks) < 3:
            raise not_enough(line)
        attr = toks[2].text
        if attr not in DEVICE_ATTRS:
            raise TtpError("-ERR '{}' is not supported by DeviceInterface::Attributes".format(attr))
        if verb.text == 'get':
            if attr == 'version':
                return self.ok(st, self.opts['firmware'])
            if attr == 'serialNumber':
                return self.ok(st, self.opts['serial_number'])
            if attr == 'hostname':
                return self.ok(st, 'TesiraServer' + self.opts['serial_number'][-2:])
            if attr == 'activeFaultList':
                fid, name = FAULTS[self.opts['fault']]
                text = '[{{"id":{} "name":"{}" "faults":[] "serialNumber":"{}"}}]'.format(
                    fid, name, self.opts['serial_number'])
                return ['+OK "value":' + text if st['verbose'] else '+OK ' + text]
            if attr in DEVICE_FLAGS:
                return self.ok(st, bool(self.state.get('DEVICE ' + attr, attr == 'mDNSEnabled')))
        elif verb.text in ('set', 'toggle') and attr in DEVICE_FLAGS:
            key = 'DEVICE ' + attr
            if verb.text == 'toggle':
                self.set(key, not self.state.get(key, attr == 'mDNSEnabled'))
            else:
                if len(toks) < 4:
                    raise not_enough(line)
                if toks[3].text not in ('true', 'false'):
                    raise bad_value(toks[3])
                self.set(key, toks[3].text == 'true')
            return ['+OK']
        self.log('warn', 'DEVICE {} {} is not emulated'.format(verb.text, attr), link)
        raise TtpError('-GENERAL_FAILURE')

    def device_service(self, line, service, args, link):
        if service in ('recallPreset', 'savePreset', 'recallPresetByName', 'savePresetByName'):
            if not args:
                raise not_enough(line)
            name = args[0].text
            if service.startswith('save'):
                self.presets[name] = {k: v for k, v in self.state.items() if k in self._keys}
                self.log('info', 'preset {} saved'.format(name), link)
                return ['+OK']
            snapshot = self.presets.get(name)
            if snapshot is None:
                self.log('info', 'preset {} was never saved here'.format(name), link)
                raise TtpError('-GENERAL_FAILURE')
            for key, value in snapshot.items():
                block, a, index = self._keys[key]
                if self.state.get(key) != value:
                    block.type.write(block, a, index, value)
            self.log('info', 'preset {} recalled'.format(name), link)
            return ['+OK']
        if service in ('startAudio', 'stopAudio'):
            self.set('audio', service == 'startAudio')
            return ['+OK']
        if service == 'reboot':
            self.after('reboot', 0.2, self.power_cycle)
            return ['+OK']
        self.log('info', 'DEVICE {}: +OK only (not emulated)'.format(service), link)
        return ['+OK']

    # ----- the UI
    def controls(self):
        out = [Readout('audio', 'Audio running'), Readout('subscriptions', 'Subscriptions (all sessions)'),
               Button('resend_config', 'Resend configuration')]
        for block in self.blocks.values():
            out += block.type.controls(block)
        return out

    def panel(self, key, value):
        with self.lock:
            if key == 'resend_config':
                self.resend_configuration()
                return
            found = self._keys.get(key)
            if found is None:
                self.set(key, value)
                return
            block, a, index = found
            block.apply('set', a, index, value, clamp=True)

    def resend_configuration(self):
        """What a design sent from Tesira Software does to live sessions: every subscription is
        gone and the blocks start over from the design; connections stay up."""
        for link in list(self.links):
            self.disconnected(link)
            link.state.get('subs', {}).clear()
        for key in list(self._keys):
            self.state.pop(key, None)
        self.reset()
        self._count_subs()
        self.log('info', 'configuration resent: subscriptions dropped, blocks reset to the design')
