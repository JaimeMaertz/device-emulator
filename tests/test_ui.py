"""
The web UI, driven through NiceGUI's simulated user: the real page and its real event handlers, no
browser. Devices it creates listen on real localhost ports, so each step is checked from the
network side too. NiceGUI's simulation needs pytest (a dev-only install: pip install pytest pytest-asyncio).
    Run:  python -m pytest tests/test_ui.py -s
"""
import asyncio
import os

import pytest

from harness import check, free_port, results, RawClient

from nicegui import ui
from nicegui.testing.user_simulation import user_simulation

from emulator import bench
from emulator import ui as emu_ui


async def async_wait(predicate, timeout=2.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def settle():
    await asyncio.sleep(0.1)


async def click(user, mark):
    user.find(marker=mark).click()
    await settle()


def element(user, mark):
    return next(iter(user.find(marker=mark).elements))


@pytest.mark.asyncio
async def test_ui():
    async with user_simulation(root=emu_ui.root) as user:
        await user.open('/')
        await user.should_see('No devices yet')
        check('1 empty page renders', True)

        # Add an LG display on a free port through the dialog
        port = free_port()
        await click(user, 'add_device')
        await user.should_see(marker='add_profile')
        element(user, 'add_profile').value = 'lg_display'
        await settle()
        await asyncio.sleep(0.05)
        check('2 picking a profile fills model, name and the profile default port',
              element(user, 'add_model').value == 'UH5J / UH7J series (98UH5J-H)'
              and element(user, 'add_name').value == 'LG signage display 1'
              and element(user, 'add_port').value == 9761)
        element(user, 'add_name').value = 'LG 1'
        await settle()
        element(user, 'add_port').value = port
        await settle()
        await click(user, 'add_create')
        await user.should_see(marker='LG_1.power')
        lg = emu_ui.DEVICES[0]
        raw = RawClient(port)
        check('3 the device card appears and the device answers on its port',
              raw.send(b'ka 01 FF\r') == b'a 01 OK00x')

        await click(user, 'LG_1.key_on')
        check('4 remote MONITOR ON button -> the processor reads power On', raw.send(b'ka 01 FF\r') == b'a 01 OK01x')
        await click(user, 'LG_1.audioMute')
        check('5 mute switch -> processor reads muted (ke 00)', raw.send(b'ke 01 FF\r') == b'e 01 OK00x')
        element(user, 'LG_1.input').value = 'HDMI2 (DTV)'
        await settle()
        check('6 input select -> processor reads 91', raw.send(b'xb 01 FF\r') == b'b 01 OK91x')

        raw.send(b'kf 01 20\r')
        check('7 volume set by the processor shows on the UI slider',
              await async_wait(lambda: element(user, 'LG_1.volume').value == 0x20))
        await user.should_see('Power: On')

        # A projector, to check a refused front-panel change snaps back
        pport = free_port()
        await click(user, 'add_device')
        await user.should_see(marker='add_profile')
        element(user, 'add_profile').value = 'nec_projector'
        await settle()
        await asyncio.sleep(0.05)
        element(user, 'add_name').value = 'Proj'
        await settle()
        element(user, 'add_port').value = pport
        await settle()
        await click(user, 'add_create')
        await user.should_see(marker='Proj.power')
        proj = emu_ui.DEVICES[1]
        await click(user, 'Proj.videoMute')
        await asyncio.sleep(0.1)
        check('8 picture mute while the projector is off is refused and the switch snaps back',
              proj.state['videoMute'] is False and element(user, 'Proj.videoMute').value is False)

        await click(user, 'LG_1.fault_silent')
        check('9 Silent fault switch -> no reply', raw.send(b'ka 01 FF\r') == b'')
        await click(user, 'LG_1.fault_silent')

        element(user, 'bench_name').value = 'ui-test'

        await settle()
        await click(user, 'bench_save')
        path = bench.bench_path('ui-test')
        saved = bench.load("ui-test")
        check('10 Save writes the bench: both devices with their ports',
              [(d['profile'], d['transports'][0]['port']) for d in saved] == [('lg_display', port), ('nec_projector', pport)])

        os.utime(path, (0, 0))
        await click(user, 'bench_save')
        asked = user.find(marker='bench_overwrite') is not None and os.path.getmtime(path) == 0
        await click(user, 'bench_overwrite')
        check('10a Save over an existing bench asks first, and writes only on Overwrite',
              asked and os.path.getmtime(path) > 0)

        raw.close()
        await click(user, 'bench_load')
        await async_wait(lambda: emu_ui.DEVICES and emu_ui.DEVICES[0] is not lg)
        await user.should_see(marker='LG_1.power')
        raw = RawClient(port)
        check('11 Load rebuilds the bench: fresh devices on the same ports',
              len(emu_ui.DEVICES) == 2 and emu_ui.DEVICES[0] is not lg and raw.send(b'ka 01 FF\r') == b'a 01 OK00x')
        raw.close()

        await click(user, 'LG_1.remove')
        await user.should_not_see(marker='LG_1.power')
        check('12 Remove closes the device and frees its port',
              len(emu_ui.DEVICES) == 1 and _refused(port))
        emu_ui.clear_devices()
        os.remove(path)
    print('\n{ok} passed, {fail} failed'.format(**results))
    assert results['fail'] == 0


def _refused(port):
    try:
        RawClient(port)
    except OSError:
        return True
    return False

