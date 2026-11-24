"""
The DM NVX profile over a real outgoing CIP transport, against crestron_cip's FakeProcessor sending what a
real RMC4 sent emulated NVXs (recorded on the wire): a route
on serial 1, an MDC poll on a decoder's COM slot, the USB slot's remote ID, Pair and RemovePairing. The COM slot is
wired to a real Samsung display profile on this bench.
    python tests/test_crestron_nvx.py
"""
import time

from harness import check, finish  # noqa: F401  (harness sets the import paths)

from emulator.bench import build  # noqa: E402
from crestron_cip import FakeProcessor, protocol as cip  # noqa: E402

COM_PORT = 15161
URL = 'rtsp://10.0.0.19:554/live.sdp'


def wait(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


fp = FakeProcessor([0x13, 0x31, 0x3C])
cs = fp.session
display = build({'profile': 'samsung_display', 'name': 'Display 1', 'options': {'display_id': 1},
                 'transports': [{'type': 'tcp', 'port': COM_PORT}]})
enc = build({'profile': 'crestron_nvx', 'model': 'DM-NVX-E30', 'name': 'Encoder 3', 'options': {'ipid': '13'},
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
dec = build({'profile': 'crestron_nvx', 'model': 'DM-NVX-D30', 'name': 'Decoder 1',
             'options': {'ipid': '31', 'com_target': '127.0.0.1:{}'.format(COM_PORT)},
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
maint = build({'profile': 'crestron_nvx', 'model': 'DM-NVX-360', 'name': 'Decoder 12', 'options': {'ipid': '3C'},
               'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})

check('1 the encoder reports its stream URL (serial 1)',
      wait(lambda: cs(0x13) is not None and cs(0x13).get('s', 1) == 'rtsp://10.0.0.19:554/live.sdp'))
check('1b and its HDMI input signal (slot 4, digital 1)', wait(lambda: cs(0x13).get_slot(4, 'd', 1) == 1))
enc.panel('sync', False)
check('1c the signal dropped at the encoder reaches the processor', wait(lambda: cs(0x13).get_slot(4, 'd', 1) == 0))

fp.drive('s', 4, '239.1.1.24')
check('1d the program\'s MulticastAddress (serial 4) comes back as MulticastAddress_F',
      wait(lambda: enc.state['multicast'] == '239.1.1.24' and cs(0x13).get('s', 4) == '239.1.1.24'))

check('2 the decoder registers', wait(lambda: cs(0x31) is not None))
fp.drive('s', 1, URL)
check('2b a route on serial 1 reaches the decoder', wait(lambda: dec.state['route'] == URL))
check('2c and comes back as StreamLocation_F', wait(lambda: cs(0x31).get('s', 1) == URL))
check('2d the decoder reports the multicast address of the encoder it receives',
      wait(lambda: dec.state['multicast'] == '239.1.1.24' and cs(0x31).get('s', 4) == '239.1.1.24'))

check('3 the COM port is wired to the display', wait(lambda: dec.state['com'].startswith('wired'), 8))
fp.send_raw(0x31, bytes.fromhex('05 00 0d 00 00 0a 20 08 07 12 00 aa 00 01 00 01'))   # the MDC status poll
check('3b an MDC poll on the COM slot gets the display\'s status reply',
      wait(lambda: (cs(0x31).get_slot(8, 's', 1) or '').startswith('\xaa\xff\x01')))

check('4 the 360 reports its USB local ID (slot 30, serial 12)',
      wait(lambda: cs(0x3C) is not None and cs(0x3C).get_slot(30, 's', 12) == '00:10:7f:00:00:3c'))
fp.send_raw(0x3C, cip.slot_serial_from_processor(30, 11, '00:10:7f:00:00:15'))
check('4b a remote device ID comes back as UsbRemoteDeviceId_F',
      wait(lambda: cs(0x3C).get_slot(30, 's', 11) == '00:10:7f:00:00:15'))
fp.send_raw(0x3C, cip.slot_digital(30, 11, True))
fp.send_raw(0x3C, cip.slot_digital(30, 11, False))
check('4c Pair pairs it (Paired_F, digital 13)', wait(lambda: cs(0x3C).get_slot(30, 'd', 13) == 1))
fp.send_raw(0x3C, cip.slot_digital(30, 12, True))
fp.send_raw(0x3C, cip.slot_digital(30, 12, False))
check('4d RemovePairing unpairs it', wait(lambda: cs(0x3C).get_slot(30, 'd', 13) == 0))

for d in (enc, dec, maint, display):
    d.close()
fp.close()
finish()
