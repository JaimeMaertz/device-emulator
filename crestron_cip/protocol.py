"""
CIP (Crestron over IP) on the wire: packet framing, every message type known, and encoders and decoders for them.

A packet is a type byte, a 2-byte big-endian length, then that many payload bytes. Sources for each layout, strongest
first: frames recorded from a real RMC4 and CCC-110-T, then three open implementations: python-cipclient,
rdriscoll's Crestron-CIP server and CommandFusion's iViewer module. Where a layout comes from only one source, its docstring
says so.

Join numbers are 1-based here, as SIMPL shows them; the wire carries join - 1.
"""

# ----- packet types
REGISTER = 0x01             # panel -> processor: sign on at an IP ID
REGISTER_RESULT = 0x02      # processor -> panel: 00 00 00 1f accepted; ff ff 02 no such IP ID
DISCONNECT = 0x03           # processor -> panel: the processor closed the session
REFUSED = 0x04              # processor -> panel: connection refused (rdriscoll; not seen on a real processor)
DATA = 0x05                 # joins, update requests, date/time, device details
SIGN_ON = 0x0A              # an older sign-on, IP ID at payload[1] (rdriscoll; not seen on a real processor)
HEARTBEAT = 0x0D            # either side; the other answers HEARTBEAT_RESPONSE
HEARTBEAT_RESPONSE = 0x0E
REGISTRATION_REQUEST = 0x0F # processor -> panel, first thing on connect: 0f 00 01 02
SERIAL = 0x12               # serial joins (and smart-object serials)
DEVICE_SIGN_ON = 0x26       # panel -> processor: the sign-on a CCC-110-T sends (IP ID, MAC, maker, model, name)
DEVICE_SIGN_ON_RESULT = 0x27  # processor -> panel: accepted, with the processor's name ("RMC4")

TYPE_NAMES = {REGISTER: 'register', REGISTER_RESULT: 'register-result', DISCONNECT: 'disconnect', REFUSED: 'refused',
              DATA: 'data', SIGN_ON: 'sign-on', HEARTBEAT: 'heartbeat', HEARTBEAT_RESPONSE: 'heartbeat-response',
              REGISTRATION_REQUEST: 'registration-request', SERIAL: 'serial', DEVICE_SIGN_ON: 'device-sign-on',
              DEVICE_SIGN_ON_RESULT: 'device-sign-on-result'}

# ----- DATA payload: 00 00 <len> <datatype> <body>
DIGITAL = 0x00          # packed pairs: low byte, then high 7 bits with 0x80 = low
ANALOG_SINGLE = 0x01    # join BE, value BE (rdriscoll)
SERIAL_TYPE1 = 0x02     # '#join,text\r' style (rdriscoll; layout uncertain)
CONTROL = 0x03          # update requests and device details: CONTROL_* below
DATE_TIME = 0x08        # processor -> panel: hh mm ss MM DD YY as BCD
ANALOG = 0x14           # packed quads: join BE, value BE
SERIAL_TYPE3 = 0x15     # join BE, 03, text (rdriscoll)
DIGITAL_BUTTON = 0x27   # a press / release: same pairs as DIGITAL
SMART_OBJECT = 0x38     # 00 00 00 <id> <len> <datatype> <body> (rdriscoll)
SLOT = 0x20             # a device's slot: <slot> <len> <datatype> <body>; slot = the device's FixedN in iodev.tio (measured on DM NVX)
SLOT_SERIAL = 0x12      # inside SLOT: <join - 1, one byte> <text>; with 0x80 set in that byte, a COM port's settings
SLOT_SERIAL_MARK = 0x35 # a SERIAL packet from the processor to a slot: 35 <slot> <len BE> 34 <join BE> 03 <text>

CONTROL_UPDATE_REQUEST = 0x00     # either side asks for every join's state
CONTROL_UPDATE_PENULTIMATE = 0x16 # processor, near the end of an update
CONTROL_END_OF_QUERY = 0x1C       # processor: the update is complete
CONTROL_END_OF_QUERY_ACK = 0x1D   # panel: acknowledges it
CONTROL_STATUS_TEXT = 0x30        # panel -> processor: run-time status text ("Startup parameter IPID=3", rdriscoll)
CONTROL_DEVICE_DETAIL = 0x31      # panel -> processor: an ENV item ("Core3Version", "WidthInPixels", rdriscoll)

REGISTERED_OK = b'\x00\x00\x00\x1f'
NO_SUCH_IPID = b'\xff\xff\x02'

# The sign-on body python-cipclient sends, with the IP ID at byte 5; registers on a 4-series as a generic XPanel.
XPANEL_REGISTER_BODY = bytes.fromhex('0000000000') + b'{ipid}' + bytes.fromhex('40fffff101')


def packet(ptype, payload=b''):
    return bytes([ptype]) + len(payload).to_bytes(2, 'big') + payload


class Splitter:
    """Collects bytes and yields complete packets as (type, payload, raw)."""

    def __init__(self):
        self.buffer = b''

    def feed(self, data):
        self.buffer += data
        out = []
        while len(self.buffer) >= 3:
            n = int.from_bytes(self.buffer[1:3], 'big')
            if len(self.buffer) < 3 + n:
                break
            raw, self.buffer = self.buffer[:3 + n], self.buffer[3 + n:]
            out.append((raw[0], raw[3:], raw))
        return out


def split(data):
    """Complete packets in `data` and the incomplete rest: (list of raw packets, remainder)."""
    s = Splitter()
    return [raw for _, _, raw in s.feed(data)], s.buffer


# ----- encoders
def data(datatype, body):
    inner = bytes([datatype]) + body
    return packet(DATA, b'\x00\x00' + bytes([len(inner)]) + inner)


def digital_pair(join, value):
    if not 1 <= join <= 32768:
        raise ValueError('digital join {} out of 1..32768'.format(join))
    j = join - 1
    return bytes([j & 0xFF, (j >> 8) & 0x7F | (0 if value else 0x80)])


def digital(join, value, button=False):
    """One digital join. button=True is the press/release form a panel sends for a button (0x27)."""
    return data(DIGITAL_BUTTON if button else DIGITAL, digital_pair(join, value))


def digitals(items, button=False):
    """Several digital joins in one packet, as the processor sends an update: items = [(join, value), ...]."""
    return data(DIGITAL_BUTTON if button else DIGITAL, b''.join(digital_pair(j, v) for j, v in items))


def analog(join, value):
    if not 1 <= join <= 65536 or not 0 <= value <= 65535:
        raise ValueError('analog join {} = {} out of range'.format(join, value))
    return data(ANALOG, (join - 1).to_bytes(2, 'big') + value.to_bytes(2, 'big'))


def analogs(items):
    return data(ANALOG, b''.join((j - 1).to_bytes(2, 'big') + v.to_bytes(2, 'big') for j, v in items))


def serial(join, text, encoding='latin-1'):
    """A serial join, flags 03 (the whole string, one packet), as the processor sends it and python-cipclient does."""
    body = bytes([0x34]) + (join - 1).to_bytes(2, 'big') + b'\x03' + text.encode(encoding)
    return packet(SERIAL, b'\x00\x00\x00' + bytes([len(body)]) + body)


def control(sub, body=b''):
    return data(CONTROL, bytes([sub]) + body)


def update_request():
    return control(CONTROL_UPDATE_REQUEST)


def end_of_query():
    return control(CONTROL_END_OF_QUERY)


def end_of_query_ack():
    return control(CONTROL_END_OF_QUERY_ACK)


# ----- a multi-slot device's slots (DM NVX; measured against an RMC4)
def slot_data(slot, datatype, body):
    """Joins of one slot, from the device: data 20 <slot> <len> <datatype> <body>."""
    inner = bytes([datatype]) + body
    return data(SLOT, bytes([slot, len(inner)]) + inner)


def slot_digital(slot, join, value):
    return slot_data(slot, DIGITAL, digital_pair(join, value))


def slot_serial(slot, join, data_bytes):
    """A slot's serial join from the device (bytes or text; a COM port's received data is its serial join 1)."""
    if not 1 <= join <= 128:
        raise ValueError('slot serial join {} out of 1..128'.format(join))
    if isinstance(data_bytes, str):
        data_bytes = data_bytes.encode('latin-1')
    return slot_data(slot, SLOT_SERIAL, bytes([join - 1]) + data_bytes)


def slot_serial_from_processor(slot, join, text):
    """The form the processor sends a serial to a slot in (for a fake processor and tests)."""
    body = bytes([0x34]) + (join - 1).to_bytes(2, 'big') + b'\x03' + text.encode('latin-1')
    inner = bytes([SLOT_SERIAL_MARK, slot]) + len(body).to_bytes(2, 'big') + body
    return packet(SERIAL, b'\x00\x00\x00' + bytes([len(inner)]) + inner)


def heartbeat():
    return packet(HEARTBEAT, b'\x00\x00')


def heartbeat_response():
    return packet(HEARTBEAT_RESPONSE, b'\x00\x00')


def registration_request():
    return packet(REGISTRATION_REQUEST, b'\x02')


def register(ipid, body=XPANEL_REGISTER_BODY):
    """The sign-on. `body` is the payload with b'{ipid}' where the IP ID byte goes; the default registers as an XPanel."""
    if not 0x03 <= ipid <= 0xFE:
        raise ValueError('IP ID 0x{:02X} out of 03..FE'.format(ipid))
    return packet(REGISTER, body.replace(b'{ipid}', bytes([ipid])))


def device_sign_on(ipid, mac, maker='Crestron', model='UC-ENGINE', name=''):
    """The sign-on a real CCC-110-T sends (captured from a real unit): 00, IP ID,
    40 01 01 00 00 00 01, the MAC, then three zero-padded fields of 50, 50 and 32 bytes. The CCC's were "Crestron",
    "UC-ENGINE" and "TEST". At a CCC-110-T slot the processor takes this panel's presses."""
    if not 0x03 <= ipid <= 0xFE:
        raise ValueError('IP ID 0x{:02X} out of 03..FE'.format(ipid))
    mac = bytes.fromhex(mac.replace(':', '').replace('-', '')) if isinstance(mac, str) else bytes(mac)
    if len(mac) != 6:
        raise ValueError('a MAC is 6 bytes')
    field = lambda text, n: text.encode('latin-1')[:n].ljust(n, b'\0')
    return packet(DEVICE_SIGN_ON, bytes([0, ipid]) + bytes.fromhex('40010100000001') + mac + field(maker, 50)
                  + field(model, 50) + field(name, 32))


def device_sign_on_result(ipid, processor='RMC4'):
    """What the RMC4 answered a CCC's sign-on: the IP ID in 4 bytes, 00 00 00 08, then its name in 30 bytes."""
    return packet(DEVICE_SIGN_ON_RESULT, bytes([0, 0, 0, ipid]) + bytes.fromhex('00000008')
                  + processor.encode('latin-1')[:30].ljust(30, b'\0'))


def register_result(ok=True):
    return packet(REGISTER_RESULT, REGISTERED_OK if ok else NO_SUCH_IPID)


# ----- decoder
def decode(raw):
    """One raw packet as a dict: always 'type' (name) and 'hex'; the parts it understands as named fields:
    joins [(kind, join, value)], 'control' (the update-request step), 'text', 'ipid', 'result'."""
    ptype, payload = raw[0], raw[3:]
    d = {'type': TYPE_NAMES.get(ptype, '0x{:02x}'.format(ptype)), 'hex': raw.hex(' ')}
    if ptype == REGISTER and len(payload) > 5:
        d['ipid'] = payload[5]
    elif ptype == SIGN_ON and len(payload) > 1:
        d['ipid'] = payload[1]
    elif ptype == DEVICE_SIGN_ON and len(payload) >= 147:
        d['ipid'] = payload[1]
        d['mac'] = ':'.join('{:02x}'.format(c) for c in payload[9:15])
        d['text'] = ' / '.join(_field(payload[a:b]) for a, b in ((15, 65), (65, 115), (115, 147)))
    elif ptype == DEVICE_SIGN_ON_RESULT and len(payload) >= 8:
        d['ipid'], d['result'], d['text'] = payload[3], 'ok', _field(payload[8:])
    elif ptype == REGISTER_RESULT:
        d['result'] = 'ok' if payload == REGISTERED_OK else 'no such IP ID' if payload == NO_SUCH_IPID else 'error'
    elif ptype == DATA and len(payload) >= 4:
        datatype, body = payload[3], payload[4:]
        d['datatype'] = '0x{:02x}'.format(datatype)
        if datatype in (DIGITAL, DIGITAL_BUTTON):
            kind = 'digital-button' if datatype == DIGITAL_BUTTON else 'digital'
            d['joins'] = [(kind, (((body[i + 1] & 0x7F) << 8) | body[i]) + 1, 0 if body[i + 1] & 0x80 else 1)
                          for i in range(0, len(body) - 1, 2)]
        elif datatype in (ANALOG, ANALOG_SINGLE):
            d['joins'] = [('analog', int.from_bytes(body[i:i + 2], 'big') + 1, int.from_bytes(body[i + 2:i + 4], 'big'))
                          for i in range(0, len(body) - 3, 4)]
        elif datatype == CONTROL and body:
            names = {CONTROL_UPDATE_REQUEST: 'update-request', CONTROL_UPDATE_PENULTIMATE: 'update-penultimate',
                     CONTROL_END_OF_QUERY: 'end-of-query', CONTROL_END_OF_QUERY_ACK: 'end-of-query-ack',
                     CONTROL_STATUS_TEXT: 'status-text', CONTROL_DEVICE_DETAIL: 'device-detail'}
            d['control'] = names.get(body[0], '0x{:02x}'.format(body[0]))
            if body[0] in (CONTROL_STATUS_TEXT, CONTROL_DEVICE_DETAIL):
                d['text'] = printable(body[1:])
        elif datatype == DATE_TIME and len(body) >= 7:
            h = body[1:].hex()
            d['text'] = '{}:{}:{} {}/{}/20{}'.format(h[0:2], h[2:4], h[4:6], h[6:8], h[8:10], h[10:12])
        elif datatype == SERIAL_TYPE3 and len(body) >= 3:
            text = body[3:]
            if text.endswith(b'\xff\xff'):   # a CCC ends each of its serials this way
                text = text[:-2]
            d['joins'] = [('serial', int.from_bytes(body[0:2], 'big') + 1, text.decode('latin-1'))]
        elif datatype == SMART_OBJECT and len(body) >= 6:
            d['smart_object'] = body[3]
            inner = decode(packet(DATA, b'\x00\x00' + body[4:]))
            d['joins'] = inner.get('joins', [])
        elif datatype == SLOT and len(body) >= 3:
            # a slot's joins go in 'slot_joins', never 'joins': they are not the device's own joins
            d['slot'], inner_type, inner = body[0], body[2], body[3:2 + body[1]]
            if inner_type == SLOT_SERIAL and inner:
                if inner[0] & 0x80:
                    d['slot_config'] = inner[1:].hex(' ')
                    d['slot_joins'] = []
                else:
                    d['slot_joins'] = [('serial', inner[0] + 1, inner[1:].decode('latin-1'))]
            else:
                d['slot_joins'] = decode(packet(DATA, b'\x00\x00' + bytes([len(inner) + 1, inner_type]) + inner)).get('joins', [])
    elif ptype == SERIAL and len(payload) >= 12 and payload[4] == SLOT_SERIAL_MARK:
        d['slot'] = payload[5]
        flags, text = payload[11], payload[12:]
        value = text.decode('utf-16-le', 'replace') if flags & 0x04 else text.decode('latin-1')
        d['slot_joins'] = [('serial', int.from_bytes(payload[9:11], 'big') + 1, value)]
    elif ptype == SERIAL and len(payload) >= 8:
        kind = 'smart-object-serial' if payload[4] == 0x39 else 'serial'
        flags, text = payload[7], payload[8:]
        value = text.decode('utf-16-le', 'replace') if flags & 0x04 else text.decode('latin-1')
        d['joins'] = [(kind, int.from_bytes(payload[5:7], 'big') + 1, value)]
        d['flags'] = flags
    return d


def _field(b):
    return b.split(b'\0', 1)[0].decode('latin-1')


def printable(b):
    return ''.join(chr(c) if 32 <= c < 127 else '\\x{:02x}'.format(c) for c in b)


def describe(raw):
    """One line for a log."""
    d = decode(raw)
    parts = [d['type']]
    for k in ('ipid', 'mac', 'result', 'control', 'smart_object'):
        if k in d:
            parts.append('{}={}'.format(k, '0x{:02X}'.format(d[k]) if k == 'ipid' else d[k]))
    if 'joins' in d:
        parts.append(' '.join('{}{}={}'.format(kind[0], j, repr(v) if isinstance(v, str) else v) for kind, j, v in d['joins']))
    if 'text' in d:
        parts.append(repr(d['text']))
    if len(parts) == 1 or d['type'].startswith('0x') or d.get('datatype') not in (None, '0x00', '0x27', '0x14', '0x03', '0x08', '0x15'):
        parts.append('[' + d['hex'] + ']')
    return ' '.join(parts)
