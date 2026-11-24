"""
LG display profile against the real Extron module lg_display_xxUH5J_xxUH7J_Series_v1_0_4_0, over
TCP and over a serial line (a pty pair: the emulator's SerialTransport on one end through pyserial,
the module on the other).   Run:  python tests/test_lg_display.py
"""
import os
import time
from harness import check, finish, load_module, start_device, wait_for, RawClient, PROFILES, SerialTransport

LG = load_module('lg_display_xxUH5J_xxUH7J_Series_v1_0_4_0.py')


def watch(m):
    m.errors = []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    return m


def update(m, command):
    m.Update(command)
    time.sleep(0.15)
    return m.ReadStatus(command)


dev, port = start_device('lg_display')
try:
    m = watch(LG.EthernetClass('127.0.0.1', port))
    m.Connect()
    check('1 power query while off -> Off, and the module counts the display as Connected',
          update(m, 'Power') == 'Off' and m.ReadStatus('ConnectionStatus') == 'Connected')
    m.Set('Volume', 30)
    check('2 while off, other commands answer NG and the module reports the error',
          any('Volume' in e for e in m.errors) and dev.state['volume'] == 20)
    m.errors.clear()
    m.Set('Power', 'On')
    check('3 Set Power On -> On, module reads On', dev.state['power'] == 'On' and update(m, 'Power') == 'On')

    names = ['HDMI 1 (DTV)', 'HDMI 1 (PC)', 'HDMI 2 (DTV)', 'HDMI 2 (PC)', 'HDMI 3 (DTV)', 'HDMI 3 (PC)',
             'DisplayPort (DTV)', 'DisplayPort (PC)', 'DVI-D (DTV)', 'DVI-D (PC)', 'USB-C (DTV)', 'USB-C (PC)', 'SuperSign']
    readback = {}
    for name in names:
        m.Set('Input', name)
        readback[name] = update(m, 'Input')
    check('4 every input the module sets is accepted and reads back as the same name',
          not m.errors and all(readback[n] == n for n in names))

    m.Set('Volume', 42); m.Set('AudioMute', 'On'); m.Set('VideoMute', 'On'); m.Set('AspectRatio', 'Original')
    check('5 volume / mute / screen off / aspect set by the module land in the emulator',
          (dev.state['volume'], dev.state['audioMute'], dev.state['screenOff'], dev.state['aspect'])
          == (42, True, True, 'Original'))
    check('6 ...and read back through the module (mute is inverted on the wire: ke 00 = muted)',
          (update(m, 'Volume'), update(m, 'AudioMute'), update(m, 'VideoMute'), update(m, 'AspectRatio'))
          == (42, 'On', 'On', 'Original'))

    raw = RawClient(port)
    check('7 raw reply format: "a 01 OK01x"', raw.send(b'ka 01 FF\r') == b'a 01 OK01x')
    check('8 another Set ID is ignored; broadcast 00 acts without replying',
          raw.send(b'kf 02 10\r') == b'' and raw.send(b'kf 00 10\r') == b'' and dev.state['volume'] == 0x10)
    check('9 out-of-range data -> NG', raw.send(b'kf 01 65\r') == b'f 01 NG65x')
    check('9a unsupported commands answer NG whatever their field count',
          raw.send(b'jq 01 FF\r') == b'q 01 NGFFx' and raw.send(b'sn 01 0c FF\r') == b'n 01 NGFFx')

    # Front panel -> feedback on the next poll
    dev.panel('volume', 77)
    dev.panel('audioMute', False)
    dev.panel('input', 'HDMI2 (DTV)')
    check('10 remote changes read back by the module',
          (update(m, 'Volume'), update(m, 'AudioMute'), update(m, 'Input')) == (77, 'Off', 'HDMI 2 (DTV)'))
    m.Set('ExecutiveMode', 'On')
    dev.panel('volume', 5)
    dev.panel('key_power', True)
    check('11 key lock (km 01) blocks the remote, including power while on',
          dev.state['volume'] == 77 and dev.state['power'] == 'On')
    m.Set('ExecutiveMode', 'Off')
    dev.panel('key_off', True)
    check('12 MONITOR OFF from the remote -> module reads Off', update(m, 'Power') == 'Off')

    # The module's MenuNavigation 'Down' sends mc 11, which the manual lists as number key 1 (Down is 41)
    m.Set('Power', 'On')
    dev.entries.clear()
    m.Set('MenuNavigation', 'Down')
    check('13 FINDING: module MenuNavigation Down sends IR key 11 (number key 1), not 41',
          any(e[2] == 'rx' and e[3] == b'mc 01 11' for e in dev.entries))

    dev.set_option('off_reply', 'no reply')
    m.Set('Power', 'Off')
    check('14 option "no reply" while off: silent for non-power commands',
          raw.send(b'kf 01 FF\r') == b'' and raw.send(b'ka 01 FF\r') == b'a 01 OK00x')
    raw.send(b'mc 01 c4\r')
    check('15 IR MONITOR ON (mc c4) works while off', dev.state['power'] == 'On')
    raw.send(b'ka 01 02\r')
    check('16 restart (ka 02): off, then back on', dev.state['power'] == 'Off')
    dev.set_option('restart_seconds', 0)
    check('   ...and back on after the restart time', wait_for(lambda: dev.state['power'] == 'On', 4))
    m.Disconnect()
    raw.close()
finally:
    dev.close()

def serial_checks():
    """pyserial on the emulator's end of a pty, the module on the other (Linux/WSL only)."""
    master, slave = os.openpty()
    dev = PROFILES['lg_display']('serial test')
    try:
        dev.add_transport(SerialTransport(dev, os.ttyname(slave), 9600))
        check('17 serial transport opened through pyserial', not dev.transports[0].error)
        m = watch(LG.SerialClass(None, master))
        m.Set('Power', 'On')
        m.Set('Volume', 12)
        check('18 module over serial: power on, volume set, read back',
              dev.state['power'] == 'On' and update(m, 'Volume') == 12 and not m.errors)
        dev.panel('audioMute', True)
        check('19 remote mute read back over serial', update(m, 'AudioMute') == 'On')
        m.Close()
    finally:
        dev.close()
        os.close(master)


if hasattr(os, 'openpty'):
    serial_checks()
else:
    print('SKIP 17-19 serial over a pty (no pty on Windows; test a real COM port instead)')

finish()
