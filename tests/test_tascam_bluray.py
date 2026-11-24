"""
TASCAM BD-MP1MKII profile against the Extron module tasc_bluray_BD_MP1_v1_2_0_0, over TCP and over a serial pty, plus raw frames checked
against the replies measured on a real BD-MP1MKII.   Run:  python tests/test_tascam_bluray.py
"""
import os
import time
from harness import check, finish, load_module, start_device, wait_for, refused, RawClient, PROFILES, SerialTransport

TM = load_module('tasc_bluray_BD_MP1_v1_2_0_0.py')
FAST = {'tray_seconds': 0.3, 'chapter_minutes': 1, 'pacing': 0.3}
PACE = 0.35   # the module doesn't pace; the comm sheet asks for 0.3 s between commands


def setp(m, *args):
    m.Set(*args)
    time.sleep(PACE)


dev, port = start_device('tascam_bluray', options=FAST)
try:
    m = TM.EthernetClass('127.0.0.1', port, Model='BD-MP1')
    m.Connect()
    setp(m, 'Transport', 'Play')
    check('1 Transport Play -> the player plays', dev.state['transport'] == 'Play')
    setp(m, 'Transport', 'Pause')
    paused = dev.state['transport']
    setp(m, 'Transport', 'Stop')
    check('2 Pause, then Stop', paused == 'Pause' and dev.state['transport'] == 'Stop')
    setp(m, 'Transport', 'Play')
    setp(m, 'Transport', 'Chapter Jump Next')
    setp(m, 'Transport', 'Chapter Jump Next')
    check('3 chapter next twice -> chapter 3', dev.chapter() == 3)
    setp(m, 'Transport', 'Chapter Jump Previous')
    check('4 chapter previous at the start of a chapter -> chapter 2', dev.chapter() == 2)
    setp(m, 'Transport', 'Title Jump Next')
    check('5 title next -> title 2, from chapter 1', (dev.state['title'], dev.chapter()) == (2, 1))
    setp(m, 'Scan', 'Forward', {'Speed': 'Fast'})
    check('6 Scan Forward Fast -> fast forward', dev.state['transport'] == 'Fast Forward')
    for key in ('Home', 'Up', 'Down', 'Left', 'Right', 'Enter', 'Return'):
        setp(m, 'MenuControl', key)
    check('7 menu keys are acknowledged; the last was Return', dev.state['lastMenu'] == 'RET')
    setp(m, 'Mute', 'On')
    check('8 Mute On', dev.state['mute'] is True)
    m.Set('MenuControl', 'Up')
    m.Set('MenuControl', 'Down')
    time.sleep(PACE)
    check('9 two commands with no gap: the second is ignored (the pacing the comm sheet asks for)',
          dev.state['lastMenu'] == 'OSD3' and dev.state['ignored'] == 1)

    raw = RawClient(port)
    time.sleep(PACE)
    check('10 raw: a known command is answered "ack" with no CR (measured)', raw.send(b'!7HOM\r') == b'ack')
    time.sleep(PACE)
    check('11 raw: an unknown code is answered "nack" (measured)', raw.send(b'!7XYZ\r') == b'nack')
    time.sleep(PACE)
    check('12 raw: a Denon "@0" code is answered "nack" (measured)', raw.send(b'@0PCHM\r') == b'nack')
    time.sleep(PACE)
    raw.send(b'!7OPCOP\r')
    time.sleep(PACE)
    check('13 raw: with the tray open, Play still answers "ack" and nothing plays (measured: Play with no disc)',
          raw.send(b'!7PLY\r') == b'ack' and dev.state['transport'] == 'Stop')
    time.sleep(PACE)
    raw.send(b'!7OPCCL\r')
    check('14 tray close -> Closed after its travel time', wait_for(lambda: dev.state['tray'] == 'Closed'))

    time.sleep(PACE)
    m.Set('Power', 'Off')
    check('15 Power Off -> the LAN port closes (comm sheet)',
          wait_for(lambda: dev.state['power'] == 'Off') and raw.closed_by_peer() and refused(port))
    dev.panel('power_on', True)
    check('16 powered on from the front panel -> the LAN port opens again', wait_for(lambda: not refused(port), 5))
finally:
    dev.close()


def serial_checks():
    master, slave = os.openpty()
    dev = PROFILES['tascam_bluray']('serial', options=dict(FAST, start_on=False))
    try:
        dev.add_transport(SerialTransport(dev, os.ttyname(slave), 115200))
        m = TM.SerialClass(None, master, Model='BD-MP1')
        setp(m, 'Transport', 'Play')
        check('17 serial, off: Play does nothing', dev.state['power'] == 'Off' and dev.state['transport'] == 'Stop')
        setp(m, 'Power', 'On')
        check('18 serial: Power On turns it on', wait_for(lambda: dev.state['power'] == 'On'))
        setp(m, 'Transport', 'Play')
        check('19 serial: then Play plays', wait_for(lambda: dev.state['transport'] == 'Play'))
        m.Close()
    finally:
        dev.close()
        os.close(master)


if hasattr(os, 'openpty'):
    serial_checks()
else:
    print('SKIP 17-19 serial over a pty (no pty on Windows; test a real COM port instead)')

finish()
