"""
Epson projector profile (ESC/VP.net, TCP 3629): raw frames against the ESC/VP.net protocol manual's session layer,
then Extron's catalog driver for the PU100x / PU2010 series,
epsn_vp_CB_EB_PU100xx_PU2010x_Series_v1_0_2_0, against it over a real localhost socket.
Run:  python tests/test_epson_projector.py
"""
import time
from harness import check, finish, load_module, start_device, wait_for, RawClient

EPSON = load_module('epsn_vp_CB_EB_PU100xx_PU2010x_Series_v1_0_2_0.py')
WARM, COOL = 1.5, 1.0
CONNECT = b'ESC/VP.net\x10\x03\x00\x00\x00\x00'
OK = b'ESC/VP.net\x10\x03\x00\x00\x20\x00'


def connect_with_password(password):
    info = password.encode().ljust(16, b'\x00')
    return b'ESC/VP.net\x10\x03\x00\x00\x00\x01' + b'\x01\x01' + info


def status(m, command):
    m.Update(command)
    time.sleep(0.2)
    return m.ReadStatus(command)


dev, port = start_device('epson_projector', model='EB-PU1007B', options={'warmup': WARM, 'cooldown': COOL})
try:
    raw = RawClient(port)
    check('1 manual: a command before CONNECT gets nothing (session mode only)', raw.send(b'PWR?\r') == b'')
    check('2 manual: CONNECT with no password -> status 0x20, 16 bytes, no headers', raw.send(CONNECT) == OK)
    check('3 manual: a null line is answered ":"', raw.send(b'\r') == b':')
    check('4 driver: PWR? in standby -> PWR=04 (network on)', raw.send(b'PWR?\r') == b'PWR=04\r:')
    check('5 gotchas: SOURCE? in standby -> ERR', raw.send(b'SOURCE?\r') == b'ERR\r:')
    check('6 driver: LAMP? answers in standby', raw.send(b'LAMP?\r') == b'LAMP=1500\r:')

    other = RawClient(port)
    check('7 manual: a second session gets 0x53 busy...', other.send(CONNECT) == b'ESC/VP.net\x10\x03\x00\x00\x53\x00')
    check('8 ...and the projector closes that connection', other.closed_by_peer())
    other.close()

    check('9 driver: PWR ON -> ":" and warming up', raw.send(b'PWR ON\r') == b':' and dev.state['power'] == 'Warming Up')
    check('10 driver: PWR? while warming -> PWR=02', raw.send(b'PWR?\r') == b'PWR=02\r:')
    check('11 guess: PWR OFF while warming up -> ERR', raw.send(b'PWR OFF\r') == b'ERR\r:')
    wait_for(lambda: dev.state['power'] == 'On', 4)
    check('12 driver: PWR=01 once on', raw.send(b'PWR?\r') == b'PWR=01\r:')
    check('13 driver: SOURCE 80 (HDBaseT) sets and reads back',
          raw.send(b'SOURCE 80\r') == b':' and raw.send(b'SOURCE?\r') == b'SOURCE=80\r:')
    check('14 driver: SDI (60) is PU2010 only -> ERR on a PU1007B', raw.send(b'SOURCE 60\r') == b'ERR\r:')
    check('15 driver: MUTE ON, MUTE? -> MUTE=ON', raw.send(b'MUTE ON\r') == b':' and raw.send(b'MUTE?\r') == b'MUTE=ON\r:')
    check('16 an unknown command -> ERR', raw.send(b'BOGUS\r') == b'ERR\r:')
    check('17 two commands in one packet are both answered', raw.send(b'FREEZE?\rASPECT?\r') == b'FREEZE=OFF\r:ASPECT=00\r:')
    raw.close()

    m = EPSON.EthernetClass('127.0.0.1', port, Model='EB-PU1007B')
    m.errors = []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Connect()
    time.sleep(0.2)
    check('18 driver: Power reads On, and ConnectionStatus goes Connected', status(m, 'Power') == 'On'
          and m.ReadStatus('ConnectionStatus') == 'Connected')
    m.Set('Input', 'HDMI')
    time.sleep(0.15)
    check('19 driver: Input HDMI sets 30 and reads back', dev.state['input'] == '30' and status(m, 'Input') == 'HDMI')
    m.Set('AVMute', 'Off')
    time.sleep(0.15)
    check('20 driver: AVMute Off, reads Off', dev.state['mute'] == 'OFF' and status(m, 'AVMute') == 'Off')
    m.Set('Volume', 10)
    time.sleep(0.15)
    check('21 driver: Volume 10 -> VOL 121 -> reads 10', dev.state['volume'] == 121 and status(m, 'Volume') == 10)
    check('22 driver: lamp usage, operation hours and device status parse',
          status(m, 'LampUsage') == 1500 and status(m, 'OperationHours') == 1700 and status(m, 'DeviceStatus') == 'Normal')
    m.Set('Power', 'Off')
    time.sleep(0.15)
    check('23 driver: Power Off -> cooling down', dev.state['power'] == 'Cooling Down'
          and status(m, 'Power') == 'Cooling Down')
    wait_for(lambda: dev.state['power'] == 'Off', 4)
    check('24 driver: standby reads Off', status(m, 'Power') == 'Off')
    m.errors.clear()
    m.Set('Input', 'HDBaseT')
    time.sleep(0.2)
    check('25 gotchas: an input set in standby is refused, and the driver reports the error',
          dev.state['input'] == '30' and any('error' in e.lower() for e in m.errors))
    m.Disconnect()
finally:
    dev.close()

dev, port = start_device('epson_projector', options={'password': 'admin1'})
try:
    raw = RawClient(port)
    check('26 manual: a password is set, CONNECT without one -> 0x41, closed',
          raw.send(CONNECT) == b'ESC/VP.net\x10\x03\x00\x00\x41\x00' and raw.closed_by_peer())
    raw.close()
    raw = RawClient(port)
    check('27 manual: a wrong password -> 0x43, closed',
          raw.send(connect_with_password('nope')) == b'ESC/VP.net\x10\x03\x00\x00\x43\x00' and raw.closed_by_peer())
    raw.close()
    raw = RawClient(port)
    check('28 manual: the right password -> 0x20, session open',
          raw.send(connect_with_password('admin1')) == OK and raw.send(b'PWR?\r') == b'PWR=04\r:')
    raw.close()
finally:
    dev.close()

dev, port = start_device('epson_projector', options={'idle_minutes': 0})
try:
    dev.tcp_idle_timeout = 0.6   # the 10-minute rule, shortened
    raw = RawClient(port)
    raw.send(CONNECT)
    check('29 manual: an idle session is closed by the projector', raw.closed_by_peer(2.0))
    raw.close()
finally:
    dev.close()

finish()
