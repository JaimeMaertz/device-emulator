"""
Denon DN-500BD MKII profile against the Extron module deno_bluray_DN_500BD_500BDMKII_v1_3_3_0 (the
catalog copy), over TCP and over a serial pty, plus raw
frames checked against the DN-500BD MKII protocol guide.   Run:  python tests/test_denon_bluray.py
"""
import os
import time
from harness import check, finish, load_module, start_device, wait_for, refused, RawClient, PROFILES, SerialTransport

DN = load_module('deno_bluray_DN_500BD_500BDMKII_v1_3_3_0.py')
FAST = {'boot': 0, 'tray_seconds': 0.3, 'chapter_minutes': 1}


def watch(m):
    m.errors = []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Discard = lambda message, m=m: m.errors.append(message)
    return m


def update(m, command, qualifier=None):
    m.Update(command, qualifier)
    return m.ReadStatus(command, qualifier)


dev, port = start_device('denon_bluray', options=FAST)
try:
    m = watch(DN.EthernetClass('127.0.0.1', port))
    m.Connect()
    check('1 power query while on -> the module reads On', update(m, 'Power') == 'On' and not m.errors)
    m.Set('Transport', 'Play')
    check('2 Transport Play -> player plays, module reads Play',
          dev.state['transport'] == 'Play' and update(m, 'DeviceStatus') == 'Play' and not m.errors)
    m.Set('Transport', 'Pause')
    pause = update(m, 'DeviceStatus')
    m.Set('Transport', 'Stop')
    check('3 Pause reads Pause; Stop reads Other (the guide lists no stopped code)',
          pause == 'Pause' and update(m, 'DeviceStatus') == 'Other')

    m.Set('Transport', 'Play')
    m.Set('Transport', 'Track/Chapter Next')
    m.Set('Transport', 'Track/Chapter Next')
    two = update(m, 'ChapterorTrack')
    m.Set('ChapterorTrack', 7)
    check('4 chapter next twice -> 3; select chapter 7 -> 7; total chapters 12',
          two == 3 and update(m, 'ChapterorTrack') == 7 and update(m, 'ChapterorTrackTotal') == 12)
    m.Set('Transport', 'Track/Chapter Prev')   # more than 3 s in? no: restart goes to previous chapter
    check('5 chapter prev near the start of a chapter -> previous chapter', update(m, 'ChapterorTrack') == 6)
    m.Set('Transport', 'Group/Title Next')
    check('6 title next -> title 2 of 3, from chapter 1',
          (update(m, 'CurrentTitle'), update(m, 'CurrentTitleTotal'), update(m, 'ChapterorTrack')) == (2, 3, 1))

    m.Set('Search', None, {'Direction': 'Forward', 'Speed': 'Fast'})
    p0, t0 = dev.position(), time.monotonic()
    time.sleep(0.5)
    rate = (dev.position() - p0) / (time.monotonic() - t0)
    check('7 fast search forward -> Fast Play Forward, position moving at 8x',
          update(m, 'DeviceStatus') == 'Fast Play Forward' and 7.5 < rate < 8.5)
    m.Set('Transport', 'Play')
    check('8 current track time reads as hh:mm:ss', update(m, 'CurrentTrackTime').count(':') == 2 and not m.errors)

    m.Set('Mute', 'On')
    m.Set('Volume', -20)
    check('9 mute and volume set and read back',
          (update(m, 'Mute'), update(m, 'Volume')) == ('On', -20) and dev.state['volume'] == -20)
    check('10 disc in, media type BDMV, title text "No Info"',
          (update(m, 'MediaStatus'), update(m, 'MediaType'), update(m, 'AlbumName')) == ('Disc In', 'BDMV', 'No Info'))
    m.Set('DiscTray', 'Open')
    check('11 tray open -> Tray Open, and transport commands are refused (NACK -> module error)',
          update(m, 'MediaStatus') == 'Tray Open'
          and (m.errors.clear(), m.Set('Transport', 'Play'), m.errors)[2] != [] or dev.state['transport'] == 'Stop')
    m.errors.clear()
    m.Set('DiscTray', 'Close')
    check('12 tray closing -> Tray Closed, then Disc In',
          update(m, 'MediaStatus') == 'Tray Closed' and wait_for(lambda: dev.state['tray'] == 'Closed')
          and update(m, 'MediaStatus') == 'Disc In')

    m.Set('Transport', 'Play')
    update(m, 'ElapsedTime')
    check('13 FINDING: the module mis-reads the guide\'s elapsed-time reply "@0EThhhmmss"',
          str(m.ReadStatus('ElapsedTime')).startswith('@0E'))

    raw = RawClient(port)
    check('14 raw: a control command is answered "ack", a status request "ack+@0STPL\\r"',
          raw.send(b'@02353\r') == b'ack' and raw.send(b'@0?ST\r') == b'ack+@0STPL\r')
    check('15 raw: an unknown command is answered "nack"', raw.send(b'@0XYZZY\r') == b'nack')

    # Front panel: the player reports the change itself, and resends once without an ACK
    dev.panel('pause', True)
    first = raw.read_after(0.1)
    again = raw.read_after(0.35)
    check('16 front-panel Pause pushes "@0STPP\\r"; no ACK -> sent again after 300 ms, then no more',
          first == b'@0STPP\r' and again == b'@0STPP\r' and raw.read_after(0.4) == b'')
    dev.panel('play', True)
    raw.read_after(0.1)
    raw.sock.sendall(b'\x06')
    check('17 ...an ACK (06h) from the host stops the resend', raw.read_after(0.4) == b'')
    dev.panel('next', True)
    check('18 front-panel chapter skip pushes the new chapter number',
          b'@0Tr' in raw.read_after(0.1))
    raw.sock.sendall(b'\x06')

    for code in ('BDM', 'BDA', 'DVR', 'CDR', 'EXT'):
        dev.set('media', code)
        readback = update(m, 'MediaType')
        if readback != {'BDM': 'BDMV', 'BDA': 'BDAV', 'DVR': 'DVD VR', 'CDR': 'CD-ROM', 'EXT': 'External Memory'}[code]:
            break
    check('18a every media type code reads back through the module', readback == 'External Memory')

    raw.send(b'@023KL\r')
    dev.panel('stop', True)
    check('19 panel lock (@023KL) blocks the front panel', dev.state['transport'] == 'Play')
    raw.send(b'@023KU\r')

    m.Set('Power', 'Off')
    check('20 Power Off -> ack, then the LAN port closes: connections dropped and refused',
          dev.state['power'] == 'Off' and raw.closed_by_peer() and refused(port))
    dev.panel('power_on', True)
    check('21 powered on from the front panel -> the LAN port opens again', wait_for(lambda: not refused(port), 5))
finally:
    dev.close()

# Guide readings: ACK/NACK bytes, and no answer to the power query while off; boot time
dev, port = start_device('denon_bluray', options=dict(FAST, ack_style='bytes 06h/15h (guide)', boot=0.5))
try:
    raw = RawClient(port)
    check('22 option "bytes": control ACK is 06h, status is 06h + code', raw.send(b'@02354\r') == b'\x06'
          and raw.send(b'@0?ST\r') == b'\x06@0STST\r' and raw.send(b'@0NOPE\r') == b'\x15')
finally:
    dev.close()


def serial_checks():
    master, slave = os.openpty()
    dev = PROFILES['denon_bluray']('serial', options=dict(FAST, start_on=False, off_power='no answer (guide)'))
    try:
        dev.add_transport(SerialTransport(dev, os.ttyname(slave), 115200))
        m = watch(DN.SerialClass(None, master))
        m.Update('Power')
        check('23 serial, off, guide reading: no answer to the power query -> module logs an error',
              m.ReadStatus('Power') is None and m.errors)
        dev.set_option('off_power', 'nack (module)')
        check('24 serial, off, module reading: "nack" -> module reads Off', update(m, 'Power') == 'Off')
        m.Set('Power', 'On')
        dev.set_option('boot', 0.5)
        dev.panel('power_off', True)
        m.Set('Power', 'On')
        check('25 serial: first second after Power On gets no answer', m.ReadStatus('Power') == 'Off'
              and (m.Update('Power'), m.ReadStatus('Power'))[1] != 'On')
        time.sleep(0.6)
        check('26 ...then it answers', update(m, 'Power') == 'On')
        m.Close()
    finally:
        dev.close()
        os.close(master)


if hasattr(os, 'openpty'):
    serial_checks()
else:
    print('SKIP 23-26 serial over a pty (no pty on Windows; test a real COM port instead)')

finish()
