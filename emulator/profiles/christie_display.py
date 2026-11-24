"""
Christie Secure Series II LCD panels, external control ASCII protocol (RS-232 115200 8N1,
LAN TCP 5000).

Command:  K:ALL<command: 3 characters>[value].    set / direct control, ends with "."
          K:ALL<command>?                         status check, ends with "?"
Reply:    ALL:<command>=A     acknowledged         ALL:<command>=N     error
          ALL:<command>=nnn   status (3 digits)
The ID is always ALL. A value is 000-100 unless the command says otherwise. The display only
answers; it never sends status on its own.

Sources: Secure Series II LCD Panels Technical Reference 020-001915-01 Rev. 1, checked against the Extron module
chri_display_SUHDxx3_x_Series_v1_0_3_0 (same command set). Where marked, behavior comes from
the open-source Display-Emulator project, which says it was captured from a
live display; it's unverified here.

Fidelity:
- From the manual: the command format, "=A" / "=N", three-digit status replies (PWR=000 on,
  001 off by power save, 002 off by RS-232 or remote), and the direct, value and status commands
  below. There is no SH6 / AI6 / W_6 (no DVI input), so those answer =N.
- From the Display-Emulator capture: replies end with CR; POF while already off answers
  "ALL:POF=N"; while off, every command except PON, POF and PWR? gets no reply; PON can answer
  with "ALL:PWR=000", "ALL:FWV=A", "ALL:MAC=A" instead of "ALL:PON=A" (option).
- The manual's examples sometimes show a space after "ALL:" ("ALL: VOL=A"), and the Extron module
  accepts one; the "Space after ALL:" option sends it.
- From the Extron module only: K:ALLWN1-4 (select a window) isn't in the manual. It's accepted
  here and read back by WIN?, which the manual does list.
- Not emulated: the HEX protocol (07 <ID> <type> ... 08 CR) the manual also describes.
"""
import re

from ..core import Device, Button, Choice, Level, Readout, Toggle, FlagOption, ChoiceOption

POWER = {0: 'On', 1: 'Off (power save)', 2: 'Off (RS-232 / remote)'}
SOURCES = {0: 'DP1', 1: 'DP2', 2: 'HDMI1', 3: 'HDMI2', 4: 'HDMI3', 5: 'HDMI4', 7: 'OPS-HDMI', 8: 'OPS-DP'}
PICTURE = {0: 'Standard', 1: 'Dynamic', 2: 'User'}
WINDOW_MODES = {0: 'Single', 1: 'Dual1', 2: 'Dual2', 3: 'Dual3', 4: 'Dual4', 5: 'Triple1', 6: 'Triple2',
                7: 'Triple3', 8: 'Triple4', 9: 'Quad'}

# Status checks: command -> initial value. Each is also the state key.
STATUS = {'PWR': 2, 'WIN': 1, 'PMT': 0, 'BLT': 70, 'CON': 50, 'BRT': 50, 'SAT': 50, 'SHA': 50, 'CTT': 3,
          'RGN': 50, 'GGN': 50, 'BGN': 50, 'HDS': 0, 'AUT': 2, 'VOL': 20, 'BCT': 50, 'LAT': 0, 'OTT': 2,
          'PST': 0, 'PWM': 0, 'MMT': 0, 'UAT': 1, 'EDI': 0, 'GMT': 0, 'AIS': 0, 'LLS': 0, 'PRS': 0,
          'WMT': 0, 'W1S': 2, 'W2S': 2, 'W3S': 2, 'W4S': 2, 'SRC': 2, 'MUT': 0}

# Value adjust: command -> highest value
VALUES = {'VOL': 100, 'BLT': 100, 'CON': 100, 'BRT': 100, 'SAT': 100, 'SHA': 100, 'RGN': 100, 'GGN': 100,
          'BGN': 100, 'BCT': 100, 'PRS': 20, 'PWM': 2}


def _direct():
    """Direct control commands -> (state key, value), or (key, +1 / -1) for a one-step change, or
    None for commands that are acknowledged without a state to show."""
    table = {}
    for code in SOURCES:
        table['SH%d' % code] = ('SRC', code)
        table['AI%d' % code] = ('AUT', code)
        for window in '1234':
            table['W%s%d' % (window, code)] = ('W%sS' % window, code)
    for code in PICTURE:
        table['PM%d' % code] = ('PMT', code)
    for code in range(6):
        table['CT%d' % code] = ('CTT', code)
    for code in WINDOW_MODES:
        table['WM%d' % code] = ('WMT', code)
    for code in range(4):
        table['OT%d' % code] = ('OTT', code)
    for code in range(3):
        table['UA%d' % code] = ('UAT', code)
    for code in range(10):
        table['RN%d' % code] = ('PRS', code or 10)
    for window in '1234':
        table['WN' + window] = ('WIN', int(window))
    table.update({
        'BLU': ('BLT', 1), 'BLD': ('BLT', -1), 'CTU': ('CON', 1), 'CTD': ('CON', -1),
        'BRU': ('BRT', 1), 'BRD': ('BRT', -1), 'STU': ('SAT', 1), 'STD': ('SAT', -1),
        'SPU': ('SHA', 1), 'SPD': ('SHA', -1), 'RGU': ('RGN', 1), 'RGD': ('RGN', -1),
        'GGU': ('GGN', 1), 'GGD': ('GGN', -1), 'BGU': ('BGN', 1), 'BGD': ('BGN', -1),
        'VLU': ('VOL', 1), 'VLD': ('VOL', -1), 'BCU': ('BCT', 1), 'BCD': ('BCT', -1),
        'HDF': ('HDS', 0), 'HDO': ('HDS', 1), 'PSF': ('PST', 0), 'PSN': ('PST', 1),
        'EDU': ('EDI', 0), 'EDF': ('EDI', 1), 'GST': ('GMT', 0), 'GDC': ('GMT', 1),
        'AIF': ('AIS', 0), 'AIO': ('AIS', 1), 'LLF': ('LLS', 0), 'LLO': ('LLS', 1),
        'MON': ('MUT', 1), 'MOF': ('MUT', 0), 'PRT': ('PRS', 0),
        'LES': ('LAT', 0), 'LGE': ('LAT', 1), 'LSP': ('LAT', 2), 'LFR': ('LAT', 3), 'LIT': ('LAT', 4),
        'LRU': ('LAT', 5), 'LKR': ('LAT', 6),
        'BLN': ('backlight', True), 'BLF': ('backlight', False),
    })
    for command in ('PIR', 'FTR', 'RSO', 'RUP', 'RDN', 'RRT', 'RLT', 'REN', 'RMN', 'RIF'):
        table[command] = None
    return table


DIRECT = _direct()
STEPS = {'BLU', 'BLD', 'CTU', 'CTD', 'BRU', 'BRD', 'STU', 'STD', 'SPU', 'SPD', 'RGU', 'RGD', 'GGU', 'GGD',
         'BGU', 'BGD', 'VLU', 'VLD', 'BCU', 'BCD'}
COMMAND = re.compile(rb'K:ALL([A-Z0-9]{3})([0-9]*)([.?])')
PON_REPLIES = ['ALL:PON=A (manual)', 'PWR / FWV / MAC (Display-Emulator capture)']


class ChristieDisplay(Device):
    profile_id = 'christie_display'
    profile_name = 'Christie Secure Series II LCD (K:ALL ASCII)'
    kind = 'display'
    models = {'SUHD653-L / 753-L / 863-L / 983-P': {}}
    log_as = 'ascii'
    default_tcp_port = 5000
    serial_defaults = {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        ChoiceOption('pon_reply', 'Power on reply', PON_REPLIES, PON_REPLIES[0]),
        ChoiceOption('off_reply', 'While off, other commands get', ['no reply', '=N'], 'no reply'),
        FlagOption('space', 'Space after "ALL:" in replies', False),
    ]
    notes = __doc__

    def reset(self):
        self.state.update(STATUS)
        self.state['backlight'] = True

    def controls(self):
        return [
            Readout('PWR', 'Power', get=lambda: POWER[self.state['PWR']]),
            Button('power_on', 'Power On (remote)'),
            Button('power_off', 'Power Off (remote)'),
            Button('power_save', 'No signal -> power save'),
            Choice('SRC', 'Input', SOURCES, labels=SOURCES),
            Level('VOL', 'Volume', 0, 100),
            Toggle('MUT', 'Mute'),
            Choice('PMT', 'Picture mode', PICTURE, labels=PICTURE),
            Level('BLT', 'Brightness', 0, 100),
            Toggle('backlight', 'Backlight'),
            Readout('WMT', 'Multi-window', get=lambda: WINDOW_MODES[self.state['WMT']]),
            Readout('PRS', 'Preset'),
        ]

    def panel(self, key, value):
        with self.lock:
            s = self.state
            if key == 'power_on':
                self.set('PWR', 0)
            elif key == 'power_off':
                self.set('PWR', 2)
            elif key == 'power_save':
                if s['PWR'] == 0:
                    self.set('PWR', 1)
            elif s['PWR'] != 0:
                self.log('info', 'remote {} ignored while off'.format(key))
            elif key == 'MUT':
                self.set('MUT', int(value))
            else:
                self.set(key, value)

    # ----- protocol
    def frames(self, buffer):
        """Commands end at "." or "?". Anything before "K:" (line endings, noise) is dropped."""
        frames = []
        while True:
            start = buffer.find(b'K:')
            if start < 0:
                return frames, buffer[-1:] if buffer.endswith(b'K') else b''
            buffer = buffer[start:]
            end = min((i for i in (buffer.find(b'.'), buffer.find(b'?')) if i >= 0), default=-1)
            if end < 0:
                if len(buffer) > 64:     # no terminator in sight: junk
                    frames.append(None)
                    buffer = buffer[2:]
                    continue
                return frames, buffer
            frames.append(buffer[:end + 1])
            buffer = buffer[end + 1:]

    def reply(self, command, value):
        return 'ALL:{}{}={}\r'.format(' ' if self.opts['space'] else '', command, value).encode()

    def handle(self, frame, link):
        match = COMMAND.fullmatch(frame)
        if not match:
            self.log('info', 'not a K:ALL command: no reply')
            return None
        command, value, end = (g.decode() for g in match.groups())
        s = self.state
        on = s['PWR'] == 0

        if end == '?':
            if value or command not in STATUS:
                return self.reply(command, 'N')
            if not on and command != 'PWR':
                return self.while_off(command)
            return self.reply(command, '{:03d}'.format(s[command]))

        if command == 'PON':
            if on:
                return self.reply('PON', 'A')
            self.set('PWR', 0)
            if self.opts['pon_reply'] == PON_REPLIES[1]:
                return self.reply('PWR', '000') + self.reply('FWV', 'A') + self.reply('MAC', 'A')
            return self.reply('PON', 'A')
        if command == 'POF':
            if not on:
                return self.reply('POF', 'N')
            self.set('PWR', 2)
            return self.reply('POF', 'A')
        if not on:
            return self.while_off(command)

        if command in VALUES and value:
            number = int(value)
            if number > VALUES[command]:
                return self.reply(command, 'N')
            self.set(command, number)
            return self.reply(command, 'A')
        if command in DIRECT and not value:
            action = DIRECT[command]
            if action:
                key, change = action
                if command in STEPS:
                    self.set(key, max(0, min(100, s[key] + change)))
                else:
                    self.set(key, change)
            else:
                self.log('info', '{} acknowledged'.format(command))
            return self.reply(command, 'A')
        self.log('info', '{} not in the manual: =N'.format(command))
        return self.reply(command, 'N')

    def while_off(self, command):
        if self.opts['off_reply'] == '=N':
            return self.reply(command, 'N')
        self.log('info', 'display is off: {} gets no reply'.format(command))
        return None
