"""
NEC display profile against nec_display_E758_E868_E988_v1_0_2_0_Modified, Extron's catalog module with
Mute and volume updates added (the modification isn't distributed: it's Extron's code, so without it this
test is skipped), connected over a real localhost socket, plus raw frames checked against
the E758/E868 external control manual. The module uses SendAndWait, so every Update returns its
reply synchronously.   Run:  python tests/test_nec_display.py
"""
import time
from harness import check, finish, load_module, start_device, wait_for, RawClient
from emulator.profiles.nec_display import bcc

NEC = load_module('nec_display_E758_E868_E988_v1_0_2_0_Modified.py')
FAST = {'busy_power': 0, 'busy_input': 0}


def connect(port, monitor_id=None):
    m = NEC.EthernetClass('127.0.0.1', port)
    m.errors, m.discards = [], []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Discard = lambda message, m=m: m.discards.append(message)
    if monitor_id:
        m.DeviceID = str(monitor_id)
    m.Connect()
    return m


def update(m, command):
    m.Update(command)
    return m.ReadStatus(command)


def frame(msg_type, body, dest=b'A'):
    """A controller -> display frame, as the manual builds it."""
    data = b'\x010' + dest + b'0' + msg_type + '{:02X}'.format(len(body) + 2).encode() + b'\x02' + body + b'\x03'
    return data + bytes([bcc(data[1:])]) + b'\r'


def body(reply):
    """The message between STX and ETX of a reply frame."""
    return reply[8:-3]



dev, port = start_device('nec_display', options=FAST)
try:
    m = connect(port)
    check('1 power read while off -> Off, Connected', update(m, 'Power') == 'Off'
          and m.ReadStatus('ConnectionStatus') == 'Connected')
    m.Set('Input', 'HDMI 2')
    check('2 module refuses non-power Sets while Off (its SafeToSet), nothing sent',
          m.discards and dev.state['input'] == 'HDMI 1')
    update(m, 'Input')
    check('3 a parameter read while Off gets result 01, which the module reports as an error',
          any('Input' in e for e in m.errors))
    m.errors.clear()
    m.Set('Power', 'On')
    check('4 Set Power On -> On, module reads On, no error',
          dev.state['power'] == 'On' and update(m, 'Power') == 'On' and not m.errors)

    names = ['HDMI 1', 'HDMI 2', 'HDMI 3', 'VGA (RGB)', 'VGA (YPbPr)', 'AV', 'Media Player']
    readback = {}
    for name in names:
        m.Set('Input', name)
        readback[name] = update(m, 'Input')
    check('5 every input the module sets reads back', not m.errors and all(readback[n] == n for n in names))

    for aspect in ('Normal', 'Zoom', '1:1', 'Full'):
        m.Set('AspectRatio', aspect)
        readback[aspect] = update(m, 'AspectRatio')
    check('6 every aspect reads back', all(readback[a] == a for a in ('Normal', 'Zoom', '1:1', 'Full')))

    m.Set('Volume', 64)   # non-blocking in the Modified module: nothing waits for the reply
    wait_for(lambda: dev.state['volume'] == 64, 1)
    m.Set('Mute', 'On')
    check('7 volume + mute (option "supported") set by the module land',
          (dev.state['volume'], dev.state['mute']) == (64, True))
    time.sleep(0.4)   # let the unread volume reply arrive before the next SendAndWait
    check('8 ...and read back (volume 0x40 = 64, mute 1 = On)',
          (update(m, 'Volume'), update(m, 'Mute')) == (64, 'On') and not m.errors)
    m.Set('MenuNavigation', 'Menu')
    check('9 menu navigation (C210) acknowledged, no module error', not m.errors)

    dev.panel('input', 'AV')
    dev.panel('volume', 5)
    dev.panel('mute', False)
    check('10 remote changes read back by the module',
          (update(m, 'Input'), update(m, 'Volume'), update(m, 'Mute')) == ('AV', 5, 'Off'))
    dev.panel('power_save', True)
    check('11 no signal -> module reads Standby (Power Save)', update(m, 'Power') == 'Standby (Power Save)')
    dev.panel('power_on', True)

    raw = RawClient(port)
    reply = raw.send(frame(b'A', b'01D6'))
    check('12 power read reply: from monitor A, "0200D6000004 0001", valid BCC, CR',
          reply[:5] == b'\x0100AB' and body(reply) == b'0200D60000040001'
          and reply[-2] == bcc(reply[1:-2]) and reply[-1:] == b'\r')
    check('13 manual example: get backlight (00-10) -> "00 00 10 00 0064 <value>"',
          body(raw.send(frame(b'C', b'0010'))) == b'00001000' + b'0064' + b'0046')
    check('14 manual example: set backlight 80 -> "F" reply echoing 0050, state 80',
          body(raw.send(frame(b'E', b'00100050'))) == b'000010000064' + b'0050' and dev.state['backlight'] == 80)
    check('15 remote key reply (C210, VOL+) is "C310" "00" key, and volume steps up',
          body(raw.send(frame(b'A', b'C210001701'))) == b'C3100017' and dev.state['volume'] == 6)
    check('16 save settings -> "000C"', body(raw.send(frame(b'A', b'0C'))) == b'000C')
    check('17 timing report with signal: "4E" SS 03, 67.50 kHz, 60.00 Hz',
          body(raw.send(frame(b'A', b'07'))) == b'4E031A5E1770')
    dev.panel('signal', False)
    check('18 ...and with no signal: SS bit 7 set', body(raw.send(frame(b'A', b'07'))) == b'4E8000000000')
    check('19 model name read: "C317" + hex-encoded "E758"',
          body(raw.send(frame(b'A', b'C217'))) == b'C317' + b'45373538')
    check('20 firmware read: "CB02" result 00, type 00, "R1.000AA"',
          body(raw.send(frame(b'A', b'CA0200'))) == b'CB020000R1.000AA')
    check('21 unknown command -> NULL message "BE"', body(raw.send(frame(b'A', b'ZZ99'))) == b'BE')
    bad = frame(b'A', b'01D6')
    check('22 bad BCC -> NULL message', body(raw.send(bad[:-2] + b'\x00\r')) == b'BE')
    check('23 a frame for monitor B is ignored', raw.send(frame(b'A', b'01D6', dest=b'B')) == b'')
    good = frame(b'A', b'01D6')
    check('23a a truncated command is dropped and the good one right after it is answered',
          body(raw.send(good[:10] + good)) == b'0200D60000040001')
    dev.partial_timeout = 0.5   # the manual's 10 s, shortened for the test
    silent = raw.send(b'\x01' + good) == b''   # stray SOH: the length is misread, so it waits
    time.sleep(0.4)
    check('23b a stray SOH stalls parsing until the timeout, which answers NULL and clears it',
          silent and body(raw.read()) == b'BE' and body(raw.send(good)) == b'0200D60000040001')
    dev.partial_timeout = 10
    check('24 broadcast (*) is acted on without a reply',
          raw.send(frame(b'E', b'00100020', dest=b'*')) == b'' and dev.state['backlight'] == 0x20)

    dev.set_option('volume_mode', 'result 01')
    check('25 volume option "result 01": get 00-62 answers result 01',
          body(raw.send(frame(b'C', b'0062')))[:2] == b'01')
    dev.set_option('volume_mode', 'no reply')
    check('26 volume option "no reply": set 00-62 gets nothing', raw.send(frame(b'E', b'00620010')) == b'')
    dev.set_option('volume_mode', 'supported')

    m.Set('Power', 'Off')
    check('27 Set Power Off -> Off', update(m, 'Power') == 'Off')
    dev.set_option('idle_timeout', 1)
    check('28 LAN idle timeout closes a silent client', raw.closed_by_peer(3))
finally:
    dev.close()

# Busy windows: NULL to everything while power or input is executing
dev, port = start_device('nec_display', options={'busy_power': 1, 'busy_input': 0.5})
try:
    m = connect(port)
    m.Set('Power', 'On')
    raw = RawClient(port)
    check('29 right after Power On, a power read gets NULL', body(raw.send(frame(b'A', b'01D6'))) == b'BE')
    m.errors.clear()
    update(m, 'Power')
    check('30 ...which the module reports as an invalid response', any('Power' in e for e in m.errors))
    time.sleep(1.1)
    check('31 after the busy window, it answers again', update(m, 'Power') == 'On')
    m.Set('Input', 'HDMI 2')
    check('32 an input change starts its own busy window', body(raw.send(frame(b'C', b'0060'))) == b'BE')
    time.sleep(0.6)
    check('33 ...then reads HDMI 2', update(m, 'Input') == 'HDMI 2')
finally:
    dev.close()

dev, port = start_device('nec_display', model='E868', options=dict(FAST, monitor_id=5))
try:
    m = connect(port, 5)
    check('34 monitor ID 5 answers the module set to DeviceID 5', update(m, 'Power') == 'Off')
    raw = RawClient(port)
    check('35 model E868 reports its own model name',
          body(raw.send(frame(b'A', b'C217', dest=b'E'))) == b'C317' + b'45383638')
finally:
    dev.close()

# --- U. Model UN552S against the UN-series catalog module (a videowall's displays)
UN = load_module('nec_display_UN_UX_Series_v1_0_3_1.py')
dev, port = start_device('nec_display', model='UN552S', options=FAST)
try:
    m = UN.EthernetClass('127.0.0.1', port, Model='UN552S')
    m.errors = []
    m.Error = lambda message: m.errors.append(message[0])
    m.Discard = lambda message: m.errors.append(message)
    m.Connect()
    q = {'Device ID': '1'}
    m.Update('Power', q)
    check('U1 UN module: power read while off -> Off, Connected',
          m.ReadStatus('Power', q) == 'Off' and m.ReadStatus('ConnectionStatus') == 'Connected')
    m.Set('Power', 'On', q)
    m.Update('Power', q)
    check('U2 Set Power On -> On, read back', dev.state['power'] == 'On' and m.ReadStatus('Power', q) == 'On')
    names = ['HDMI 1', 'HDMI 2', 'DisplayPort 1', 'DVI', 'MP']
    readback = {}
    for name in names:
        m.Set('Input', name, q)
        m.Update('Input', q)
        readback[name] = m.ReadStatus('Input', q)
    check('U3 the UN inputs the module sets read back', all(readback[n] == n for n in names))
    m.Set('Power', 'Off', q)
    m.Update('Power', q)
    check('U4 Set Power Off -> Off, no module errors throughout',
          m.ReadStatus('Power', q) == 'Off' and not m.errors)
finally:
    dev.close()

finish()
