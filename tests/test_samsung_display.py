"""
Samsung MDC profile against two real clients over real localhost sockets: the Extron module
smsg_display_QB_QM_QE_LH_Series_v1_2_1_0, and the samsung-mdc Python library
(pip install samsung-mdc; it checks every reply's ID, length, checksum and field layout).
Raw frames are checked against the MDC Protocol v15.0 spec, and the module runs over serial too
(a pty pair; skipped on Windows).   Run:  python tests/test_samsung_display.py
"""
import asyncio
import os
import sys
import time
from harness import check, finish, load_module, start_device, wait_for, refused, RawClient, \
    PROFILES, SerialTransport
from emulator.profiles.samsung_display import checksum

try:  # the samsung-mdc library (pip install samsung-mdc): checks 22-25 run when it's installed
    from samsung_mdc import MDC
    from samsung_mdc.exceptions import NAKError, MDCTLSRequired, MDCError
except ImportError:
    MDC = None

SMSG = load_module('smsg_display_QB_QM_QE_LH_Series_v1_2_1_0.py')
KEEP = {'reconnect': 0}   # most checks keep one connection through power on


def connect(port, model='QM55N', device_id='0'):
    m = SMSG.EthernetClass('127.0.0.1', port, Model=model)
    m.errors, m.discards = [], []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Discard = lambda message, m=m: m.discards.append(message)
    m.DeviceID = device_id
    m.Connect()
    return m


def update(m, command, qualifier=None):
    m.Update(command, qualifier)
    time.sleep(0.15)
    return m.ReadStatus(command, qualifier)


def frame(cmd, display_id, *data):
    """A controller -> display frame, as the spec builds it."""
    body = bytes([cmd, display_id, len(data)] + list(data))
    return bytes([0xAA]) + body + bytes([checksum(body)])


def run(coro):
    return asyncio.run(coro)


async def with_mdc(port, fn, **kwargs):
    async with MDC('127.0.0.1:{}'.format(port), timeout=2, **kwargs) as mdc:
        return await fn(mdc)


# ----- the Extron module
dev, port = start_device('samsung_display', model='QM series (QMN)', options=KEEP)
try:
    m = connect(port)
    check('1 power read while off -> Off, and the module counts the display as Connected',
          update(m, 'Power') == 'Off' and m.ReadStatus('ConnectionStatus') == 'Connected')
    update(m, 'Input')
    check('2 an input read while off gets a NAK, which the module reports as an Input error',
          any('Input' in e for e in m.errors))
    m.errors.clear()
    m.Set('Power', 'On')
    check('3 Set Power On -> On, module reads On', wait_for(lambda: dev.state['power'] == 'On', 1)
          and update(m, 'Power') == 'On')

    names = ['DVI', 'MagicInfo', 'HDMI 1', 'HDMI 2', 'DisplayPort', 'Screen Mirroring', 'Remote Workspace',
             'Web Browser', 'MagicInfo S']
    readback = {}
    for name in names:
        m.Set('Input', name)
        readback[name] = update(m, 'Input')
    check('4 every input the module sets reads back', not m.errors and all(readback[n] == n for n in names))
    dev.panel('pc_signal', True)
    m.Set('Input', 'HDMI 1')
    check('5 with a PC signal on HDMI the display reports the get-only HDMI1_PC code (22)',
          update(m, 'Input') == 'HDMI 1 PC')
    m.Set('Input', 'HDMI 2')
    check('5a FINDING: the module never reads HDMI2_PC (24): its reply regex has the byte unescaped, '
          'and 0x24 is "$", the end anchor. The reply is right; the module keeps the Set\'s HDMI 2',
          update(m, 'Input') == 'HDMI 2'
          and any(e[2] == 'tx' and e[3] == bytes.fromhex('AA FF 00 03 41 14 24 7B') for e in dev.entries))
    dev.panel('pc_signal', False)

    m.Set('Volume', 37)
    m.Set('Mute', 'On')
    check('6 volume + mute set by the module land and read back',
          wait_for(lambda: (dev.state['volume'], dev.state['mute']) == (37, True), 1)
          and (update(m, 'Volume'), update(m, 'Mute')) == (37, 'On'))

    dev.panel('volume', 5)
    check('7 a front-panel change reads back through the module', update(m, 'Volume') == 5)
    m.Set('PanelLock', 'On')
    time.sleep(0.15)
    dev.panel('volume', 60)
    check('8 panel lock set by the module: front-panel changes are refused, the lock reads back',
          dev.state['volume'] == 5 and update(m, 'PanelLock') == 'On')
    for lock in ('PanelLock', 'SafetyLock', 'AllKeysLock'):
        m.Set(lock, 'On')
        time.sleep(0.1)
        m.Set(lock, 'Off')
    check('9 safety lock and all keys lock set and clear too',
          wait_for(lambda: not (dev.state['safety_lock'] or dev.state['all_keys_lock']), 1)
          and update(m, 'AllKeysLock') == 'Off' and not m.errors)

    update(m, 'VideoWallMode')
    check('10 video wall mode while the wall is off gets a NAK (spec: works only with the wall on)',
          any('VideoWallMode' in e for e in m.errors))
    m.errors.clear()
    m.Set('VideoWall', 'On')
    m.Set('VideoWallMode', 'Natural')
    m.Set('VideoWallSize', '4', {'Row': '2', 'Column': '3'})
    time.sleep(0.2)
    check('11 video wall on, mode, and a 2 x 3 layout (display 4) set and read back',
          (update(m, 'VideoWall'), update(m, 'VideoWallMode')) == ('On', 'Natural')
          and update(m, 'VideoWallSize', {'Row': '2', 'Column': '3'}) == '4'
          and dev.state['wall_div'] == 0x23 and not m.errors)
    m.Set('VideoWall', 'Off')

    m.Set('MagicInfoServerSettingCommand', 'http://10.0.0.5:7001')
    time.sleep(0.2)
    check('12 FINDING: the module frames MagicInfoServerSettingCommand with a length one short (the '
          '82 sub-command isn\'t counted), so the display sees a bad checksum and never answers',
          any(e[2] == 'info' and 'checksum error' in str(e[3]) for e in dev.entries))
    m.Disconnect()

    m = connect(port, device_id='7')
    update(m, 'Power')
    check('13 a module set to another Display ID gets no reply', m.ReadStatus('Power') is None)
    m.Disconnect()
    dev.set_option('display_id', 7)
    m = connect(port, device_id='7')
    check('14 ...and does once the display has that ID', update(m, 'Power') == 'On')
    m.Disconnect()
    dev.set_option('display_id', 0)

    m = connect(port, device_id='Broadcast')
    m.Set('Volume', 22)
    m.Update('Volume')
    check('15 broadcast: the display acts without replying; the module refuses status queries',
          wait_for(lambda: dev.state['volume'] == 22, 1) and m.discards and m.ReadStatus('Volume') is None)
    m.Disconnect()

    # ----- raw frames against the spec
    raw = RawClient(port)
    check('16 spec example "AA 11 FE 01 01 11" (power on, broadcast): acts, no ACK',
          raw.send(bytes.fromhex('AA 11 FE 01 00 10')) == b'' and dev.state['power'] == 'Off'
          and raw.send(bytes.fromhex('AA 11 FE 01 01 11')) == b'' and dev.state['power'] == 'On')
    check('17 power read reply: AA FF 00 03 41 11 01 55',
          raw.send(frame(0x11, 0)) == bytes.fromhex('AA FF 00 03 41 11 01 55'))
    check('18 a bad checksum, or another ID, gets no reply',
          raw.send(frame(0x11, 0)[:-1] + b'\x00') == b'' and raw.send(frame(0x11, 3)) == b'')
    dev.set_option('broadcast', 'act and reply')
    check('19 option "act and reply": broadcast answers with ID FE',
          raw.send(frame(0x12, 0xFE)) == bytes.fromhex('AA FF FE 03 41 12 16') + bytes([checksum(bytes.fromhex('FF FE 03 41 12 16'))]))
    dev.set_option('broadcast', 'act, no reply (spec)')
    status = raw.send(frame(0x00, 0))
    check('20 status 00: length 9, then power, volume, mute, input, picture size, 0, 0',
          status[3] == 9 and status[4:6] == b'A\x00' and list(status[6:13]) == [1, 22, 1, 0x23, 0x01, 0, 0])
    check('21 junk before a frame is skipped', raw.send(b'\x00\x13' + frame(0x13, 0))[:7]
          == bytes.fromhex('AA FF 00 03 41 13 01'))
    raw.close()
finally:
    dev.close()


# ----- the samsung-mdc library
if MDC is None:
    print('SKIP 22-25: pip install samsung-mdc to run them')
else:
    dev, port = start_device('samsung_display', model='QM series (QMN)', options=KEEP)
    try:
        async def identity(mdc):
            return (await mdc.status(0), await mdc.serial_number(0), await mdc.software_version(0),
                    await mdc.model_name(0), await mdc.model_number(0), await mdc.error_status(0))
        status, serial, version, name, number, errors = run(with_mdc(port, identity))
        check('22 samsung-mdc parses status, serial, version, model name/number and error status while off',
              status[0] == MDC.power.POWER_STATE.OFF and serial == ('EMU000000000001',)
              and name == ('QM55N',) and number[0].name == 'LCD' and errors[3].name == 'NORMAL')

        async def power_up(mdc):
            await mdc.power(0, ['ON'])
            await mdc.input_source(0, ['DISPLAY_PORT_1'])
            await mdc.virtual_remote(0, ['KEY_VOLUME_UP'])
            await mdc.video_wall_state(0, ['ON'])
            await mdc.video_wall_model(0, ['2,3', 5])   # the library's pack() only takes the string form
            return await mdc.status(0), await mdc.video_wall_model(0)
        status, wall = run(with_mdc(port, power_up))
        check('23 power on, DisplayPort, remote volume up, video wall (2,3) display 5 through samsung-mdc',
              status[0].name == 'ON' and status[3].name == 'DISPLAY_PORT_1' and status[1] == 11
              and wall == ((2, 3), 5) and dev.state['wall_div'] == 0x32)

        async def refused_aspect(mdc):
            try:
                await mdc.picture_aspect(0, ['VIDEO_16_9'])
            except NAKError:
                return True
            return False
        check('24 picture size while the video wall is on: NAK, raised by samsung-mdc as NAKError',
              run(with_mdc(port, refused_aspect)))

        dev.set_option('secured', True)

        async def plain(mdc):
            try:
                await mdc.power(0)
            except MDCTLSRequired:
                return True
            return False
        check('25 Secured Protocol: the TLS greeting, which samsung-mdc reports as MDCTLSRequired',
              run(with_mdc(port, plain)))
        dev.set_option('secured', False)

        dev.set_option('reconnect', 4)   # longer than Windows takes to report a refused connect (~2 s)

        async def off_then_on(mdc):
            await mdc.power(0, ['OFF'])
            await asyncio.sleep(0.3)
            ack = await mdc.power(0, ['ON'])
            await asyncio.sleep(0.5)
            try:
                await mdc.power(0)
            except (MDCError, ConnectionError, OSError):
                return ack, True
            return ack, False
        ack, dropped = run(with_mdc(port, off_then_on))
        check('26 LAN power on: ACK, then the connection drops and the port refuses (spec: re-connect after 10 s)',
              ack == (MDC.power.POWER_STATE.ON,) and dropped and refused(port))
        check('27 ...and it listens again after the reconnect time', wait_for(lambda: not refused(port), 6))

        async def standby_off(mdc):
            await mdc.network_standby(0, ['OFF'])
            await mdc.power(0, ['OFF'])
        run(with_mdc(port, standby_off))
        check('28 Network Standby Off + power off: the LAN port closes (spec: power on needs WOL then)',
              wait_for(lambda: refused(port), 3))
        dev.panel('power_on', True)
        check('29 the remote powers it on and the port opens again', wait_for(lambda: not refused(port), 3))

        dev.set_option('reboot_seconds', 4)
        raw = RawClient(port)
        check('30 power 02 (reboot): ACK 02, the port closes, and it comes back On',
              raw.send(frame(0x11, 0, 2))[5:7] == b'\x11\x02' and wait_for(lambda: refused(port), 3)
              and wait_for(lambda: dev.state['power'] == 'On' and not refused(port), 8))
    finally:
        dev.close()


# ----- serial (pty pair)
if not hasattr(os, 'openpty'):
    print('SKIP 31 serial over a pty (no pty on Windows; test a real COM port instead)')
else:
    master, slave = os.openpty()
    dev = PROFILES['samsung_display']('serial', 'QB / QE series (QBN, QEN)', KEEP)
    try:
        dev.add_transport(SerialTransport(dev, os.ttyname(slave), 9600))
        m = SMSG.SerialClass(None, master, Model='QB55N')
        m.errors = []
        m.Error = lambda message: m.errors.append(message[0])
        m.Set('Power', 'On')
        time.sleep(0.2)
        m.Set('Input', 'HDMI 2')
        time.sleep(0.2)
        check('31 over serial: the module powers on a QB and reads its input (no DisplayPort on QB)',
              update(m, 'Input') == 'HDMI 2' and dev.state['power'] == 'On'
              and 'DisplayPort' not in dev.inputs.values())
    finally:
        dev.close()

finish()
