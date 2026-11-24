"""
TASCAM BD-MP1MKII Blu-ray player, serial command protocol (RS-232, and LAN port 9030 through the
same command set).

Command:  '!7' <command> CR          e.g. "!7PLY\\r" (Play), "!7OSD2\\r" (cursor right)
Reply:    the text "ack" for a command it knows, "nack" for anything else, with no CR after
          either.

Sources: the Extron module tasc_bluray_BD_MP1_v1_2_0_0 and its communication sheet (command
codes, 115200 8N1, TCP 9030 that closes while the player is off, 0.3 s pacing between commands),
and the replies of a real BD-MP1MKII, measured.

Fidelity:
- Measured: "ack" / "nack" as text with no CR; every code in the module's list answers "ack",
  Play and Stop too with no disc in; unknown text, an empty line and Denon '@0' codes answer
  "nack".
- From the comm sheet: the codes, Power On by serial only (the LAN port is closed while off),
  0.3 s between commands.
- Best guesses (no Tascam protocol manual on disk): commands while off answer "nack" except
  Power On; a command sooner than 0.3 s after the last is ignored with no answer (the option
  turns that off); Previous Chapter restarts the chapter unless it is in its first 3 s;
  transport commands do nothing without a disc but still answer "ack"; the player never sends
  anything unprompted. The module never reads a reply, so none of this changes what it does.
"""
import time

from ..core import Device, Button, Readout, Toggle, NumberOption, FlagOption

TRANSPORT_SPEED = {'Stop': 0, 'Play': 1, 'Pause': 0, 'Fast Forward': 8, 'Fast Reverse': -8,
                   'Slow Forward': 0.5, 'Slow Reverse': -0.5}
SCAN = {b'Ff': 'Fast Forward', b'Rf': 'Fast Reverse', b'Fs': 'Slow Forward', b'Rs': 'Slow Reverse'}

# Codes acknowledged without state: menus, cursor, info, subtitle, number keys
ACK_ONLY = {b'SMN', b'TMN', b'OMN', b'PMN', b'DSP', b'RET', b'ENT', b'HOM',
            b'OSD1', b'OSD2', b'OSD3', b'OSD4', b'SBT1'} | {b'NUM%d' % d for d in range(10)}


class TascamBluray(Device):
    profile_id = 'tascam_bluray'
    profile_name = 'TASCAM BD-MP1MKII Blu-ray'
    kind = 'bluray'
    models = {'BD-MP1MKII': {}}
    log_as = 'ascii'
    default_tcp_port = 9030
    serial_defaults = {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        NumberOption('titles', 'Disc: titles', 3, 1, 99),
        NumberOption('chapters', 'Disc: chapters per title', 12, 1, 99),
        NumberOption('chapter_minutes', 'Disc: minutes per chapter', 5, 1, 120),
        NumberOption('pacing', 'Ignore commands closer than (s)', 0.3, 0, 2),
        NumberOption('tray_seconds', 'Tray open/close time (s)', 2, 0, 30),
        FlagOption('start_on', 'Start powered on', True),
    ]
    notes = __doc__

    def reset(self):
        self.state.update(power='On' if self.opts['start_on'] else 'Off', tray='Closed', disc=True,
                          transport='Stop', title=1, pos=0.0, anchor=time.monotonic(),
                          mute=False, osd=True, lastMenu='-', ignored=0)

    # ----- disc position: (pos at anchor, anchor time, speed), computed when read
    def speed(self):
        return TRANSPORT_SPEED[self.state['transport']]

    def title_seconds(self):
        return self.opts['chapters'] * self.opts['chapter_minutes'] * 60

    def position(self):
        s = self.state
        p = s['pos'] + (time.monotonic() - s['anchor']) * self.speed()
        return max(0.0, min(p, self.title_seconds()))

    def _move(self, transport, pos=None):
        s = self.state
        s['pos'] = self.position() if pos is None else float(pos)
        s['anchor'] = time.monotonic()
        self.set('transport', transport)
        self.cancel('end')
        v = self.speed()
        if v > 0:
            self.after('end', (self.title_seconds() - s['pos']) / v, lambda: self._move('Stop', 0))
        elif v < 0:
            self.after('end', s['pos'] / -v, lambda: self._move('Play', 0))

    def chapter(self):
        return min(int(self.position() // (self.opts['chapter_minutes'] * 60)) + 1, self.opts['chapters'])

    def chapter_elapsed(self):
        return self.position() - (self.chapter() - 1) * self.opts['chapter_minutes'] * 60

    def time_text(self):
        t = int(self.position())
        return '{}:{:02}:{:02}'.format(t // 3600, t // 60 % 60, t % 60)

    def playable(self):
        s = self.state
        return s['power'] == 'On' and s['tray'] == 'Closed' and s['disc']

    # ----- front panel
    def controls(self):
        return [
            Readout('power', 'Power'),
            Button('power_on', 'Power On'), Button('power_off', 'Standby'),
            Readout('transport', 'Transport'),
            Button('play', 'Play'), Button('pause', 'Pause'), Button('stop', 'Stop'),
            Button('prev', '|<<'), Button('next', '>>|'),
            Button('open', 'Open'), Button('close', 'Close'),
            Readout('title', 'Title'),
            Readout('chapter', 'Chapter', get=lambda: self.chapter() if self.playable() else '-'),
            Readout('elapsed', 'Time', get=lambda: self.time_text() if self.playable() else '-'),
            Readout('tray', 'Tray'),
            Toggle('disc', 'Disc in tray (change while open)'),
            Readout('lastMenu', 'Last menu/cursor command'),
            Readout('ignored', 'Commands ignored (too fast)'),
        ]

    def panel(self, key, value):
        with self.lock:
            s = self.state
            if key == 'disc':
                if s['tray'] != 'Open':
                    self.log('info', 'open the tray to change the disc')
                    return
                self.set('disc', value)
            elif key == 'power_on':
                self._power(True)
            elif key == 'power_off':
                self._power(False)
            elif s['power'] != 'On':
                self.log('info', 'front panel {} ignored while off'.format(key))
            else:
                self.do(key)

    # ----- actions shared by the front panel and the protocol
    def do(self, action):
        s = self.state
        if action == 'open':
            self._move('Stop', 0)
            self.set('tray', 'Open')
        elif action == 'close':
            if s['tray'] == 'Open':
                self.set('tray', 'Closing')
                self.after('tray', self.opts['tray_seconds'], lambda: self.set('tray', 'Closed'))
        elif not self.playable():
            self.log('info', '{}: no disc, nothing happens (still ack)'.format(action))
        elif action == 'play':
            self._move('Play')
        elif action == 'pause':
            self._move('Pause')
        elif action == 'stop':
            self._move('Stop', 0)
        elif action in ('next', 'prev'):
            ch_len = self.opts['chapter_minutes'] * 60
            ch = self.chapter()
            if action == 'next':
                ch = min(ch + 1, self.opts['chapters'])
            elif self.chapter_elapsed() <= 3:
                ch = max(ch - 1, 1)
            self._move('Play' if s['transport'] == 'Stop' else s['transport'], (ch - 1) * ch_len)
        elif action in ('title_next', 'title_prev'):
            t = s['title'] + (1 if action == 'title_next' else -1)
            if 1 <= t <= self.opts['titles']:
                self.set('title', t)
                self._move('Play', 0)
        elif action in SCAN.values():
            self._move(action)

    def _power(self, on):
        s = self.state
        if on and s['power'] != 'On':
            self.set('power', 'On')
            self.lan_changed()
        elif not on and s['power'] == 'On':
            self._move('Stop', 0)
            self.set('power', 'Off')
            self.after('lan', 0.2, self.lan_changed)   # let the ack go out before the port closes

    def lan_open(self):
        return self.state.get('power') == 'On'

    # ----- protocol
    def frames(self, buffer):
        """Commands end with CR; an empty line is a frame too (the real player answers it nack)."""
        frames = []
        while True:
            end = buffer.find(b'\r')
            if end < 0:
                return frames, buffer
            frames.append(buffer[:end])
            buffer = buffer[end + 1:]

    def handle(self, frame, link):
        s = self.state
        # The pacing window is a timer, not a clock reading, so a test harness's virtual clock runs it
        if self.opts['pacing']:
            if self.pending('pace'):
                self.set('ignored', s['ignored'] + 1)
                self.log('info', 'less than {} s after the last command: ignored, no answer'.format(self.opts['pacing']))
                return None
            self.after('pace', self.opts['pacing'], lambda: None)
        if not frame.startswith(b'!7'):
            return b'nack'
        cmd = frame[2:]
        if cmd == b'PWR01':
            self._power(True)
            return b'ack'
        if s['power'] != 'On':
            self.log('info', 'player is off')
            return b'nack'
        return b'ack' if self.command(cmd) else b'nack'

    def command(self, cmd):
        simple = {b'PLY': 'play', b'PAS': 'pause', b'STP': 'stop', b'SKPNX': 'next', b'SKPPV': 'prev',
                  b'GSKNX': 'title_next', b'GSKPV': 'title_prev', b'OPCOP': 'open', b'OPCCL': 'close'}
        if cmd in simple:
            self.do(simple[cmd])
            return True
        if cmd[:3] == b'SCN' and cmd[3:] in SCAN:
            self.do(SCAN[cmd[3:]])
            return True
        if cmd == b'PWR00':
            self._power(False)
            return True
        if cmd in (b'MUT00', b'MUT01'):
            self.set('mute', cmd == b'MUT00')
            return True
        if cmd in (b'OSD00', b'OSD01'):
            self.set('osd', cmd == b'OSD00')
            return True
        if cmd in ACK_ONLY:
            self.set('lastMenu', cmd.decode())
            return True
        self.log('info', 'unknown command')
        return False
