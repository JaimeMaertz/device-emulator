"""
Atlona AT-OME-PS62 profile against the ControlScript driver atna_switcher_AT_OME_PS62_v1_1_0_0 (a
privately written driver, not in Extron's catalog and not distributed; without it this test is skipped)
over a real localhost socket, plus the API doc's examples,
and serial over a pty (skipped on Windows). The driver paces its sends 500 ms apart, so reads
wait for its queue.   Run:  python tests/test_atlona_switcher.py
"""
import os
import time
from harness import check, finish, load_module, start_device, wait_for, RawClient, PROFILES, SerialTransport

ATNA = load_module('atna_switcher_AT_OME_PS62_v1_1_0_0_extronlib.py')


def watch(m):
    m.errors = []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Discard = lambda message, m=m: m.errors.append(message)
    return m


def read(m, command, qualifier=None, timeout=3.0):
    """Update, then wait for the paced queue to send it and the reply to land."""
    m.Update(command, qualifier)
    wait_for(lambda: m.ReadStatus(command, qualifier) is not None, timeout)
    time.sleep(0.6)
    return m.ReadStatus(command, qualifier)


def settle(m, seconds=0.6):
    """Let the driver's queue drain (one command per 0.5 s)."""
    time.sleep(seconds)


dev, port = start_device('atlona_switcher')
try:
    m = watch(ATNA.EthernetClass('127.0.0.1', port))
    m.Connect()
    check('1 Type reads AT-OME-PS62, Connected', read(m, 'Type') == 'AT-OME-PS62'
          and m.ReadStatus('ConnectionStatus') == 'Connected')

    m.Set('VideoRoute', 'HDMI3', {'Output': '1'})
    settle(m)
    check('2 route HDMI3 -> output 1 lands; Status ("x3Vx1", API doc form) reads both outputs',
          dev.state['out1'] == 'HDMI3' and read(m, 'VideoRoute', {'Output': '1'}) == 'HDMI3'
          and m.ReadStatus('VideoRoute', {'Output': '2'}) == 'HDBT2')
    dev.set_option('status_form', 'x3AVx1 (Q-SYS plugin)')
    dev.panel('out2', 'USB-C')
    check('3 plugin form "x6AVx2" parses too', read(m, 'VideoRoute', {'Output': '2'}) == 'USB-C')

    m.Set('Volume', -35, {'Zone': '3'})
    m.Set('Volume', -35, {'Zone': '4'})
    m.Set('AudioMute', 'On', {'Zone': '3'})
    settle(m, 1.8)
    check('4 the room\'s volume + mute on zones 3 and 4 land and read back',
          (dev.state['vol3'], dev.state['vol4'], dev.state['mute3']) == (-35, -35, True)
          and read(m, 'Volume', {'Zone': '3'}) == -35 and read(m, 'AudioMute', {'Zone': '3'}) == 'On')
    m.Set('VolumeStep', 'Up', {'Zone': '3'})
    settle(m)
    check('5 VolumeStep Up (VOUT3 +) -> -34, reported through Volume',
          wait_for(lambda: m.ReadStatus('Volume', {'Zone': '3'}) == -34, 2))

    m.Set('Mono', 'Mono', {'Output': '1'})
    m.Set('Mono', 'Mono', {'Output': '2'})
    settle(m, 1.2)
    check('6 the room\'s Mono on both outputs lands and reads back',
          dev.state['mono1'] and dev.state['mono2'] and read(m, 'Mono', {'Output': '2'}) == 'Mono'
          and not m.errors)

    read(m, 'InputStatus', {'Input': '1'})
    check('7 InputStatus, 2 digits (API doc): inputs 1-2 known, 3-6 never get a status',
          m.ReadStatus('InputStatus', {'Input': '2'}) == 'Signal Detected'
          and m.ReadStatus('InputStatus', {'Input': '3'}) is None)
    dev.set_option('input_status_width', 6)
    dev.panel('signal3', True)
    read(m, 'InputStatus', {'Input': '1'})
    check('8 ...6 digits (plugin): input 3 Signal Detected, input 6 No Signal',
          m.ReadStatus('InputStatus', {'Input': '3'}) == 'Signal Detected'
          and m.ReadStatus('InputStatus', {'Input': '6'}) == 'No Signal')

    read(m, 'AudioSource', {'Output': '1'})
    check('9 "AUD1 sta" (not in the API doc) answers Command FAILED, which the driver reports',
          any('FAILED' in str(e) for e in m.errors))
    m.errors.clear()
    dev.set_option('sta_aud_usbmode', 'answers (Q-SYS plugin)')
    dev.set_option('usb_reply', 'bare digit (Q-SYS plugin fallback)')
    check('10 with the plugin\'s readings: AudioSource reads, and bare-digit UsbMode/UsbRoute replies '
          'are attributed to the query before them',
          read(m, 'AudioSource', {'Output': '1'}) == 'HDMI' and read(m, 'UsbMode') == 'Follow USB'
          and read(m, 'UsbRoute') == 'USB Host 1' and not m.errors)

    m.Set('RepCmdTime', '3')
    settle(m)
    check('11 RepCmdTime 3 lands; its "RepeatCmd 3" reply reads back as RepCmdTime',
          dev.state['repeat_count'] == 3 and read(m, 'RepCmdTime') == '3')
    check('12 IPCFG: all five network statuses from one query',
          read(m, 'IPAddr') == '192.168.1.254' and m.ReadStatus('Netmask') == '255.255.255.0'
          and m.ReadStatus('Gateway') == '192.168.1.1' and m.ReadStatus('SSHPort') == '22')

    dev.set_option('pacing', 'Command FAILED')
    m.errors.clear()
    for level in range(-30, -20):
        m.Set('Volume', level, {'Zone': '3'})       # a slider drag: latest wins in the driver's queue
    m.Set('VideoRoute', 'HDBT2', {'Output': '1'})
    settle(m, 2)
    check('13 with 500 ms pacing enforced by the switcher, a slider drag + route: nothing refused, '
          'the last level lands (driver coalesces and paces)',
          not m.errors and dev.state['vol3'] == -21 and dev.state['out1'] == 'HDBT2')
    dev.set_option('pacing', 'processed')

    dev.set_option('push_routes', True)
    dev.panel('out1', 'HDMI4')
    check('14 option: front-panel routing sends "x4AVx1", which updates VideoRoute live',
          wait_for(lambda: m.ReadStatus('VideoRoute', {'Output': '1'}) == 'HDMI4', 2))
    m.Set('Lock', 'On')
    settle(m)
    dev.panel('out1', 'HDBT1')
    check('15 Lock: the front panel can\'t change routes', dev.state['locked'] and dev.state['out1'] == 'HDMI4')
    m.Disconnect()

    # ----- the API doc's examples
    raw = RawClient(port)
    check('16 doc: "x2AVx1" -> "x2AVx1"; "x3AVx1, x2" -> "x3AVx1, x3AVx2"',
          raw.send(b'x2AVx1\r', 0.6) == b'x2AVx1\r\n' and raw.send(b'x3AVx1, x2\r', 0.6) == b'x3AVx1, x3AVx2\r\n')
    check('17 doc: "x3All" -> "x3AVx1, x3AVx2"; "All#" -> "x1AVx1, x2AVx2"',
          raw.send(b'x3All\r', 0.6) == b'x3AVx1, x3AVx2\r\n' and raw.send(b'All#\r', 0.6) == b'x1AVx1, x2AVx2\r\n')
    check('18 doc: "VOUT1 4" -> "VOUT1 4", "VOUT1 +" -> "VOUT1 5"; out of range -> Command FAILED',
          raw.send(b'VOUT1 4\r', 0.6) == b'VOUT1 4\r\n' and raw.send(b'VOUT1 +\r', 0.6) == b'VOUT1 5\r\n'
          and raw.send(b'VOUT1 11\r', 0.6) == b'Command FAILED\r\n')
    check('19 commands are case-sensitive: "status" -> Command FAILED',
          raw.send(b'status\r', 0.6) == b'Command FAILED\r\n')
    check('20 doc: "RS232zone2[VOL23]" is echoed; "RS232para sta" -> "RS232para[115200,8,0,1]"',
          raw.send(b'RS232zone2[VOL23]\r', 0.6) == b'RS232zone2[VOL23]\r\n'
          and raw.send(b'RS232para sta\r', 0.6) == b'RS232para[115200,8,0,1]\r\n')
    dev.set_option('reboot_seconds', 1)
    check('21 doc: "IPStatic ..." gets no feedback and the unit reboots',
          raw.send(b'IPStatic 10.0.0.9 255.255.255.0 10.0.0.1\r', 0.6) == b''
          and dev.state['ip'] == '10.0.0.9' and wait_for(lambda: dev.state['rebooting'], 1)
          and wait_for(lambda: not dev.state['rebooting'], 3))
    raw.close()
finally:
    dev.close()


# ----- serial (pty pair), the way a room wires it to a processor COM port
if not hasattr(os, 'openpty'):
    print('SKIP 22 serial over a pty (no pty on Windows; test a real COM port instead)')
else:
    master, slave = os.openpty()
    dev = PROFILES['atlona_switcher']('serial')
    try:
        dev.add_transport(SerialTransport(dev, os.ttyname(slave), 115200))
        m = watch(ATNA.SerialClass(None, master))
        m.Set('VideoRoute', 'HDBT2', {'Output': '1'})
        settle(m)
        check('22 over serial at 115200: route lands and Status reads it back',
              dev.state['out1'] == 'HDBT2' and read(m, 'VideoRoute', {'Output': '1'}) == 'HDBT2')
    finally:
        dev.close()

finish()
