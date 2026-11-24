"""
Christie GS 630 / 635 projector profile: raw frames against the GS Series 630 and 635 serial
reference (020-102559-03), then the Extron module chri_vp_DHD630_DWU630_GS_v1_0_2_0 against it
over a real localhost socket.   Run:  python tests/test_christie_projector.py
"""
import time
from harness import check, finish, load_module, start_device, wait_for, RawClient

CHRISTIE = load_module('chri_vp_DHD630_DWU630_GS_v1_0_2_0.py')
WARM, COOL = 2.0, 1.5   # long enough for several 0.2 s raw sends inside each


def connect(port):
    m = CHRISTIE.EthernetClass('127.0.0.1', port, Model='DWU630-GS')
    m.errors = []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Connect()
    return m


def status(m, command):
    m.Update(command)
    time.sleep(0.15)
    return m.ReadStatus(command)


dev, port = start_device('christie_projector', model='DWU630-GS', options={'warmup': WARM, 'cooldown': COOL})
try:
    raw = RawClient(port)
    check('1 manual: (PWR?) while off -> (PWR!0)', raw.send(b'(PWR?)') == b'(PWR!0)')
    check('2 manual: a set gets no reply', raw.send(b'(PWR 1)') == b'' and dev.state['power'] == 'Warming Up')
    check('3 manual: a space is optional, (PWR ?) reads 1 while warming up', raw.send(b'(PWR ?)') == b'(PWR!1)')
    check('4 guess: a power command during warm-up is refused with an error',
          raw.send(b'(PWR 0)').startswith(b'(65535 00000 ERR') and dev.state['power'] == 'Warming Up')
    wait_for(lambda: dev.state['power'] == 'On', 4)
    check('5 manual: (#SHU 1) closes the shutter and echoes the message',
          raw.send(b'(#SHU 1)') == b'(#SHU 1)' and dev.state['shutter'] == 1)
    check('6 manual SHU page: (SHU?) -> (SHU!1)', raw.send(b'(SHU?)') == b'(SHU!1)')
    raw.send(b'(SIN+MAIN 3)')
    check('7 manual: SIN+MAIN 3 selects HDMI 1, read back (SIN+MAIN!3)',
          dev.state['input'] == 3 and raw.send(b'(SIN+MAIN?)') == b'(SIN+MAIN!3)')
    check('8 manual: the error shape, (ITP) -> ERR00005 "... Too Few Parameters"',
          raw.send(b'(SZP)') == b'(65535 00000 ERR00005 "SZP: Too Few Parameters")')
    check('9 manual: a new "(" discards the partial message before it',
          raw.send(b'(PWR 0(SHU?)') == b'(SHU!1)' and dev.state['power'] == 'On')
    check('10 an input the manual has no code for (6) is refused',
          raw.send(b'(SIN+MAIN 6)').startswith(b'(65535') and dev.state['input'] == 3)
    raw.close()

    m = connect(port)
    check('11 module: Power On, Connected', status(m, 'Power') == 'On'
          and m.ReadStatus('ConnectionStatus') == 'Connected')
    for name in ('HDBaseT', 'HDMI 2', 'HDMI 1'):
        m.Set('Input', name)
        time.sleep(0.05)
    check('12 module: inputs set and read back', status(m, 'Input') == 'HDMI 1' and dev.state['input'] == 3)
    m.Set('VideoMute', 'Off')
    time.sleep(0.05)
    check('13 module: VideoMute is the shutter: Off opens it, reads Off',
          dev.state['shutter'] == 0 and status(m, 'VideoMute') == 'Off')
    check('14 module: light source hours parse', status(m, 'OperationHours') is not None)
    m.Set('Power', 'Off')
    time.sleep(0.05)
    check('15 module: Power Off -> cooling down reads Off', dev.state['power'] == 'Cooling Down'
          and status(m, 'Power') == 'Off')
    wait_for(lambda: dev.state['power'] == 'Off', 4)
    m.Set('Input', 'HDMI 2')
    time.sleep(0.1)
    check('16 guess: an input set while off is refused, and the module reports the error',
          dev.state['input'] == 3 and any('ERR' in e for e in m.errors))
    m.errors.clear()
    m.Set('Power', 'On')
    time.sleep(0.05)
    check('17 no module errors for the ordinary sets and reads', not m.errors)
finally:
    dev.close()

dev, port = start_device('christie_projector', options={'reply_format': 'padded: (PWR!001 "On") (format chapter)'})
try:
    raw = RawClient(port)
    check('18 option: the format chapter\'s padded reply', raw.send(b'(PWR?)') == b'(PWR!000 "Off")')
    raw.close()
    m = connect(port)
    m.Update('Power')
    time.sleep(0.15)
    check('19 ...which the module can\'t parse: no Power status', m.ReadStatus('Power') is None)
finally:
    dev.close()

finish()
