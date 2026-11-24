"""
Atlona AT-OME-PS62, 6x2 matrix presentation switcher, ASCII API (RS-232 115200 8N1; the same
commands on TCP 9000 / Telnet 23 / SSH 22).

Command:  text, case-sensitive, ending CR        e.g. "x3AVx1\\r", "VOUT3 -20\\r", "VOUTMute3 sta\\r"
Reply:    one or more lines ending CR LF         e.g. "x3AVx1", "VOUT3 -20", "VOUTMute3 on"
A command that fails or isn't known answers "Command FAILED". The doc asks for 500 ms between
commands.

Sources: Atlona's AT-OME-PS62 API v5, checked against a ControlScript driver for it
(atna_switcher_AT_OME_PS62_v1_1_0_0, built from that doc and Atlona's Q-SYS plugin; never run on a
real unit). Ten things were left to confirm on first contact with a unit; the options below are
those questions.

Fidelity:
- From the API doc: every command, its arguments and its feedback; "Command FAILED"; x?AVx&,
  x?All, All#, Status, VOUT (-90..10, +, -), VOUTMute, AUD, SetMono, UsbMode, UsbRoute,
  InputStatus, Blink, CommaWait, RepeatCmd, RepCmdTime, RS232para, RS232zone, IPCFG, IPDHCP,
  IPStatic and Mreset (no feedback; the unit reboots), Lock, Unlock, Type, Version.
- Where the doc and the vendor Q-SYS plugin disagree, the default follows the doc and an option
  switches to the plugin's reading:
  * Status: "x3Vx1" per output (doc) or "x3AVx1" (plugin).
  * InputStatus width: 2 digits (the doc's example) or 6 (plugin).
  * "AUD sta" and "UsbMode sta": the doc lists no sta for either (Command FAILED); the plugin
    sends both.
  * UsbMode / UsbRoute sta replies prefixed ("UsbRoute 3", doc) or a bare digit (plugin's fallback).
- The doc prints RepCmdTime's syntax and feedback as "RepeatCmd n". Default: RepCmdTime is
  accepted and answers "RepeatCmd n"; the option makes RepCmdTime fail (RepeatCmd carries the count).
- Best guesses: no echo of commands and no unsolicited route lines from the front panel (options
  for both); commands closer than 500 ms are processed normally (an option refuses or drops them,
  to check a controller's pacing); Mreset is accepted in either casing (the doc spells it both
  ways); the reboot after IPStatic / Mreset takes the "Reboot time"; Lock locks only the front
  panel routing buttons.
"""
import re
import time

from ..core import Device, Choice, Level, Readout, Toggle, NumberOption, FlagOption, ChoiceOption

INPUTS = {1: 'HDBT1', 2: 'HDBT2', 3: 'HDMI3', 4: 'HDMI4', 5: 'HDMI5', 6: 'USB-C'}
OUTPUTS = {1: 'HDMI OUT', 2: 'HDBaseT OUT'}
ZONES = {1: 'HDBaseT OUT', 2: 'HDMI OUT', 3: 'Analog OUT 1', 4: 'Analog OUT 2'}
AUDIO_SOURCES = ['HDBaseT', 'HDMI', 'AUX1', 'AUX2', 'AUX3']
BAUDS = ['2400', '9600', '19200', '38400', '56000', '57600', '115200']
FLAGS = {'Blink': 'blink', 'CommaWait': 'comma_wait', 'IPDHCP': 'dhcp', 'RepeatCmd': 'repeat'}

STATUS_FORMS = ['x3Vx1 (API doc)', 'x3AVx1 (Q-SYS plugin)']
STA_MODES = ['Command FAILED (API doc lists no sta)', 'answers (Q-SYS plugin)']
USB_REPLIES = ['prefixed "UsbRoute 3" (API doc)', 'bare digit (Q-SYS plugin fallback)']
REPCMD_MODES = ['RepCmdTime answers "RepeatCmd n" (API doc feedback)', 'RepCmdTime fails; RepeatCmd n sets the count']
PACING_MODES = ['processed', 'Command FAILED', 'ignored']

FAILED = 'Command FAILED'
ROUTE = re.compile(r'x([1-6])AVx([12])(?:,\s*x([12]))?$')


class AtlonaSwitcher(Device):
    profile_id = 'atlona_switcher'
    profile_name = 'Atlona AT-OME-PS62 switcher'
    kind = 'switcher'
    models = {'AT-OME-PS62': {}}
    log_as = 'ascii'
    default_tcp_port = 9000
    serial_defaults = {'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        ChoiceOption('status_form', 'Status reply form', STATUS_FORMS, STATUS_FORMS[0]),
        NumberOption('input_status_width', 'InputStatus digits (2 = API doc, 6 = plugin)', 2, 2, 6),
        ChoiceOption('sta_aud_usbmode', '"AUD sta" / "UsbMode sta"', STA_MODES, STA_MODES[0]),
        ChoiceOption('usb_reply', 'UsbMode / UsbRoute sta reply', USB_REPLIES, USB_REPLIES[0]),
        ChoiceOption('repcmd', 'RepCmdTime', REPCMD_MODES, REPCMD_MODES[0]),
        FlagOption('echo', 'Echo commands back', False),
        FlagOption('push_routes', 'Front-panel routing sends x?AVx& lines', False),
        ChoiceOption('pacing', 'Commands closer than 500 ms', PACING_MODES, PACING_MODES[0]),
        NumberOption('reboot_seconds', 'Reboot time after IPStatic / Mreset (s)', 20, 0, 120),
    ]
    notes = __doc__

    def reset(self):
        s = self.state
        s.update(out1='HDBT1', out2='HDBT2', aud1='HDMI', aud2='HDMI', mono1=False, mono2=False,
                 usb_mode=0, usb_route=1, locked=False, blink=False, comma_wait=True, dhcp=True,
                 repeat=False, repeat_count=2, rs232para='115200,8,0,1', rebooting=False,
                 ip='192.168.1.254', netmask='255.255.255.0', gateway='192.168.1.1')
        for zone in ZONES:
            s['vol%d' % zone] = -20
            s['mute%d' % zone] = False
        for n in INPUTS:
            s['signal%d' % n] = n in (1, 2)
        self.last_command = 0.0

    def controls(self):
        c = [Readout('rebooting', 'Rebooting'), Readout('locked', 'Front panel locked')]
        for out, name in OUTPUTS.items():
            c.append(Choice('out%d' % out, 'Output %d (%s)' % (out, name), INPUTS.values()))
        for zone in (3, 4):
            c.append(Level('vol%d' % zone, 'Zone %d volume (dB)' % zone, -90, 10))
            c.append(Toggle('mute%d' % zone, 'Zone %d mute' % zone))
        for n, name in INPUTS.items():
            c.append(Toggle('signal%d' % n, 'Signal on %d (%s)' % (n, name)))
        return c

    def panel(self, key, value):
        with self.lock:
            if key.startswith('out'):
                if self.state['locked']:
                    self.log('info', 'front panel is locked; routing change ignored')
                    return
                self.set(key, value)
                if self.opts['push_routes']:
                    number = {v: k for k, v in INPUTS.items()}[value]
                    self.push('x{}AVx{}\r\n'.format(number, key[3:]).encode())
            else:
                self.set(key, value)

    # ----- protocol
    def frames(self, buffer):
        frames = []
        while True:
            end = buffer.find(b'\r')
            if end < 0:
                return frames, buffer
            line, buffer = buffer[:end].strip(b'\n '), buffer[end + 1:]
            if line:
                frames.append(line)

    def lines(self, *text):
        return b''.join(t.encode() + b'\r\n' for t in text)

    def handle(self, frame, link):
        now = time.monotonic()
        too_fast = now - self.last_command < 0.5
        self.last_command = now
        if too_fast and self.opts['pacing'] != 'processed':
            self.log('info', 'command less than 500 ms after the last one: {}'.format(self.opts['pacing']))
            return None if self.opts['pacing'] == 'ignored' else self.lines(FAILED)
        text = frame.decode('latin-1')
        reply = self.execute(text)
        if reply is None:
            return b''.join([frame + b'\r\n'] if self.opts['echo'] else []) or None
        reply = self.lines(*reply) if isinstance(reply, tuple) else self.lines(reply)
        return (frame + b'\r\n' + reply) if self.opts['echo'] else reply

    def execute(self, text):
        s = self.state
        route = ROUTE.match(text)
        if route:
            number, out, second = route.groups()
            outs = [int(out)] + ([int(second)] if second else [])
            for o in outs:
                self.set('out%d' % o, INPUTS[int(number)])
            return ', '.join('x{}AVx{}'.format(number, o) for o in outs)
        match = re.fullmatch(r'x([1-6])All', text)
        if match:
            for o in OUTPUTS:
                self.set('out%d' % o, INPUTS[int(match.group(1))])
            return 'x{0}AVx1, x{0}AVx2'.format(match.group(1))
        if text == 'All#':
            self.set('out1', INPUTS[1])
            self.set('out2', INPUTS[2])
            return 'x1AVx1, x2AVx2'
        if text == 'Status':
            return tuple(self.route_status(o) for o in OUTPUTS)

        match = re.fullmatch(r'VOUT([1-4]) (\+|-|sta|-?\d+)', text)
        if match:
            zone, arg = int(match.group(1)), match.group(2)
            key = 'vol%d' % zone
            if arg == '+':
                self.set(key, min(10, s[key] + 1))
            elif arg == '-':
                self.set(key, max(-90, s[key] - 1))
            elif arg != 'sta':
                level = int(arg)
                if not -90 <= level <= 10:
                    return FAILED
                self.set(key, level)
            return 'VOUT{} {}'.format(zone, s[key])
        match = re.fullmatch(r'VOUTMute([1-4]) (on|off|sta)', text)
        if match:
            zone, arg = int(match.group(1)), match.group(2)
            if arg != 'sta':
                self.set('mute%d' % zone, arg == 'on')
            return 'VOUTMute{} {}'.format(zone, 'on' if s['mute%d' % zone] else 'off')
        match = re.fullmatch(r'AUD([12]) (\S+)', text)
        if match:
            out, arg = match.groups()
            if arg == 'sta':
                if self.opts['sta_aud_usbmode'] == STA_MODES[0]:
                    return FAILED
            elif arg in AUDIO_SOURCES:
                self.set('aud' + out, arg)
            else:
                return FAILED
            return 'AUD{} {}'.format(out, s['aud' + out])
        match = re.fullmatch(r'SetMono([12]) (on|off|sta)', text)
        if match:
            out, arg = match.groups()
            if arg != 'sta':
                self.set('mono' + out, arg == 'on')
            return 'SetMono{} {}'.format(out, 'on' if s['mono' + out] else 'off')
        match = re.fullmatch(r'Usb(Mode|Route) (sta|\d)', text)
        if match:
            which, arg = match.groups()
            key, top = ('usb_mode', 2) if which == 'Mode' else ('usb_route', 6)
            if arg == 'sta':
                if which == 'Mode' and self.opts['sta_aud_usbmode'] == STA_MODES[0]:
                    return FAILED
                if self.opts['usb_reply'] == USB_REPLIES[1]:
                    return str(s[key])
            elif int(arg) <= top and (which == 'Mode' or int(arg) >= 1):
                self.set(key, int(arg))
            else:
                return FAILED
            return 'Usb{} {}'.format(which, s[key])
        if text == 'InputStatus':
            width = self.opts['input_status_width']
            return 'InputStatus ' + ''.join('1' if s['signal%d' % n] else '0' for n in range(1, width + 1))
        match = re.fullmatch(r'InputStatus([1-6])', text)
        if match:
            n = match.group(1)
            return 'InputStatus{} {}'.format(n, 1 if s['signal' + n] else 0)

        match = re.fullmatch(r'(Blink|CommaWait|IPDHCP|RepeatCmd) (on|off|sta)', text)
        if match:
            command, arg = match.groups()
            key = FLAGS[command]
            if arg != 'sta':
                self.set(key, arg == 'on')
            return '{} {}'.format(command, 'on' if s[key] else 'off')
        match = re.fullmatch(r'(RepCmdTime|RepeatCmd) ([2-4]|sta)', text)
        if match:
            command, arg = match.groups()
            if command == 'RepCmdTime' and self.opts['repcmd'] == REPCMD_MODES[1]:
                return FAILED
            if arg != 'sta':
                self.set('repeat_count', int(arg))
            return 'RepeatCmd {}'.format(s['repeat_count'])
        match = re.fullmatch(r'RS232para\[(\d+),([78]),([0-2]),([12])\]', text)
        if match:
            if match.group(1) not in BAUDS:
                return FAILED
            self.set('rs232para', ','.join(match.groups()))
            return 'RS232para[{}]'.format(s['rs232para'])
        if text == 'RS232para sta':
            return 'RS232para[{}]'.format(s['rs232para'])
        match = re.fullmatch(r'RS232zone([1-3])\[(.*)\]', text)
        if match:
            self.log('info', 'RS-232 zone {} sends {!r}'.format(*match.groups()))
            return text
        if text == 'IPCFG':
            return ('IP Addr ' + s['ip'], 'Netmask ' + s['netmask'], 'Gateway ' + s['gateway'],
                    'telnet Port 23', 'ssh Port: 22')
        match = re.fullmatch(r'IPStatic (\S+) (\S+) (\S+)', text)
        if match:
            self.set('ip', match.group(1))
            self.set('netmask', match.group(2))
            self.set('gateway', match.group(3))
            self.set('dhcp', False)
            self.reboot()
            return None
        if text in ('Mreset', 'MReset'):
            self.reset()
            self.reboot()
            return None
        if text in ('Lock', 'Unlock'):
            self.set('locked', text == 'Lock')
            return text
        if text == 'Type':
            return 'AT-OME-PS62'
        if text == 'Version':
            return '1.2.1'
        self.log('info', 'not in the API doc (or wrong case): Command FAILED')
        return FAILED

    def route_status(self, out):
        number = {v: k for k, v in INPUTS.items()}[self.state['out%d' % out]]
        form = 'x{}Vx{}' if self.opts['status_form'] == STATUS_FORMS[0] else 'x{}AVx{}'
        return form.format(number, out)

    def reboot(self):
        """IPStatic / Mreset: no feedback; the unit powers off and back on."""
        seconds = self.opts['reboot_seconds']
        if not seconds:
            return
        self.set('rebooting', True)
        self.booting = True
        self.after('lan', 0.2, self._apply_offline)

        def done():
            self.set('rebooting', False)
            self.booting = False
            self._apply_offline()
        self.after('reboot', seconds, done)
