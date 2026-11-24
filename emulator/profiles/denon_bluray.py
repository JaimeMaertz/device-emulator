"""
Denon Professional DN-500BD MKII Blu-ray player, serial command protocol (RS-232, and LAN port
9030 through the same command set).

Command:  '@0' <command> CR          e.g. "@02353\\r" (Play), "@0?ST\\r" (transport status)
Reply:    ACK, plus the status information code for a status request ("@0STPL\\r"); NACK for an
          unknown command or one that fails. When something changes on the player itself (its
          front panel), it sends the status information code unprompted; the host answers ACK
          (06h), and without it the player resends once after 300 ms, then gives up.

Sources: DN-500BD MKII Serial Command Protocol Guide v1.1 and the Extron
module deno_bluray_DN_500BD_500BDMKII_v1_3_3_0, whose parsing
was written against the real player.

Fidelity:
- From the guide: '@0' framing, the command codes, the status codes (ST, CD, Tr, Tt, tl, ET,
  RM, PCTYP, mt, DVOV, ...), unsolicited status on front-panel changes with the ACK/resend
  handshake, 115200 8N1 for the full command set, "wait one second after Power On".
- From the module, where it contradicts the guide ("ACK/NACK style" and "Power query while off"
  options switch to the guide's reading):
  * ACK/NACK are the text "ack"/"nack" (the guide says bytes 06h/15h): the module waits for
    exactly 3 characters after a command and parses status replies as "ack+@0STPL".
  * The power query answers "nack" while off (the guide says no answer): the module exempts
    Power from its nack error and reads anything but "ack" as Off.
  * Text fields with nothing to show answer "NULL" (ti / at / al).
  * The LAN port closes while the player is off (comm sheet), so TCP connections are refused.
- The guide's Fast Forward code is a typo (it repeats Previous Title); the module's search
  commands (@0PCSLS + F/R + f/s) are used.
- Finding: the module has no receive handler and never ACKs the player's unsolicited status, so
  every push is sent twice. A push that arrives between a poll's query and its reply is read as
  the start of that reply, and the poll fails ("Invalid/unexpected response"). The emulator
  replies within about a millisecond, so this is rare here; the real player may take up to
  300 ms (guide), so expect it from front-panel use on the real player while the room polls.
- Finding, not emulated around: the module reads elapsed/remaining time (@0?ET / @0?RM) as
  digits right after "ack+"; the guide's reply is "@0EThhhmmss", which the module mis-reads.
- Best guesses: the stopped status code (the guide lists none; "@0STST" here, which the module
  reports as 'Other'); which front-panel changes push status (transport, tray/disc, chapter);
  commands while off answer NACK; commands in the first second after Power On get no answer;
  the model string; starting powered on (an option). Only changes started on the player push
  status (front-panel buttons, a tray closed from its own button, reaching the end of a title);
  the results of host commands don't.
"""
import time

from ..core import Device, Button, Choice, Readout, Toggle, NumberOption, ChoiceOption, FlagOption

ACK_STYLES = ['text ack/nack (module)', 'bytes 06h/15h (guide)']
OFF_POWER_REPLY = ['nack (module)', 'no answer (guide)']
# Media type codes (@0?PCTYP, guide p.17) -> what the card shows
MEDIA = {
    'BDM': 'Blu-ray Disc Movie (BDMV)',
    'BDA': 'Blu-ray Disc Audio/Visual (BDAV)',
    'DVV': 'DVD-Video',
    'DVA': 'DVD-Audio',
    'DVR': 'DVD Video Recording (DVD-VR)',
    'CDA': 'Audio CD (CD-DA)',
    'CDR': 'Data CD (CD-ROM)',
    'SAC': 'Super Audio CD (SACD)',
    'AVH': 'Advanced Video Codec High Definition (AVCHD)',
    'DLN': 'Network media (DLNA)',
    'EXT': 'External memory (USB)',
    'UKN': 'Unknown',
}

# Transport state -> (ST status code, playback speed)
TRANSPORT = {
    'Stop': (b'ST', 0), 'Play': (b'PL', 1), 'Pause': (b'PP', 0),
    'Fast Forward': (b'DVFF', 8), 'Fast Reverse': (b'DVFR', -8),
    'Slow Forward': (b'DVSF', 0.5), 'Slow Reverse': (b'DVSR', -0.5),
}
SEARCH = {b'Ff': 'Fast Forward', b'Rf': 'Fast Reverse', b'Fs': 'Slow Forward', b'Rs': 'Slow Reverse'}

# Commands acknowledged without state (menus, cursor, display, settings the emulator doesn't model)
ACK_ONLY = {
    b'PCSU', b'DVTP', b'DVOP', b'DVPU', b'PCRTN', b'PCCUSR1', b'PCCUSR2', b'PCCUSR3', b'PCCUSR4',
    b'PCENTR', b'PCHM', b'DVDSIF', b'DVSBTL1', b'DVADLG+', b'DVADLG-', b'DVANGL+', b'DVMO',
    b'DVFCLR1', b'DVFCLR2', b'DVFCLR3', b'DVFCLR4', b'PCTMDEL', b'PCTMDRM', b'PCTMDTL', b'PCTMDTR',
    b'PCRPAF', b'PCRPBF', b'PCEXRP', b'PCPMP00', b'PCPMP01', b'PCPMR', b'DVHOSD00', b'DVHOSD01',
    b'PCAP00', b'PCAP01', b'PCAR00', b'PCAR01',
} | {b'PCTKEY%d' % d for d in range(10)} | {b'DVVR%d' % d for d in range(1, 7)}


class DenonBluray(Device):
    profile_id = 'denon_bluray'
    profile_name = 'Denon DN-500BD MKII Blu-ray'
    kind = 'bluray'
    models = {'DN-500BD MKII': {'version': b'01.10.00', 'model': b'DN500BD2'}}
    log_as = 'ascii'
    default_tcp_port = 9030
    serial_defaults = {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        ChoiceOption('ack_style', 'ACK/NACK style', ACK_STYLES, ACK_STYLES[0]),
        ChoiceOption('off_power', 'Power query while off', OFF_POWER_REPLY, OFF_POWER_REPLY[0]),
        NumberOption('titles', 'Disc: titles', 3, 1, 99),
        NumberOption('chapters', 'Disc: chapters per title', 12, 1, 99),
        NumberOption('chapter_minutes', 'Disc: minutes per chapter', 5, 1, 120),
        NumberOption('boot', 'No answer after Power On (s)', 1, 0, 60),
        NumberOption('tray_seconds', 'Tray open/close time (s)', 2, 0, 30),
        FlagOption('start_on', 'Start powered on', True),
    ]
    notes = __doc__

    def reset(self):
        self.state.update(power='On' if self.opts['start_on'] else 'Off', booting=False, tray='Closed', disc=True, media='BDM',
                          transport='Stop', title=1, pos=0.0, anchor=time.monotonic(),
                          mute=False, volume=0, fixed=False, panelLock=False, irLock=False)

    # ----- disc position: stored as (pos at anchor, anchor time, speed); computed when read
    def speed(self):
        return TRANSPORT[self.state['transport']][1]

    def title_seconds(self):
        return self.opts['chapters'] * self.opts['chapter_minutes'] * 60

    def position(self):
        s = self.state
        p = s['pos'] + (time.monotonic() - s['anchor']) * self.speed()
        return max(0.0, min(p, self.title_seconds()))

    def _move(self, transport, pos=None):
        """Change transport (and optionally position), re-anchoring the clock."""
        s = self.state
        s['pos'] = self.position() if pos is None else float(pos)
        s['anchor'] = time.monotonic()
        self.set('transport', transport)
        self.cancel('end')
        v = self.speed()
        if v > 0:
            self.after('end', (self.title_seconds() - s['pos']) / v, self._reached_end)
        elif v < 0:
            self.after('end', s['pos'] / -v, lambda: self._changed_on_device(self._move, 'Play', 0))

    def _reached_end(self):
        self._changed_on_device(self._move, 'Stop', 0)

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
            Button('prev', '|<<'), Button('next', '>>|'), Button('rew', '<<'), Button('ff', '>>'),
            Button('open', 'Open'), Button('close', 'Close'),
            Readout('title', 'Title'),
            Readout('chapter', 'Chapter', get=lambda: self.chapter() if self.playable() else '-'),
            Readout('elapsed', 'Time', get=lambda: self.time_text() if self.playable() else '-'),
            Readout('tray', 'Tray'),
            Toggle('disc', 'Disc in tray (change while open)'),
            Choice('media', 'Media type', MEDIA, labels=MEDIA),
            Readout('panelLock', 'Panel lock (@023KL)'),
        ]

    def panel(self, key, value):
        with self.lock:
            s = self.state
            if key in ('disc', 'media'):
                if key == 'disc' and s['tray'] != 'Open':
                    self.log('info', 'open the tray to change the disc')
                    return
                self.set(key, value)
                return
            if s['panelLock'] and key != 'power_on':
                self.log('info', 'front panel locked (@023KL)')
                return
            if key == 'power_on':
                self._power(True)
            elif key == 'power_off':
                self._power(False)
            elif s['power'] != 'On':
                self.log('info', 'front panel {} ignored while off'.format(key))
            else:
                self._changed_on_device(self.do, key, None, True)

    # ----- actions shared by the front panel and the protocol; return False if refused
    def do(self, action, arg=None, from_panel=False):
        s = self.state
        if action == 'open':
            self._tray('Open')
        elif action == 'close':
            self._tray('Closed', from_panel)
        elif not self.playable():
            return False
        elif action == 'play':
            self._move('Play')
        elif action == 'pause':
            self._move('Pause')
        elif action == 'stop':
            self._move('Stop', 0)
        elif action in ('next', 'prev', 'chapter'):
            ch_len = self.opts['chapter_minutes'] * 60
            ch = self.chapter()
            if action == 'next':
                ch = min(ch + 1, self.opts['chapters'])
            elif action == 'prev':
                ch = ch if self.chapter_elapsed() > 3 else max(ch - 1, 1)   # restart, or previous
            else:
                if not 1 <= arg <= self.opts['chapters']:
                    return False
                ch = arg
            self._move('Play' if s['transport'] == 'Stop' else s['transport'], (ch - 1) * ch_len)
        elif action in ('title_next', 'title_prev', 'title'):
            # Previous title restarts the current one unless it's just started (guide), like |<<
            prev = s['title'] if self.position() > 3 else s['title'] - 1
            t = {'title_next': s['title'] + 1, 'title_prev': prev, 'title': arg}[action]
            if not 1 <= t <= self.opts['titles']:
                return False
            self.set('title', t)
            self._move('Play', 0)
        elif action in ('ff', 'rew'):
            self._move('Fast Forward' if action == 'ff' else 'Fast Reverse')
        elif action in SEARCH.values():
            self._move(action)
        return True

    def _tray(self, where, from_panel=False):
        if where == 'Open':
            self._move('Stop', 0)
            self.set('tray', 'Open')
        elif self.state['tray'] == 'Open':
            self.set('tray', 'Closing')
            # Finishing a close started on the player's own button is a change on the player;
            # finishing one the host commanded isn't reported unprompted.
            done = (lambda: self._changed_on_device(self.set, 'tray', 'Closed')) if from_panel \
                else (lambda: self.set('tray', 'Closed'))
            self.after('tray', self.opts['tray_seconds'], done)

    def _power(self, on):
        s = self.state
        if on and s['power'] != 'On':
            self.set('power', 'On')
            if self.opts['boot']:
                self.set('booting', True)
                self.after('boot', self.opts['boot'], lambda: self.set('booting', False))
            self.lan_changed()
        elif not on and s['power'] == 'On':
            self._move('Stop', 0)
            self.set('power', 'Off')
            self.after('lan', 0.2, self.lan_changed)   # let the ack go out before the port closes

    def lan_open(self):
        return self.state.get('power') == 'On'

    # ----- unsolicited status, for changes made on the player itself
    def status_codes(self):
        s = self.state
        cd = b'TO' if s['tray'] == 'Open' else b'TC' if s['tray'] == 'Closing' else b'CI' if s['disc'] else b'NC'
        return {'ST': b'@0ST' + TRANSPORT[s['transport']][0], 'CD': b'@0CD' + cd,
                'Tr': b'@0Tr%04d' % self.chapter()}

    def _changed_on_device(self, fn, *args):
        before = self.status_codes()
        fn(*args)
        after = self.status_codes()
        for key in ('ST', 'CD', 'Tr'):
            if after[key] != before[key]:
                self._announce(after[key] + b'\r')

    def _announce(self, code):
        for link in list(self.links):
            link.state['awaiting_ack'] = code
            self.push(code, links=[link])
            self.after('resend %s' % id(link), 0.3, lambda link=link, code=code: self._resend(link, code))

    def _resend(self, link, code):
        if link.state.get('awaiting_ack') != code:
            return
        self.log('info', 'no ACK from host in 300 ms, resending once', link)
        self.push(code, links=[link])
        self.after('resend %s' % id(link), 0.3, lambda: self._give_up(link, code))

    def _give_up(self, link, code):
        if link.state.get('awaiting_ack') == code:
            link.state.pop('awaiting_ack', None)
            self.log('info', 'no ACK after the resend, giving up', link)

    # ----- protocol
    def frames(self, buffer):
        """Commands end with CR; a bare ACK byte (06h) from the host answers a status push."""
        frames = []
        while buffer:
            if buffer[0] == 0x06:
                frames.append(b'\x06')
                buffer = buffer[1:]
                continue
            end = buffer.find(b'\r')
            if end < 0:
                return frames, buffer
            line, buffer = buffer[:end], buffer[end + 1:]
            if line:
                frames.append(line)
        return frames, buffer

    def ack(self, status=None):
        if self.opts['ack_style'] == ACK_STYLES[0]:
            return b'ack' if status is None else b'ack+' + status + b'\r'
        return b'\x06' if status is None else b'\x06' + status + b'\r'

    def nack(self):
        return b'nack' if self.opts['ack_style'] == ACK_STYLES[0] else b'\x15'

    def handle(self, frame, link):
        s = self.state
        if frame == b'\x06':
            if link.state.pop('awaiting_ack', None):
                self.log('info', 'host ACKed the status', link)
            return None
        if not frame.startswith(b'@0'):
            return self.nack()
        cmd = frame[2:]
        if s['booting']:
            self.log('info', 'still powering on, no answer')
            return None

        if cmd == b'?PW':
            if s['power'] == 'On':
                return self.ack()
            return self.nack() if self.opts['off_power'] == OFF_POWER_REPLY[0] else None
        if cmd == b'PW00':
            self._power(True)
            return self.ack()
        if s['power'] != 'On':
            self.log('info', 'player is off')
            return self.nack()
        if cmd == b'PW01':
            self._power(False)
            return self.ack()
        if cmd.startswith(b'?'):
            return self.query(cmd[1:])
        return self.ack() if self.command(cmd) else self.nack()

    def command(self, cmd):
        s = self.state
        simple = {b'PCDTRYOP': 'open', b'PCDTRYCL': 'close', b'2353': 'play', b'2348': 'pause',
                  b'2354': 'stop', b'2333': 'prev', b'2332': 'next', b'PCGPNX': 'title_next',
                  b'PCGPPV': 'title_prev'}
        if cmd in simple:
            return self.do(simple[cmd])
        if cmd[:2] == b'Tr' and cmd[2:].isdigit():
            return self.do('chapter', int(cmd[2:]))
        if cmd[:4] == b'PCGp' and cmd[4:].isdigit():
            return self.do('title', int(cmd[4:]))
        if cmd[:5] == b'PCSLS' and cmd[5:] in SEARCH:
            return self.do(SEARCH[cmd[5:]])
        if cmd in (b'mt00', b'mt01'):
            self.set('mute', cmd == b'mt00')
            return True
        if cmd in (b'23KL', b'23KU'):
            self.set('panelLock', cmd == b'23KL')
            return True
        if cmd in (b'PCIRLK00', b'PCIRLK01'):
            self.set('irLock', cmd == b'PCIRLK00')
            return True
        if cmd == b'DVOVFX':
            self.set('fixed', True)
            return True
        if cmd[:4] == b'DVOV':
            level = cmd[4:]
            try:
                value = -999 if level == b'inf' else int(level)
            except ValueError:
                return False
            if not (value == -999 or -90 <= value <= 10):
                return False
            self.set('volume', value)
            self.set('fixed', False)
            return True
        if cmd in ACK_ONLY:
            self.log('info', 'acknowledged without state')
            return True
        self.log('info', 'unknown command')
        return False

    def query(self, q):
        s = self.state
        disc = self.playable()
        ch_len = self.opts['chapter_minutes'] * 60
        if q == b'CD':
            return self.ack(self.status_codes()['CD'])
        if q == b'ST':
            return self.ack(self.status_codes()['ST'])
        if q == b'Tr':
            return self.ack(b'@0Tr' + (b'%04d' % self.chapter() if disc else b'UNKN'))
        if q == b'Tt':
            return self.ack(b'@0Tt' + (b'%04d' % self.opts['chapters'] if disc else b'UNKN'))
        if q == b'PCGp':
            return self.ack(b'@0PCGp' + (b'%04d' % s['title'] if disc else b'UNKN'))
        if q == b'PCTG':
            return self.ack(b'@0PCTG' + (b'%04d' % self.opts['titles'] if disc else b'UNKN'))
        if q in (b'tl', b'ET', b'RM'):
            e = int(self.chapter_elapsed()) if disc else 0
            if q == b'tl':
                return self.ack(b'@0tl%03d%02d00' % (e // 60, e % 60))
            t = e if q == b'ET' else max(ch_len - e, 0) if disc else 0
            return self.ack(b'@0%s%03d%02d%02d' % (q, t // 3600, t // 60 % 60, t % 60))
        if q == b'PCTYP':
            return self.ack(b'@0PCTYP' + (s['media'].encode() if s['disc'] else b'UKN'))
        if q == b'mt':
            return self.ack(b'@0mt' + (b'00' if s['mute'] else b'01'))
        if q == b'DVOV':
            v = s['volume']
            level = b'FX' if s['fixed'] else b'inf' if v == -999 else b'000' if v == 0 else b'%+03d' % v
            return self.ack(b'@0DVOV' + level)
        if q == b'PCPMR':
            return self.ack(b'@0PCPMRSOF')
        if q in (b'ti', b'at', b'al'):
            return self.ack(b'@0' + q + b'NULL')
        if q == b'VN':
            return self.ack(b'@0VN' + self.spec['version'] + self.spec['model'])
        self.log('info', 'status request not emulated')
        return self.nack()
