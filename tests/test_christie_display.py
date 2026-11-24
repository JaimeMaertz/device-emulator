"""
Christie Secure Series II profile against the Extron module chri_display_SUHDxx3_x_Series_v1_0_3_0
over a real localhost socket, plus the Technical Reference's example commands and results, and
serial over a pty (skipped on Windows).   Run:  python tests/test_christie_display.py
"""
import os
import time
from harness import check, finish, load_module, start_device, wait_for, RawClient, PROFILES, SerialTransport

CHRI = load_module('chri_display_SUHDxx3_x_Series_v1_0_3_0.py')


def watch(m):
    m.errors, m.discards = [], []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Discard = lambda message, m=m: m.discards.append(message)
    return m


def update(m, command, qualifier=None):
    m.Update(command, qualifier)
    time.sleep(0.15)
    return m.ReadStatus(command, qualifier)


dev, port = start_device('christie_display')
try:
    m = watch(CHRI.EthernetClass('127.0.0.1', port))
    m.Connect()
    check('1 power read while off -> PWR=002, which the module reads as Off',
          update(m, 'Power') == 'Off' and m.ReadStatus('ConnectionStatus') == 'Connected')
    check('2 while off, an input read gets no reply (capture), so the module has no Input',
          update(m, 'Input') is None and not m.errors)
    m.Set('Power', 'On')
    check('3 Set Power On -> On, module reads On', wait_for(lambda: dev.state['PWR'] == 0, 1)
          and update(m, 'Power') == 'On')

    names = ['DisplayPort 1', 'DisplayPort 2', 'HDMI 1', 'HDMI 2', 'HDMI 3', 'HDMI 4', 'OPS/HDMI', 'OPS/DP']
    readback = {}
    for name in names:
        m.Set('Input', name)
        readback[name] = update(m, 'Input')
    check('4 every input in the manual sets and reads back', all(readback[n] == n for n in names) and not m.errors)
    m.Set('Input', 'DVI')
    time.sleep(0.15)
    check('5 FINDING: the module offers DVI (SH6), which the manual doesn\'t have. The display answers '
          'ALL:SH6=N, and the module\'s error pattern has no SH, so the refusal goes unnoticed',
          dev.state['SRC'] == 8 and update(m, 'Input') == 'OPS/DP' and not m.errors
          and any(e[2] == 'tx' and e[3] == b'ALL:SH6=N\r' for e in dev.entries))

    m.Set('Volume', 45)
    m.Set('Mute', 'On')
    m.Set('PictureMode', 'Dynamic')
    m.Set('AudioInput', 'HDMI 3')
    check('6 volume, mute, picture mode and audio input set by the module land and read back',
          wait_for(lambda: (dev.state['VOL'], dev.state['MUT'], dev.state['PMT'], dev.state['AUT']) == (45, 1, 1, 4), 1)
          and (update(m, 'Volume'), update(m, 'Mute'), update(m, 'PictureMode'), update(m, 'AudioInput'))
          == (45, 'On', 'Dynamic', 'HDMI 3'))

    m.Set('PresetMode', '12')
    m.Set('MultiWindowMode', 'Quad')
    m.Set('WindowInput', 'HDMI 4', {'Window': '3'})
    m.Set('WindowMode', 'Window 3')
    check('7 preset, multi-window, window 3 input and window select (WN3, module-only) read back',
          wait_for(lambda: dev.state['WIN'] == 3, 1)
          and (update(m, 'PresetMode'), update(m, 'MultiWindowMode'), update(m, 'WindowInput', {'Window': '3'}))
          == ('12', 'Quad', 'HDMI 4') and not m.errors)
    for key in ('Menu', 'Up', 'Down', 'Left', 'Right', 'Enter'):
        m.Set('MenuNavigation', key)
    time.sleep(0.2)
    check('8 menu navigation keys are acknowledged, no module error', not m.errors)

    dev.panel('VOL', 7)
    dev.panel('SRC', 3)
    check('9 front-panel changes read back through the module',
          (update(m, 'Volume'), update(m, 'Input')) == (7, 'HDMI 2'))
    dev.set_option('space', True)
    check('10 option "Space after ALL:" ("ALL: VOL=007", as some manual examples show): the module still parses',
          update(m, 'Volume') == 7 and any(e[2] == 'tx' and e[3] == b'ALL: VOL=007\r' for e in dev.entries))
    dev.set_option('space', False)
    m.Disconnect()

    # ----- the manual's examples
    raw = RawClient(port)
    check('11 manual: K:ALLSH1. -> ALL:SH1=A', raw.send(b'K:ALLSH1.') == b'ALL:SH1=A\r')
    check('12 manual: K:ALLVOL080. -> ALL:VOL=A, then K:ALLVOL? -> ALL:VOL=080',
          raw.send(b'K:ALLVOL080.') == b'ALL:VOL=A\r' and raw.send(b'K:ALLVOL?') == b'ALL:VOL=080\r')
    check('13 manual: K:ALLWM0. -> ALL:WM0=A; K:ALLRSO. -> ALL:RSO=A; K:ALLMON. -> ALL:MON=A',
          raw.send(b'K:ALLWM0.') == b'ALL:WM0=A\r' and raw.send(b'K:ALLRSO.') == b'ALL:RSO=A\r'
          and raw.send(b'K:ALLMON.') == b'ALL:MON=A\r')
    check('14 value out of range and a command not in the manual answer =N',
          raw.send(b'K:ALLVOL101.') == b'ALL:VOL=N\r' and raw.send(b'K:ALLXYZ.') == b'ALL:XYZ=N\r')
    check('15 commands with CR/LF after them, and two in one packet, each get their reply',
          raw.send(b'K:ALLVLU.\r\nK:ALLVOL?\r\n') == b'ALL:VLU=A\rALL:VOL=081\r')
    check('16 manual: K:ALLPOF. -> ALL:POF=A, then K:ALLPWR? -> ALL:PWR=002',
          raw.send(b'K:ALLPOF.') == b'ALL:POF=A\r' and raw.send(b'K:ALLPWR?') == b'ALL:PWR=002\r')
    check('17 capture: POF while off -> ALL:POF=N; other commands get no reply',
          raw.send(b'K:ALLPOF.') == b'ALL:POF=N\r' and raw.send(b'K:ALLVOL?') == b''
          and raw.send(b'K:ALLSH2.') == b'' and dev.state['SRC'] == 1)
    dev.set_option('off_reply', '=N')
    check('18 option: while off, other commands answer =N', raw.send(b'K:ALLSRC?') == b'ALL:SRC=N\r')
    dev.set_option('pon_reply', 'PWR / FWV / MAC (Display-Emulator capture)')
    check('19 option: PON answers with the captured PWR / FWV / MAC burst',
          raw.send(b'K:ALLPON.') == b'ALL:PWR=000\rALL:FWV=A\rALL:MAC=A\r')
    dev.panel('power_save', True)
    check('20 no signal -> power save: PWR=001', raw.send(b'K:ALLPWR?') == b'ALL:PWR=001\r')
    raw.close()
finally:
    dev.close()


# ----- serial (pty pair)
if not hasattr(os, 'openpty'):
    print('SKIP 21 serial over a pty (no pty on Windows; test a real COM port instead)')
else:
    master, slave = os.openpty()
    dev = PROFILES['christie_display']('serial')
    try:
        dev.add_transport(SerialTransport(dev, os.ttyname(slave), 115200))
        m = watch(CHRI.SerialClass(None, master))
        m.Set('Power', 'On')
        time.sleep(0.2)
        m.Set('Input', 'HDMI 4')
        time.sleep(0.2)
        check('21 over serial at 115200: the module powers on and reads its input',
              update(m, 'Input') == 'HDMI 4' and dev.state['PWR'] == 0)
    finally:
        dev.close()

finish()
