"""
Sony BRAVIA Professional displays (FW-xxBZ30/35/40/50 L and P series and other Pro BRAVIA with an RS-232C jack),
serial control: RS-232C 9600 8N1 on a 3.5 mm stereo mini jack (enabled on the set under Settings > RS232C control >
Via serial port). Served on TCP too, as a serial-over-Ethernet adapter would carry it.

Command (set):   8C 00 <function> <length> <data...> <checksum>   length = bytes from data to checksum
Query (get):     83 00 <function> FF FF <checksum>
Answer (set):    70 <status> <checksum>
Answer (get):    70 00 <size> <data...> <checksum>   size = bytes from data to checksum; an error has no data
The checksum is the low byte of the sum of every byte before it. Status: 00 completed, 01 over the upper limit,
02 under the lower limit, 03 command canceled, 04 parse error (bad packet or checksum). The display only answers.

Sources:
- Sony's serial control pages (pro-bravia.sony.net/remote-display-control/serial-control/ and its /command page): the settings, the frames, the answer codes, Power (function 00; works in standby, "OFF is always
  Enable") and Input Select (function 02; HDMI n = 04 0n; disabled in standby).
- The Extron module sonydisplayFWxxBZ30LxxBZ35LxxBZ40LxxBZ50LSerie (with its comm sheet):
  the query answers' data, which Sony's page does not give (power 70 00 02 0x, input 70 00 03 <type> <number>,
  volume 70 00 03 01 vv), and volume 05 (01 vv), AV mute 06, video mute 0D, audio mute 36, aspect 46 (each 01 xx).

Guessed: a command other than power while the display is in standby is answered 03 (Sony's table only says such
commands are disabled in standby); an unknown function is answered 03; the display turns on and off at once.
"""
from ..core import Button, Choice, Device, Level, Readout, Toggle

SET, GET, ANSWER = 0x8C, 0x83, 0x70
COMPLETED, OVER, UNDER, CANCELED, PARSE_ERROR = 0x00, 0x01, 0x02, 0x03, 0x04
POWER, INPUT, VOLUME, AV_MUTE, VIDEO_MUTE, AUDIO_MUTE, ASPECT = 0x00, 0x02, 0x05, 0x06, 0x0D, 0x36, 0x46

INPUTS = {(0x04, 1): 'HDMI 1', (0x04, 2): 'HDMI 2', (0x04, 3): 'HDMI 3', (0x04, 4): 'HDMI 4',
          (0x02, 1): 'Video / SCART 1', (0x05, 1): 'PC', (0x07, 1): 'Shared Input'}
FLAGS = {AV_MUTE: 'av_mute', VIDEO_MUTE: 'video_mute', AUDIO_MUTE: 'audio_mute'}
ASPECTS = {0x00: '16:9', 0x03: '4:3 Wide Zoom', 0x04: '4:3'}


def checksum(data):
    return sum(data) & 0xFF


class SonyBraviaDisplay(Device):
    profile_id = 'sony_bravia_display'
    profile_name = 'Sony BRAVIA Professional display (RS-232C)'
    kind = 'display'
    models = {'FW-BZ40 / BZ30 / BZ35 / BZ50 (L, P)': {}}
    default_tcp_port = 2001
    serial_defaults = {'baudrate': 9600, 'bytesize': 8, 'parity': 'N', 'stopbits': 1}
    options = []
    notes = __doc__

    def reset(self):
        self.state.update(power='Off', input='HDMI 1', volume=20, av_mute=False, video_mute=False,
                          audio_mute=False, aspect='16:9')

    def controls(self):
        return [Readout('power', 'Power'), Button('power_on', 'Power On (remote)'), Button('power_off', 'Power Off (remote)'),
                Choice('input', 'Input', list(INPUTS.values())), Level('volume', 'Volume', 0, 100),
                Toggle('audio_mute', 'Audio mute'), Readout('av_mute', 'AV mute'), Readout('video_mute', 'Video mute'),
                Readout('aspect', 'Aspect')]

    def panel(self, key, value):
        if key == 'power_on':
            self.set('power', 'On')
        elif key == 'power_off':
            self.set('power', 'Off')
        elif self.state['power'] != 'On':
            self.log('info', 'remote {} ignored while off'.format(key))
        else:
            self.set(key, value)

    # ----- protocol
    def frames(self, buffer):
        """Whole commands. A byte that starts no command is junk; a set's length byte gives its size."""
        frames = []
        while buffer:
            if buffer[0] not in (SET, GET):
                frames.append(None)
                buffer = buffer[1:]
                continue
            if buffer[0] == GET:
                total = 6
            else:
                if len(buffer) < 4:
                    break
                total = 4 + buffer[3]
            if len(buffer) < total:
                break
            frames.append(buffer[:total])
            buffer = buffer[total:]
        return frames, buffer

    @staticmethod
    def answer(status, data=None):
        body = bytes([ANSWER, status]) + (bytes([len(data) + 1]) + bytes(data) if data is not None else b'')
        return body + bytes([checksum(body)])

    def handle(self, frame, link):
        if checksum(frame[:-1]) != frame[-1] or frame[1] != 0x00:
            self.log('info', 'bad packet or checksum: parse error')
            return self.answer(PARSE_ERROR)
        func = frame[2]
        if frame[0] == GET:
            return self.query(func)
        return self.control(func, frame[4:-1])

    def control(self, func, data):
        s = self.state
        if func == POWER and len(data) == 1 and data[0] in (0, 1):
            self.set('power', 'On' if data[0] else 'Off')
            return self.answer(COMPLETED)
        if func not in (POWER, INPUT, VOLUME, AV_MUTE, VIDEO_MUTE, AUDIO_MUTE, ASPECT):
            self.log('info', 'unknown function {:02X}: canceled'.format(func))
            return self.answer(CANCELED)
        if s['power'] != 'On':
            self.log('info', 'function {:02X} while in standby: canceled'.format(func))
            return self.answer(CANCELED)
        if func == INPUT and len(data) == 2 and (data[0], data[1]) in INPUTS:
            self.set('input', INPUTS[(data[0], data[1])])
            return self.answer(COMPLETED)
        if func == VOLUME and len(data) == 2 and data[0] == 0x01:
            if data[1] > 100:
                return self.answer(OVER)
            self.set('volume', data[1])
            return self.answer(COMPLETED)
        if func in FLAGS and len(data) == 2 and data[0] == 0x01 and data[1] in (0, 1):
            self.set(FLAGS[func], bool(data[1]))
            return self.answer(COMPLETED)
        if func == ASPECT and len(data) == 2 and data[0] == 0x01 and data[1] in ASPECTS:
            self.set('aspect', ASPECTS[data[1]])
            return self.answer(COMPLETED)
        return self.answer(PARSE_ERROR)

    def query(self, func):
        s = self.state
        if func == POWER:
            return self.answer(COMPLETED, [1 if s['power'] == 'On' else 0])
        if s['power'] != 'On':
            return self.answer(CANCELED)
        if func == INPUT:
            code = next(k for k, v in INPUTS.items() if v == s['input'])
            return self.answer(COMPLETED, list(code))
        if func == VOLUME:
            return self.answer(COMPLETED, [0x01, s['volume']])
        if func in FLAGS:
            return self.answer(COMPLETED, [0x01, int(s[FLAGS[func]])])
        return self.answer(CANCELED)
