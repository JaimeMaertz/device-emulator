"""
NEC projector profile against the real Extron module nec_vp_NP_P547UL_P627UL_v1_1_0_0, connected
over a real localhost socket. If the module reads the emulator correctly, it reads the same shapes
from the projector.   Run:  python tests/test_nec_projector.py
"""
import time
from harness import check, finish, load_module, start_device, wait_for, RawClient, refused

NEC = load_module('nec_vp_NP_P547UL_P627UL_v1_1_0_0.py')
WARM, COOL = 1.0, 1.0
QUERY = b'\x00\xBF\x00\x00\x01\x02\xC2'


def connect(port):
    m = NEC.EthernetClass('127.0.0.1', port)
    m.errors = []
    m.Error = lambda message, m=m: m.errors.append(message)
    m.Connect()
    return m


def status(m, command='Power'):
    """Poll the way a program does (Update) and return what the module parsed."""
    before = m.counter
    m.Update('Power')
    wait_for(lambda: m.counter == 0 and before >= 0, 0.5)
    time.sleep(0.1)
    return m.ReadStatus(command)


dev, port = start_device('nec_projector', options={'warmup': WARM, 'cooldown': COOL})
dev2, port2 = start_device('nec_projector')
try:
    raw = RawClient(port)
    reply = raw.send(QUERY)
    check('1 status reply is the 22-byte running-status packet with a valid checksum',
          len(reply) == 22 and reply[:2] == b'\x20\xBF' and sum(reply[:-1]) & 0xFF == reply[-1])

    a, b = connect(port), connect(port2)
    check('2 module parses it: Power Off, Input HDBaseT, mutes Off',
          (status(a), a.ReadStatus('Input'), a.ReadStatus('VideoMute'), a.ReadStatus('AudioMute'),
           a.ReadStatus('Freeze')) == ('Off', 'HDBaseT', 'Off', 'Off', 'Off'))

    a.Set('Power', 'On')
    check('3 power on -> Warming Up', status(a) == 'Warming Up')
    a.Set('Power', 'Off')
    check('4 power off during warm-up refused, still Warming Up, module logs no error',
          status(a) == 'Warming Up' and not a.errors)
    time.sleep(WARM + 0.2)
    check('5 after warm-up -> On', status(a) == 'On')
    check('6 devices are independent (second projector still Off)', status(b) == 'Off')
    a.Set('Power', 'Off')
    check('7 power off -> Cooling Down', status(a) == 'Cooling Down')
    a.Set('Power', 'On')
    check('8 power on during cool-down refused', status(a) == 'Cooling Down')
    time.sleep(COOL + 0.2)
    check('9 after cool-down -> Off', status(a) == 'Off')

    seen = []
    for name in ('HDMI 1', 'HDMI 2', 'Computer', 'USB-A', 'LAN', 'HDBaseT'):
        a.Set('Input', name)
        seen.append(status(a, 'Input') == name)
    check('10 every input the module can set reads back through the status reply', all(seen))

    for command in ('VideoMute', 'AudioMute', 'Freeze'):
        a.Set(command, 'On')
    status(a)
    check('11 picture mute / sound mute / freeze set by the module read back On',
          (a.ReadStatus('VideoMute'), a.ReadStatus('AudioMute'), a.ReadStatus('Freeze')) == ('On', 'On', 'On'))

    before = {c: a.ReadStatus(c) for c in ('Power', 'Input', 'VideoMute', 'LampMode', 'LampUsage', 'Volume')}
    for command, value in (('AspectRatio', '16:9'), ('AutoImage', None), ('LampMode', 'Eco'),
                           ('MenuNavigation', 'Menu'), ('MenuNavigation', 'Exit'), ('Volume', 10)):
        a.Set(command, value)
    status(a)
    after = {c: a.ReadStatus(c) for c in before}
    check('12 remaining Sets acknowledged without disturbing any parsed status or raising',
          before == after and not a.errors)

    raw.sock.sendall(b'\x02\x00\x00\x00\x00\x05')   # bad checksum
    time.sleep(0.2)
    check('13 bad checksum dropped, emulator keeps answering', len(raw.send(QUERY)) == 22)

    # Front panel: a change made at the projector shows up on the processor's next poll
    for key in ('VideoMute', 'AudioMute', 'Freeze'):
        a.Set(key, 'Off')
    dev.panel('power_on', True)
    check('14 remote Power On -> module sees Warming Up on its next poll', status(a) == 'Warming Up')
    time.sleep(WARM + 0.2)
    dev.panel('input', 'HDMI 2')
    dev.panel('videoMute', True)
    check('15 remote input + picture mute read back by the module',
          (status(a, 'Input'), a.ReadStatus('VideoMute')) == ('HDMI 2', 'On'))
    dev.panel('power_off', True)
    dev.panel('input', 'LAN')
    check('16 remote ignores input changes while not On', dev.state['input'] == 'HDMI 2')
    time.sleep(COOL + 0.2)

    # Faults
    dev.set_fault('silent', True)
    check('17 silent fault: no reply', raw.send(QUERY) == b'')
    dev.set_fault('silent', False)
    dev.set_fault('corrupt', True)
    reply = raw.send(QUERY)
    check('18 corrupt fault: reply arrives with a bad checksum', len(reply) == 22 and sum(reply[:-1]) & 0xFF != reply[-1])
    dev.set_fault('corrupt', False)
    dev.drop_clients()
    check('19 drop clients closes the connection', raw.closed_by_peer())
    dev.set_fault('offline', True)
    check('20 offline fault: new connections are refused', refused(port))
    dev.set_fault('offline', False)

    # The queries the module polls, parsed by the module's own match strings
    m = connect(port)
    wait_for(lambda: dev.state['power'] == 'Off', 1)
    for command in ('DeviceStatus', 'LampMode', 'LampUsage', 'Volume'):
        m.Update(command)
    time.sleep(0.3)
    check('20a device status / light mode / light hours / volume read back through the module',
          (m.ReadStatus('DeviceStatus'), m.ReadStatus('LampMode'), m.ReadStatus('LampUsage'), m.ReadStatus('Volume'))
          == ('Normal', 'Eco', 1200, 10))   # check 12 set Eco and volume 10
    m.Set('LampMode', 'Eco')
    m.Set('Volume', 22)
    dev.panel('deviceStatus', 'Lamp Off')
    for command in ('DeviceStatus', 'LampMode', 'Volume'):
        m.Update(command)
    time.sleep(0.3)
    check('20b sets land, and an error raised on the front panel reads back as the module decodes it',
          (m.ReadStatus('DeviceStatus'), m.ReadStatus('LampMode'), m.ReadStatus('Volume')) == ('Lamp Off', 'Eco', 22))
    m.Disconnect()
    check('21 log recorded the refused power commands',
          any(e[2] == 'info' and 'refused' in str(e[3]) for e in dev.entries))
finally:
    dev.close()
    dev2.close()

dev, port = start_device('nec_projector', options={'normal_standby': True})
try:
    raw = RawClient(port)
    silent = raw.send(QUERY) == b''
    raw.send(b'\x02\x00\x00\x00\x00\x02')
    check('22 NORMAL standby: no reply and no power-on while off', silent and dev.state['power'] == 'Off')
finally:
    dev.close()

dev, port = start_device('nec_projector', options={'start_on': True})
try:
    check('23 start_on starts On', status(connect(port)) == 'On')
    dev.set_option('start_on', False)
    dev.power_cycle()
    check('24 power cycle returns to the reset state (Off)', dev.state['power'] == 'Off')
finally:
    dev.close()

finish()
