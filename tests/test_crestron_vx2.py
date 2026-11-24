"""
The IV-SAM-VX2 profile over a real outgoing CIP transport, against crestron_cip's FakeProcessor driving the
joins the way a room program does (Camera_Preset, then Recall or Save; Toggle_Autoswitch).
    python tests/test_crestron_vx2.py
"""
import time

from harness import check, finish  # noqa: F401  (harness sets the import paths)

from emulator.bench import build  # noqa: E402
from crestron_cip import FakeProcessor  # noqa: E402


def wait(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def pulse(fp, join):
    fp.drive('d', join, 1)
    time.sleep(0.1)
    fp.drive('d', join, 0)
    time.sleep(0.1)


fp = FakeProcessor([0x04])
dev = build({'profile': 'crestron_vx2', 'name': 'VX2 test', 'options': {'talker_seconds': 0},
             'transports': [{'type': 'cip', 'host': '127.0.0.1', 'port': fp.port}]})
cs = lambda: fp.session(0x04)
check('1 registers at 04', wait(lambda: cs() is not None and dev.state['status'] == 'online'))
check('1b reports automatic switching on (Autoswitch_F, digital 4)', wait(lambda: cs().get('d', 4) == 1))

pulse(fp, 6)
check('2 Toggle_Autoswitch (6) turns it off', wait(lambda: dev.state['auto'] is False and cs().get('d', 4) == 0))
pulse(fp, 6)
check('2b and on again', wait(lambda: dev.state['auto'] is True and cs().get('d', 4) == 1))
pulse(fp, 5)
check('2c Stop_Autoswitch (5) turns it off', wait(lambda: dev.state['auto'] is False and cs().get('d', 4) == 0))

fp.drive('a', 23, 3)
pulse(fp, 10)
check('3 Manual_Switch_Camera (10) takes Camera_Select (analog 23)', wait(lambda: dev.state['camera'] == 3))

fp.drive('a', 24, 2)
pulse(fp, 22)
check('4 Save_Camera_Preset (22) stores the live camera in Camera_Preset (analog 24)', wait(lambda: dev.saved == {2: 3}))
dev.panel('camera', 1)
check('4b the camera moved by hand', dev.state['camera'] == 1)
pulse(fp, 21)
check('5 Recall_Camera_Preset (21) brings camera 3 back', wait(lambda: dev.state['camera'] == 3 and dev.state['preset'] == 2))
fp.drive('a', 24, 4)
pulse(fp, 21)
check('5b recalling an empty preset changes nothing', wait(lambda: 'empty' in dev.state['last']) and dev.state['camera'] == 3)

pulse(fp, 4)
check('6 Start_Autoswitch (4) turns it on and clears the preset',
      wait(lambda: dev.state['auto'] is True and dev.state['preset'] == 0 and cs().get('d', 4) == 1))
dev.panel('auto', False)
check('6b switching it off in the software tells the processor', wait(lambda: cs().get('d', 4) == 0))

dev.set_option('talker_seconds', 1)
dev.panel('auto', True)
before = dev.state['camera']
check('7 while automatic, the live camera follows a moving talker', wait(lambda: dev.state['camera'] != before, 3))
dev.close()
fp.close()
finish()
