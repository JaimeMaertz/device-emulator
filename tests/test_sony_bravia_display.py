"""
Sony BRAVIA Professional display, RS-232C protocol: Sony's own example frames (pro-bravia.sony.net serial-control
pages) sent raw, then the Extron module sonydisplayFWxxBZ30LxxBZ35LxxBZ40LxxBZ50LSerie run against the profile through
its SerialOverEthernetClass.
    python tests/test_sony_bravia_display.py
"""
import time

from harness import check, finish, load_module, start_device, RawClient  # noqa: F401

h = lambda s: bytes.fromhex(s.replace(' ', ''))

# ----- Sony's example frames
dev, port = start_device('sony_bravia_display')
raw = RawClient(port)
check('1 Power Query (83 00 00 FF FF 81) in standby answers off', raw.send(h('83 00 00 FF FF 81')) == h('70 00 02 00 72'))
check('2 an input select in standby is canceled', raw.send(h('8C 00 02 03 04 01 96')) == h('70 03 73'))
check('3 Power On (8C 00 00 02 01 8F) completes', raw.send(h('8C 00 00 02 01 8F')) == h('70 00 70') and dev.state['power'] == 'On')
check('4 HDMI 2 (8C 00 02 03 04 02 97) selects it', raw.send(h('8C 00 02 03 04 02 97')) == h('70 00 70') and dev.state['input'] == 'HDMI 2')
check('5 Input Query (83 00 02 FF FF 83) answers HDMI 2', raw.send(h('83 00 02 FF FF 83')) == h('70 00 03 04 02 79'))
check('6 a bad checksum is a parse error', raw.send(h('8C 00 00 02 01 00')) == h('70 04 74') and dev.state['power'] == 'On')
check('7 Power Off (8C 00 00 02 00 8E) completes', raw.send(h('8C 00 00 02 00 8E')) == h('70 00 70') and dev.state['power'] == 'Off')
check('8 a command split across reads is taken whole', raw.send(h('8C 00 00'), 0.05) == b'' and raw.send(h('02 01 8F')) == h('70 00 70'))
dev.close()

# ----- the Extron module
SONY = load_module('sonydisplayFWxxBZ30LxxBZ35LxxBZ40LxxBZ50LSerie.py')
dev, port = start_device('sony_bravia_display')
m = SONY.SerialOverEthernetClass('127.0.0.1', port, Model='FW-75BZ40L')
m.errors, m.discards = [], []
m.Error = lambda message: m.errors.append(message[0])
m.Discard = lambda message: m.discards.append(message)
m.Connect()


def update(command):
    m.Update(command)
    time.sleep(0.2)
    return m.ReadStatus(command)


check('9 the module reads power off', update('Power') == 'Off')
m.Set('Power', 'On')
time.sleep(0.3)
check('10 the module turns it on', dev.state['power'] == 'On' and update('Power') == 'On')
m.Set('Input', 'HDMI 3')
time.sleep(0.3)
check('11 the module selects HDMI 3 and reads it back', dev.state['input'] == 'HDMI 3' and update('Input') == 'HDMI 3')
m.Set('Volume', 35)
time.sleep(0.3)
check('12 the module sets and reads the volume', update('Volume') == 35)
m.Set('AVMute', 'On')
time.sleep(0.3)
check('13 AV mute on, read back', update('AVMute') == 'On')
check('14 the module logged no errors', m.errors == [])
dev.close()
finish()
