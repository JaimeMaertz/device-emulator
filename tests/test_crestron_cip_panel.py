"""
The CIP panel profile over a real outgoing CIP transport, against crestron_cip's FakeProcessor (whose sequence
follows frames recorded from a real RMC4).
    python tests/test_crestron_cip_panel.py
"""
import time

from harness import check, finish, PROFILES  # noqa: F401  (harness sets the import paths)

from emulator.bench import build  # noqa: E402
from emulator.profiles.crestron_cip_panel import cip  # noqa: E402
from crestron_cip import FakeProcessor  # noqa: E402


def wait(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# 1. A CCC at 03 comes online, counts Hide Project, presses and sets the in-meeting join
fp = FakeProcessor([0x03])
dev = build({'profile': 'crestron_cip_panel', 'model': 'CCC-110-T', 'name': 'CCC test',
             'options': {'ipid': '03'},
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
check('1a registers and finishes the update', wait(lambda: dev.state['status'] == 'online'))
check('1b signs on as a CCC (0x26) with its MAC', fp.session(0x03).registrations == [cip.device_sign_on(0x03, '02:00:00:00:00:03', name='TEST')])
fp.drive('d', 18504, 1)
fp.drive('d', 18504, 0)
check('1c Hide Project (digital 18504) counted once', wait(lambda: dev.state['hide_project'] == 1))
check('1c2 and answered with Hide Project_fb, as the real CCC does', wait(lambda: fp.session(0x03).get('d', 18504) == 1 and fp.session(0x03).get('d', 18503) == 0))
dev.set('join', 7)
dev.panel('press', True)
check('1d a press reaches the processor', wait(lambda: fp.session(0x03).get('d', 7) == 1))
check('1e and its release', wait(lambda: fp.session(0x03).get('d', 7) == 0))
dev.panel('in_meeting', True)
check('1f In meeting sets digital 27767 (System4 O468)', wait(lambda: fp.session(0x03).get('d', 27767) == 1))
dev.panel('in_meeting', False)
check('1g and clears it', wait(lambda: fp.session(0x03).get('d', 27767) == 0))
cfg = dev.to_config()
check('1h the bench keeps the cip transport', cfg['transports'] == [{'type': 'cip', 'host': '127.0.0.1',
                                                                      'port': fp.port, 'tls': False}])
dev.close()
fp.close()

# 2. Unknown in-meeting join: nothing is sent, a warning is logged
fp = FakeProcessor([0x03])
dev = build({'profile': 'crestron_cip_panel', 'model': 'CCC-110-T', 'name': 'CCC no join',
             'options': {'in_meeting_join': 0}, 'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
wait(lambda: dev.state['status'] == 'online')
n = fp.session(0x03).packets_in
dev.panel('in_meeting', True)
time.sleep(0.3)
check('2a nothing sent without the join', fp.session(0x03).packets_in == n)
check('2b a warning says why', any(e[2] == 'warn' and 'In-meeting join' in str(e[3]) for e in dev.entries))
dev.close()
fp.close()

# 3. A processor that stops after 0x16 (the RMC4 at a CCC slot, to a generic sign-on) is shown as such
fp = FakeProcessor([0x03], penultimate_only=True)
dev = build({'profile': 'crestron_cip_panel', 'model': 'CCC-110-T', 'name': 'CCC stalled',
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
check('3a a CCC slot that ends at 0x16 reads as working', wait(lambda: 'ends its update at 0x16: working' in dev.state['status']))
dev.close()
fp.close()

# 4. A captured sign-on body goes out as given, with the IP ID in place of ii
fp = FakeProcessor([0x05])
dev = build({'profile': 'crestron_cip_panel', 'model': 'TSW / XPanel', 'name': 'custom sign-on',
             'options': {'ipid': '05', 'register_body': '7f 00 00 01 00 ii 40'},   # CommandFusion's older sign-on
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
check('4a online with the custom sign-on', wait(lambda: dev.state['status'] == 'online'))
check('4b the bytes are the option\'s', fp.session(0x05).registrations == [bytes.fromhex('01 00 07 7f 00 00 01 00 05 40')])
dev.close()
fp.close()

# 5. The processor goes away: the transport redials and the panel comes back online
fp = FakeProcessor([0x03])
port = fp.port
dev = build({'profile': 'crestron_cip_panel', 'model': 'CCC-110-T', 'name': 'redial',
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': port}]})
dev.transports[0].retry = 0.3
wait(lambda: dev.state['status'] == 'online')
fp.close()
check('5a drop seen', wait(lambda: dev.state['status'] == 'not connected'))
fp = FakeProcessor([0x03], port=port)
check('5b back online after redial', wait(lambda: dev.state['status'] == 'online', 8))
dev.close()
fp.close()

# 6. A bench entry with start_offline never dials until Offline is switched off, and keeps the setting when saved
fp = FakeProcessor([0x03])
dev = build({'profile': 'crestron_cip_panel', 'model': 'CCC-110-T', 'name': 'starts offline', 'start_offline': True,
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
time.sleep(1)
check('6a loaded offline: nothing registers', fp.session(0x03) is None and dev.faults['offline'] is True)
dev.set_fault('offline', False)
check('6b Offline off: it dials and registers', wait(lambda: dev.state['status'] == 'online'))
check('6c the bench keeps start_offline', dev.to_config().get('start_offline') is True)
dev.close()
fp.close()

finish()
