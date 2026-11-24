"""
Samsung commercial displays, MDC (Multiple Display Control) protocol (RS-232 9600 8N1, LAN TCP 1515).

Command:  AA <cmd> <ID> <length> <data...> <checksum>
Reply:    AA FF <ID> <length> 'A'|'N' <cmd> <data...> <checksum>
The checksum is the low byte of the sum of every byte after AA. An empty data field reads the
current value. A NAK carries one error code. Commands with a sub-command (1C 82, ...) echo it after
<cmd> in an ACK, not in a NAK. ID FE is broadcast: every display acts, none replies. The display
only answers; it never sends status on its own.

Sources: MDC Protocol v15.0, checked against
the Extron module smsg_display_QB_QM_QE_LH_Series_v1_2_1_0 and the samsung-mdc Python client.

Fidelity:
- From the spec: framing, checksum, broadcast without ACK, and the commands below with their data
  values: status 00, serial 0B, display status 0D, software version 0E, model number 10, power
  11 (02 = reboot), volume 12, mute 13, input 14 (the _PC and DVI_VIDEO codes are get-only),
  picture size 15 (refused while the video wall is on), contrast 24, brightness 25, video wall
  mode 5C (only while the wall is on), safety lock 5D, panel lock 5F, volume up/down 62, all keys
  lock 77, video wall on 84, video wall model 89, model name 8A, virtual remote B0, network
  standby B5, panel on/off F9. Locks work whether the display is on or off (spec).
- From the spec's power note: power on over LAN needs Network Standby On (otherwise the LAN port
  is closed while off, and only WOL wakes it), and after a LAN power on the controller "must
  re-connect a socket connection after 10 sec". The samsung-mdc example closes and waits 15 s at
  the same point. Here the connection drops after the ACK and the port refuses for the
  "Reconnect after LAN power on" time.
- From samsung-mdc: "Secured Protocol" firmware greets every LAN connection with
  "MDCSTART<<TLS>>" and wants TLS plus a PIN. The catalog module can't do that, so with the option
  on it never gets a reply, which is how a display with Secured Protocol left on behaves in the
  field. The TLS session itself isn't emulated.
- Best guesses, to confirm on a real unit:
  * While off, status, identity reads, power, locks and network standby answer; everything else
    gets a NAK with error code 01 (or, with the option, no reply). The spec gives no error codes.
  * A frame with a bad checksum, or for another ID, gets no reply.
  * The _PC input codes are reported while "PC signal on HDMI" is set; DVI_VIDEO isn't modeled.
  * Reboot takes the "Reboot time" with the LAN port closed, then comes back On.
  * Model code 00 in the model number reply (the spec's code list stops before these models).
"""
from ..core import Device, Button, Choice, Level, Readout, Toggle, NumberOption, FlagOption, ChoiceOption

HEADER, REPLY, ACK, NAK, BROADCAST = 0xAA, 0xFF, 0x41, 0x4E, 0xFE
TLS_GREETING = b'MDCSTART<<TLS>>'

# Input codes a set accepts, per model (the Extron module's lists, which match the spec's names)
QB_INPUTS = {0x18: 'DVI', 0x20: 'MagicInfo', 0x21: 'HDMI 1', 0x23: 'HDMI 2', 0x60: 'MagicInfo S',
             0x61: 'Screen Mirroring', 0x65: 'Web Browser', 0x66: 'Remote Workspace'}
QM_INPUTS = {**QB_INPUTS, 0x25: 'DisplayPort'}
PC_CODES = {'HDMI 1': 0x22, 'HDMI 2': 0x24}   # reported (get-only) when the HDMI signal is PC timing

ASPECTS = {0x10: '16:9 (PC)', 0x18: '4:3 (PC)', 0x20: 'Original Ratio (PC)', 0x21: '21:9 (PC)',
           0x22: 'Custom (PC)', 0x00: 'Auto Wide', 0x01: '16:9', 0x04: 'Zoom', 0x05: 'Zoom1',
           0x06: 'Zoom2', 0x09: 'Screen Fit', 0x0B: '4:3', 0x0C: 'Wide Fit', 0x0D: 'Custom',
           0x0E: 'Smart View 1', 0x0F: 'Smart View 2', 0x31: 'Wide Zoom', 0x32: '21:9'}
WALL_MODES = {0x00: 'Natural', 0x01: 'Full'}

# Virtual remote (B0) keys that change state; every key in the spec's table is acknowledged
KEY_SOURCE, KEY_POWER, KEY_VOL_UP, KEY_VOL_DOWN, KEY_MUTE, KEY_POWER_OFF = 0x01, 0x02, 0x07, 0x0B, 0x0F, 0x98
KEYS = {0x01, 0x02, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11,
        0x12, 0x14, 0x15, 0x16, 0x1A, 0x1F, 0x23, 0x24, 0x2D, 0x30, 0x45, 0x46, 0x47, 0x48, 0x4A, 0x4B,
        0x58, 0x5B, 0x60, 0x61, 0x62, 0x65, 0x68, 0x6C, 0x77, 0x79, 0x98, 0x9F}

# Commands that answer whatever the power state (the locks per the spec; the rest inferred)
WHILE_OFF = {0x00, 0x0B, 0x0D, 0x0E, 0x10, 0x11, 0x5D, 0x5F, 0x77, 0x8A, 0xB5}
LOCKS = {0x5D: 'safety_lock', 0x5F: 'panel_lock', 0x77: 'all_keys_lock'}
LEVELS = {0x12: 'volume', 0x24: 'contrast', 0x25: 'brightness'}

BROADCAST_MODES = ['act, no reply (spec)', 'act and reply']


def checksum(data):
    return sum(data) & 0xFF


class SamsungDisplay(Device):
    profile_id = 'samsung_display'
    profile_name = 'Samsung display (MDC)'
    kind = 'display'
    models = {
        'QM series (QMN)': {'inputs': QM_INPUTS, 'model_name': 'QM55N'},
        'QB / QE series (QBN, QEN)': {'inputs': QB_INPUTS, 'model_name': 'QB55N'},
    }
    default_tcp_port = 1515
    serial_defaults = {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = [
        NumberOption('display_id', 'Display ID', 0, 0, 224),
        ChoiceOption('broadcast', 'Broadcast (ID FE)', BROADCAST_MODES, BROADCAST_MODES[0]),
        ChoiceOption('off_reply', 'While off, other commands get', ['NAK', 'no reply'], 'NAK'),
        NumberOption('reconnect', 'Reconnect after LAN power on (s, 0 = keep connection)', 10, 0, 120),
        NumberOption('reboot_seconds', 'Reboot time (s)', 10, 0, 300),
        FlagOption('secured', 'Secured Protocol (TLS greeting on LAN)', False),
    ]
    notes = __doc__

    def reset(self):
        self.inputs = self.spec['inputs']
        self.input_codes = {name: code for code, name in self.inputs.items()}
        self.state.update(power='Off', input='HDMI 1', volume=10, mute=False, aspect='16:9',
                          contrast=50, brightness=50, signal=True, pc_signal=False, panel_off=False,
                          network_standby=True, safety_lock=False, panel_lock=False,
                          all_keys_lock=False, video_wall=False, wall_mode='Full', wall_div=0,
                          wall_sno=1, lan_reset=False)

    def controls(self):
        return [
            Readout('power', 'Power'),
            Button('power_on', 'Power On (remote)'),
            Button('power_off', 'Power Off (remote)'),
            Toggle('network_standby', 'Network Standby (menu)'),
            Toggle('signal', 'Signal present'),
            Toggle('pc_signal', 'PC signal on HDMI'),
            Choice('input', 'Input', self.inputs.values()),
            Level('volume', 'Volume', 0, 100),
            Toggle('mute', 'Mute'),
            Choice('aspect', 'Picture size', ASPECTS.values()),
            Level('brightness', 'Brightness', 0, 100),
            Readout('panel_off', 'Panel off'),
            Readout('safety_lock', 'Safety lock'),
            Readout('panel_lock', 'Panel lock'),
            Readout('all_keys_lock', 'All keys lock'),
            Readout('video_wall', 'Video wall'),
            Readout('wall', 'Wall layout', get=self._wall_text),
        ]

    def _wall_text(self):
        div = self.state['wall_div']
        if not div:
            return 'off'
        return '{} x {}, display {}, {}'.format(div >> 4, div & 0x0F, self.state['wall_sno'],
                                                self.state['wall_mode'])

    # ----- power and the LAN port
    def lan_open(self):
        s = self.state
        if s.get('lan_reset'):
            return False
        return s.get('power') == 'On' or s.get('network_standby', True)

    def _power(self, on, link=None):
        s = self.state
        if on == (s['power'] == 'On'):
            return
        self.set('power', 'On' if on else 'Off')
        if on and link is not None and link.kind == 'tcp' and self.opts['reconnect']:
            # Spec: after a LAN power on, re-connect after 10 s. Let the ACK go out first.
            def close_port():
                self.set('lan_reset', True)
                self.lan_changed()
                self.after('lan', self.opts['reconnect'], reopen)

            def reopen():
                self.set('lan_reset', False)
                self.lan_changed()
            self.after('lan', 0.2, close_port)
        else:
            self.after('lan', 0.2, self.lan_changed)

    def _reboot(self):
        self.set('power', 'Rebooting')
        self.set('lan_reset', True)
        self.after('lan', 0.2, self.lan_changed)

        def done():
            self.set('lan_reset', False)
            self.set('power', 'On')
            self.lan_changed()
        self.after('reboot', self.opts['reboot_seconds'], done)

    def panel(self, key, value):
        with self.lock:
            s = self.state
            if key in ('signal', 'pc_signal'):
                self.set(key, value)
                return
            if s['all_keys_lock'] or s['panel_lock'] or s['safety_lock']:
                self.log('info', 'keys are locked; {} ignored'.format(key))
                return
            if key == 'power_on':
                self._power(True)
            elif key == 'power_off':
                self._power(False)
            elif key == 'network_standby':
                self.set(key, value)
                self.lan_changed()
            elif s['power'] != 'On':
                self.log('info', 'remote {} ignored while {}'.format(key, s['power']))
            else:
                self.set(key, value)

    # ----- protocol
    def connected(self, link):
        if link.kind == 'tcp' and self.opts['secured']:
            link.state['secured'] = True
            return TLS_GREETING
        return None

    def frames(self, buffer):
        """Complete frames, including ones with a bad checksum. Bytes before a header are junk."""
        frames = []
        while buffer:
            start = buffer.find(bytes([HEADER]))
            if start != 0:
                frames.append(None)
                if start < 0:
                    return frames, b''
                buffer = buffer[start:]
            if len(buffer) < 4:
                break
            total = 4 + buffer[3] + 1
            if len(buffer) < total:
                break
            frames.append(buffer[:total])
            buffer = buffer[total:]
        return frames, buffer

    def reply(self, display_id, ack, cmd, data=b''):
        body = bytes([REPLY, display_id, 2 + len(data), ACK if ack else NAK, cmd]) + bytes(data)
        return bytes([HEADER]) + body + bytes([checksum(body)])

    def handle(self, frame, link):
        cmd, display_id, data = frame[1], frame[2], frame[4:-1]
        if link.state.get('secured'):
            self.log('info', 'plaintext MDC on a Secured Protocol connection: no reply')
            return None
        if checksum(frame[1:-1]) != frame[-1]:
            self.log('info', 'checksum error: no reply')
            return None
        if display_id not in (self.opts['display_id'], BROADCAST):
            return None
        result = self.execute(cmd, data, link)
        if result is None:
            return None
        if display_id == BROADCAST and self.opts['broadcast'] == BROADCAST_MODES[0]:
            return None
        ack, payload = result
        return self.reply(display_id, ack, cmd, payload)

    def nak(self, why, code=0x01):
        self.log('info', 'NAK ({})'.format(why))
        return False, [code]

    def execute(self, cmd, data, link):
        s = self.state
        power_key = cmd == 0xB0 and data[:1] in (bytes([KEY_POWER]), bytes([KEY_POWER_OFF]))
        if s['power'] != 'On' and cmd not in WHILE_OFF and not power_key:
            if self.opts['off_reply'] == 'no reply':
                self.log('info', 'display is {}: no reply'.format(s['power']))
                return None
            return self.nak('display is ' + s['power'])
        get = not data
        value = data[0] if data else None

        if cmd == 0x00 and get:
            power = 1 if s['power'] == 'On' else 0
            return True, [power, s['volume'], int(s['mute']), self.input_code(),
                          self.aspect_code(), 0, 0]
        if cmd == 0x0B and get:
            return True, b'EMU000000000001' + b'\x00\x00\x00'   # 15 characters + 3 reserved
        if cmd == 0x0D and get:
            no_sync = 0 if s['signal'] or s['power'] != 'On' else 1
            return True, [0, 0, 0, no_sync, 42, 2]   # lamp, temp error, sensor none, sync, 42 C, no fan
        if cmd == 0x0E and get:
            return True, b'EMU-' + self.spec['model_name'].encode() + b'-1000.0'
        if cmd == 0x10 and get:
            return True, [0x02, 0x00, 0x01]   # LCD, model code, no TV tuner
        if cmd == 0x8A and get:
            return True, self.spec['model_name'].encode()

        if cmd == 0x11:
            if get:
                return True, [1 if s['power'] == 'On' else 0]
            if value == 2:
                self._reboot()
            elif value in (0, 1):
                self._power(value == 1, link)
            else:
                return self.nak('power value {:02X}'.format(value))
            return True, [value]
        if cmd in LEVELS:
            key = LEVELS[cmd]
            if get:
                return True, [s[key]]
            if value > 100:
                return self.nak('{} {} out of range'.format(key, value))
            self.set(key, value)
            return True, [value]
        if cmd == 0x13:
            if get:
                return True, [int(s['mute'])]
            if value not in (0, 1):
                return self.nak('mute value {:02X}'.format(value))
            self.set('mute', bool(value))
            return True, [value]
        if cmd == 0x14:
            if get:
                return True, [self.input_code()]
            if value not in self.inputs:
                return self.nak('input {:02X} not settable on this model'.format(value))
            self.set('input', self.inputs[value])
            return True, [value]
        if cmd == 0x15:
            if get:
                return True, [self.aspect_code()]
            if s['video_wall']:
                return self.nak('picture size is locked while the video wall is on')
            if value not in ASPECTS:
                return self.nak('picture size {:02X}'.format(value))
            self.set('aspect', ASPECTS[value])
            return True, [value]
        if cmd in LOCKS:
            key = LOCKS[cmd]
            if get:
                return True, [int(s[key])]
            if value not in (0, 1):
                return self.nak('lock value {:02X}'.format(value))
            self.set(key, bool(value))
            return True, [value]
        if cmd == 0x5C:
            if not s['video_wall']:
                return self.nak('video wall mode needs the video wall on')
            if get:
                return True, [{n: c for c, n in WALL_MODES.items()}[s['wall_mode']]]
            if value not in WALL_MODES:
                return self.nak('wall mode {:02X}'.format(value))
            self.set('wall_mode', WALL_MODES[value])
            return True, [value]
        if cmd == 0x62 and not get:
            if value not in (0, 1):
                return self.nak('volume up/down {:02X}'.format(value))
            self.set('volume', min(100, s['volume'] + 1) if value == 0 else max(0, s['volume'] - 1))
            return True, [value]
        if cmd == 0x84:
            if get:
                return True, [int(s['video_wall'])]
            if value not in (0, 1):
                return self.nak('video wall value {:02X}'.format(value))
            self.set('video_wall', bool(value))
            return True, [value]
        if cmd == 0x89:
            if get:
                return True, [s['wall_div'], s['wall_sno']]
            if len(data) != 2:
                return self.nak('video wall model needs 2 bytes')
            div, sno = data
            rows, cols = div >> 4, div & 0x0F
            if div and not (rows and cols and 1 <= sno <= rows * cols):
                return self.nak('video wall {:02X} / display {}'.format(div, sno))
            self.set('wall_div', div)
            self.set('wall_sno', sno)
            return True, [div, sno]
        if cmd == 0xB0 and not get:
            if value not in KEYS:
                return self.nak('remote key {:02X}'.format(value))
            self.key(value)
            return True, [value]
        if cmd == 0xB5:
            if get:
                return True, [int(s['network_standby'])]
            if value not in (0, 1):
                return self.nak('network standby {:02X}'.format(value))
            self.set('network_standby', bool(value))
            self.lan_changed()
            return True, [value]
        if cmd == 0xF9:
            if get:
                return True, [int(s['panel_off'])]
            if value not in (0, 1):
                return self.nak('panel value {:02X}'.format(value))
            self.set('panel_off', bool(value))
            return True, [value]
        return self.nak('command {:02X} {} not emulated'.format(cmd, 'get' if get else 'set'))

    def input_code(self):
        name = self.state['input']
        if self.state['pc_signal'] and name in PC_CODES:
            return PC_CODES[name]
        return self.input_codes[name]

    def aspect_code(self):
        return {n: c for c, n in ASPECTS.items()}[self.state['aspect']]

    def key(self, code):
        s = self.state
        if code == KEY_POWER:
            self._power(s['power'] != 'On')
        elif code == KEY_POWER_OFF:
            self._power(False)
        elif code == KEY_VOL_UP:
            self.set('volume', min(100, s['volume'] + 1))
        elif code == KEY_VOL_DOWN:
            self.set('volume', max(0, s['volume'] - 1))
        elif code == KEY_MUTE:
            self.set('mute', not s['mute'])
        elif code == KEY_SOURCE:
            names = list(self.inputs.values())
            self.set('input', names[(names.index(s['input']) + 1) % len(names)])
        else:
            self.log('info', 'remote key {:02X} acknowledged'.format(code))
