"""
Extron DTP CrossPoint 4K profile: the user guide's example frames over Telnet; both catalog modules
(86/108 v1.10 over SIS-over-SSH, and 82/84 v1.8.7.2) through every
Set/Update example on their comm sheets; verbose notices, passwords, reboot, and serial over a pty
(skipped on Windows).   Run:  python tests/test_extron_dtp_crosspoint.py
"""
import os
import time
from harness import (check, finish, load_module, start_device, add_ssh, wait_for, RawClient,
                     PROFILES, SerialTransport)

DTP86 = load_module('extr_matrix_DTPCrossPoint_86_1084KSeriesv11000.py')
DTP84 = load_module('extr_matrix_DTP_CrossPoint_82_84_4kSeriesv1872.py')
MODEL_86 = 'DTP CrossPoint 86 4K IPCP Q SA'
MODULE_MODEL = 'DTP CrossPoint 86 4K IPCP SA'   # the module doesn't list the Q (Q SA works like the SA)


def watch(m):
    m.errors = []
    m.Error = lambda message, m=m: m.errors.append(message[0])
    m.Discard = lambda message, m=m: m.errors.append('discard: ' + str(message))
    return m


def settle(seconds=0.25):
    time.sleep(seconds)


def read(m, command, qualifier=None, expect=None):
    """Update, then wait for the reply (for `expect`, when given)."""
    m.Update(command, qualifier)
    wait_for(lambda: m.ReadStatus(command, qualifier) is not None if expect is None
             else m.ReadStatus(command, qualifier) == expect, 1.5)
    return m.ReadStatus(command, qualifier)


def handshake(m, timeout=6):
    """Poll the way room code does. The modules take two calls: the first only schedules
    "w0echo" (SSH), the next sends "w3cv"; "Vrb3" makes them Connected and read every tie."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        m.Update('ExecutiveMode')
        if wait_for(lambda: m.ReadStatus('ConnectionStatus') == 'Connected' and not m.VerboseDisabled, 1.2):
            return True
    return False


# Every Set example on the comm sheets whose value reads back with Update, as (command, value,
# qualifier). The model-specific ones (MA amplifier, the 82's A/B outputs) are run on that model.
SETS_86 = [
    ('AmplifierAttenuationSA', -20.0, {'L/R': 'Left'}), ('AmplifierMuteSA', 'On', {'L/R': 'Left'}),
    ('AmplifierPostmixerTrim', 12.0, {'L/R': 'Left'}),
    ('AnalogAttenuation', -5.5, {'Output': '1', 'L/R': 'Left'}), ('AnalogMute', 'On', {'Output': '1', 'L/R': 'Left'}),
    ('AspectRatio', 'Follow', {'Input': '1'}), ('DTPAttenuation', -30.0, {'L/R': 'Left', 'Output': '5'}),
    ('DTPMute', 'On', {'L/R': 'Left', 'Output': '5'}), ('ExecutiveMode', 'Mode 1', None),
    ('ExpansionPremixerGain', 12.0, {'Input': '1'}), ('ExpansionPremixerMute', 'On', {'Input': '1'}),
    ('Freeze', 'On', {'Output': '3'}), ('GroupMicLineInputGain', 80.0, {'Group': '1'}),
    ('GroupMixpoint', 12.0, {'Group': '3'}), ('GroupMute', 'On', {'Group': '17'}),
    ('GroupOutputAttenuation', -10.0, {'Group': '4'}), ('GroupPostmixerTrim', 12.0, {'Group': '5'}),
    ('GroupPrematrixTrim', 12.0, {'Group': '6'}), ('GroupPremixerGain', 12.0, {'Group': '7'}),
    ('HDCPInputAuthorization', 'Off', {'Input': '1'}), ('HDCPOutputAuthorization', 'On', {'Output': '1'}),
    ('HDMIAttenuation', -12.0, {'Output': '1', 'L/R': 'Left'}), ('HDMIMute', 'On', {'Output': '1', 'L/R': 'Left'}),
    ('InputAudioSwitchMode', 'Analog', {'Input': '1'}),
    ('InputGain', 24.0, {'Format': 'Analog', 'Input': '1', 'L/R': 'Left'}),
    ('InputGain', -6.0, {'Format': 'Digital', 'Input': '1', 'L/R': 'Left'}),
    ('InputMute', 'On', {'Input': '1', 'L/R': 'Left'}), ('LogoKeySetting', 'Transparency', {'Output': '3'}),
    ('MicLineGain', 80.0, {'Input': '1'}), ('MicLineMute', 'On', {'Input': '1'}),
    ('MixpointGain', 12.0, {'Input': 'Output 1 Left', 'Output': 'Output 1 Left'}),
    ('MixpointMute', 'On', {'Input': 'Output 1 Left', 'Output': 'Output 1 Left'}),
    ('OutputAudioSelect', 'No Audio', {'Output': '1'}), ('OutputPostmixerTrim', 12.0, {'L/R': 'Left', 'Output': '1'}),
    ('OutputResolution', '640x480 (60Hz)', {'Output': '3'}), ('PhantomPower', 'On', {'Input': '1'}),
    ('PostMatrixGain', 12.0, {'L/R': 'Left', 'Output': '1'}), ('PostMatrixMute', 'On', {'L/R': 'Left', 'Output': '1'}),
    ('PrematrixTrim', 12.0, {'Input': '1', 'L/R': 'Left'}), ('PremixerGain', 12.0, {'Input': '1'}),
    ('PremixerMute', 'On', {'Input': '1'}), ('TestPattern', 'Color Bars', {'Output': '3'}),
    ('VideoMute', 'Video', {'Output': '1'}), ('VirtualReturnGain', -20.0, {'Input': 'A'}),
    ('VirtualReturnMute', 'On', {'Input': 'A'}),
]
SETS_84 = [   # on an 82 IPCP MA 70 (the comm sheet's examples use the 82's outputs)
    ('AmplifierAttenuationMA', -20.0, None), ('AmplifierMuteMA', 'On', None),
    ('AnalogAttenuation', -5.5, {'Output': '1', 'L/R': 'Left'}), ('AspectRatio', 'Follow', {'Input': '1'}),
    ('ExecutiveMode', 'Off', None), ('GroupMute', 'On', {'Group': '20'}), ('HDMIAttenuation', -12.0, {'Output': '1', 'L/R': 'Left'}),
    ('InputGain', 24.0, {'Format': 'Analog', 'Input': '1', 'L/R': 'Left'}), ('MicLineGain', 80.0, {'Input': '1'}),
    ('MixpointMute', 'On', {'Input': 'Output 1 Left', 'Output': 'Output 1 Left'}),
    ('OutputResolution', '640x480 (60Hz)', {'Output': '1'}), ('PhantomPower', 'On', {'Input': '1'}),
    ('VideoMute', 'Video', {'Output': '1A'}), ('VirtualReturnGain', -20.0, {'Input': 'A'}),
]


def sweep(m, sets):
    bad = []
    for command, value, qualifier in sets:
        m.errors.clear()
        m.Set(command, value, qualifier)
        settle(0.1)
        got = read(m, command, qualifier, value)
        if got != value or m.errors:
            bad.append((command, value, got, list(m.errors)))
    return bad


# ----- the 86 over SIS-over-SSH, with the 86/108 module (its SSHClass)
dev, port = start_device('extron_dtp_crosspoint', model=MODEL_86)
ssh_port = add_ssh(dev)
try:
    raw = RawClient(port)
    banner = raw.read_after(0.3)
    check('1 Telnet connect: copyright banner with DTPCP86, the part number, no Password: (no password set)',
          banner.startswith(b'(c) Copyright') and b'DTPCP86' in banner and b'60-1382-92' in banner
          and b'Password' not in banner)

    m = watch(DTP86.SSHClass('127.0.0.1', ssh_port, Credentials=('admin', ''), Model=MODULE_MODEL))
    check('2 SSHClass connects over SSH', m.Connect() == 'Connected')
    check('3 handshake: w0echo -> Echo0 (SSH only), w3cv -> Vrb3, then Connected and every tie read',
          handshake(m)
          and wait_for(lambda: m.ReadStatus('OutputTieStatus', {'Output': '6', 'Tie Type': 'Audio/Video'}) == '0', 3))

    m.Set('MatrixTieCommand', None, {'Input': '1', 'Output': '3', 'Tie Type': 'Audio/Video'})
    check('4 tie 1 -> 3 A/V lands and "Out03 In01 All" sets OutputTieStatus',
          wait_for(lambda: m.ReadStatus('OutputTieStatus', {'Output': '3', 'Tie Type': 'Audio/Video'}) == '1', 2)
          and dev.state['vid3'] == 1 and dev.state['aud3'] == 1)
    m.Set('MatrixTieCommand', None, {'Input': '4', 'Output': '5', 'Tie Type': 'Video'})
    m.Set('MatrixTieCommand', None, {'Input': '2', 'Output': '5', 'Tie Type': 'Audio'})
    check('5 audio breakaway: output 5 video 4 / audio 2',
          wait_for(lambda: m.ReadStatus('OutputTieStatus', {'Output': '5', 'Tie Type': 'Audio'}) == '2', 2)
          and m.ReadStatus('OutputTieStatus', {'Output': '5', 'Tie Type': 'Video'}) == '4')

    dev.panel('vid4', 7)
    check('6 front-panel tie: "Qik" pushed to the verbose-3 session, the module re-reads every tie',
          wait_for(lambda: m.ReadStatus('OutputTieStatus', {'Output': '4', 'Tie Type': 'Video'}) == '7', 3))

    raw.send(b'WG50100*-200AU\r')
    check('7 a change from another connection (virtual return A gain) reaches the module unasked',
          wait_for(lambda: m.ReadStatus('VirtualReturnGain', {'Input': 'A'}) == -20.0, 2))

    dev.panel('vrg1', -15)
    dev.panel('vrm1', True)
    check('7b the virtual returns show on the front panel (vrg / vrm mirror the DSP objects), and a '
          'panel change reaches the module like a front-panel tie does',
          dev.state['vrg0'] == -20.0 and dev.state['vrm1'] is True
          and wait_for(lambda: m.ReadStatus('VirtualReturnGain', {'Input': 'B'}) == -15.0
                       and m.ReadStatus('VirtualReturnMute', {'Input': 'B'}) == 'On', 2))

    dev.panel('mx_row', 22)    # V. Return C
    dev.panel('mx_col', 16)    # V. Send A
    muted_before = dev.state['mx_mute']
    dev.panel('mx_mute', False)
    check('7c a mix-point picked on the front panel: starts muted, unmuting it lands in the DSP, shows in '
          'the row readout and reaches the module',
          muted_before is True and dev.state['mx_mute'] is False and 'V. Send A' in dev._mix_row(22)
          and wait_for(lambda: m.ReadStatus('MixpointMute', {'Input': 'V. Return C', 'Output': 'V. Send A'}) == 'Off', 2))
    raw.send(b'WM22216*1AU\r')
    check('7d a mute sent over SIS follows into the selected mix-point toggle',
          wait_for(lambda: dev.state['mx_mute'] is True, 2) and dev._mix_row(22) == 'none')

    bad = sweep(m, SETS_86)
    check('8 all {} comm-sheet Set examples land and read back through the 86/108 module{}'.format(
        len(SETS_86), '' if not bad else ': ' + repr(bad)), not bad)

    m.errors.clear()
    m.Set('PresetRecall', '5')
    settle()
    check('9 recalling a preset that was never saved: E11, reported by the module',
          m.errors == ['Invalid preset number (out of range)'])
    ties = dict(dev.state['presets'])
    dev.state['preset_slot'] = 5
    dev.panel('preset_save', True)
    dev.panel('vid3', 0)
    settle()
    m.Set('PresetRecall', '5')
    check('10 a saved preset recalls (PrstR05) and the module re-reads the ties',
          wait_for(lambda: m.ReadStatus('OutputTieStatus', {'Output': '3', 'Tie Type': 'Video'}) == '1', 2)
          and dev.state['vid3'] == 1 and not ties)

    m.Set('MatrixIONameCommand', None, {'Type': 'Input', 'Number': '2', 'Name': 'Blu-ray B'})
    settle()
    m.Set('RefreshMatrixIONames', None)
    check('11 input name write + RefreshMatrixIONames: "Nmi2,Blu-ray B" read back',
          wait_for(lambda: m.ReadStatus('MatrixIONameStatus', {'Type': 'Input', 'Number': '2'}) == 'Blu-ray B', 4)
          and dev.state['names_in'][2] == 'Blu-ray B')

    dev.panel('sig1', True)
    got = [read(m, 'InputSignalStatus', {'Input': '1'}, 'Active'), read(m, 'HDCPInputStatus', {'Input': '1'}),
           read(m, 'InputFormat', {'Input': '1'}), read(m, 'HDCPOutputStatus', {'Output': '3A'})]
    check('12 InputSignalStatus ("Frq00 10000000"), HDCP input/output status, input format: {}'.format(got),
          got[0] == 'Active' and None not in got and got[2] != 'No signal detected')
    dev.panel('temp', 45)
    dev.panel('mic1', -42)
    check('13 Temperature 45 (Sts00*...) and mic 1 meter -42.0 dBFS (DsV40000*1*420)',
          read(m, 'Temperature', None, 45) == 45 and read(m, 'MicrophoneSignalStatus', {'Input': '1'}, -42.0) == -42.0)
    m.Disconnect()

    # ----- the user guide's examples over Telnet (raw SIS)
    def sis(data):
        return raw.send(data, 0.15)
    check('14 guide: "1*3!" -> "Out03 In01 All"; "7*5%" -> "Out05 In07 Vid"; "8*4$" -> "Out04 In08 Aud"',
          sis(b'1*3!') == b'Out03 In01 All\r\n' and sis(b'7*5%') == b'Out05 In07 Vid\r\n'
          and sis(b'8*4$') == b'Out04 In08 Aud\r\n')
    check('15 guide: "W+Q3*4!3*3%3*2$|" -> "Qik"; "5*!" -> "In05 All"; "0*3!" unties; "4%" reads "05"',
          sis(b'W+Q3*4!3*3%3*2$|') == b'Qik\r\n' and dev.state['aud2'] == 3
          and sis(b'5*!') == b'In05 All\r\n' and sis(b'0*3!') == b'Out03 In00 All\r\n'
          and sis(b'4%') == b'05\r\n')
    check('16 range errors: input 9 -> E01, output 7 -> E12, unknown command -> E10',
          sis(b'9*1!') == b'E01\r\n' and sis(b'1*7!') == b'E12\r\n' and sis(b'Z') == b'E10\r\n')
    check('17 guide: "WEA7*12EDID" -> "EdidA07*12"; read -> "12"; value 65 (past the 86\'s user slots) -> E13',
          sis(b'\x1bA7*12EDID\r') == b'EdidA07*12\r\n' and sis(b'WA7EDID\r') == b'12\r\n'
          and sis(b'WA7*65EDID\r') == b'E13\r\n')
    check('18 guide DSP: "EG30012*56AU" -> "DsG30012*56", read "56"; "EM60006*1AU"; "EZ40000*1AU"',
          sis(b'WG30012*56AU\r') == b'DsG30012*56\r\n' and sis(b'WG30012AU\r') == b'56\r\n'
          and sis(b'WM60006*1AU\r') == b'DsM60006*1\r\n' and sis(b'WZ40000*1AU\r') == b'DsZ40000*1\r\n')
    check('19 DSP limits: output volume +1.0 dB -> E13; output 8 (not on an 86) -> E14; trims have no mute',
          sis(b'WG60000*10AU\r') == b'E13\r\n' and sis(b'WG50014AU\r') == b'E14\r\n'
          and sis(b'WM30100*1AU\r') == b'E14\r\n')
    check('20 guide groups: "ED2*-293GRPM" -> "GrpmD2*-293"; "ED2*30+GRPM" -> "GrpmD2*-263"; soft limits',
          sis(b'WD2*-293GRPM\r') == b'GrpmD2*-293\r\n' and sis(b'WD2*30+GRPM\r') == b'GrpmD2*-263\r\n'
          and sis(b'WL2*+60*-60GRPM\r') == b'GrpmL2*60*-60\r\n' and sis(b'WD2*100GRPM\r') == b'E13\r\n')
    check('21 video mutes: "1*1B" -> "Vmt1*1"; paired "3*1B" -> E12; "3a*2b" -> "Vmt3A*2"; "1*B" all',
          sis(b'1*1B') == b'Vmt1*1\r\n' and sis(b'3*1B') == b'E12\r\n' and sis(b'3a*2b') == b'Vmt3A*2\r\n'
          and sis(b'1*B') == b'Vmt1\r\n' and set(dev.state['vmute'].values()) == {1})
    check('22 I -> "DTPCP86"; N -> "60-1382-92"; W3CV -> "Vrb3", then S and 0LS are tagged',
          sis(b'I') == b'DTPCP86\r\n' and sis(b'N') == b'60-1382-92\r\n' and sis(b'w3cv\r') == b'Vrb3\r\n'
          and sis(b'S') == b'Sts00*12.250 45.000 750000 750000\r\n' and sis(b'0LS') == b'Frq00 10000000\r\n')
    check('23 echo is SIS-over-SSH only: "W0ECHO" on Telnet -> E14', sis(b'W0ECHO\r') == b'E14\r\n')
    check('24 CR/LF between commands is ignored; commands can run back to back',
          sis(b'\r\n1*1!2*2!\r\n') == b'Out01 In01 All\r\nOut02 In02 All\r\n')

    # ----- verbose notices between connections
    a, b, quiet = RawClient(port), RawClient(port), RawClient(port)
    a.read_after(0.2), b.read_after(0.2), quiet.read_after(0.2)
    a.send(b'W1CV\r', 0.15)
    b.send(b'4*2!', 0.15)
    check('25 verbose 1 (and 3) get a change made on another connection; verbose 0 (Telnet default) does not',
          a.read_after(0.2) == b'Out02 In04 All\r\n' and raw.read_after(0.1) == b'Out02 In04 All\r\n'
          and quiet.read_after(0.1) == b'')
    a.close(), b.close(), quiet.close()

    # ----- passwords
    dev.set_option('admin_password', 'extron')
    dev.set_option('user_password', 'userpw')
    c = RawClient(port)
    greeting = c.read_after(0.3)
    check('26 password set: banner then "Password:"; wrong -> Password: again; admin -> Login Administrator',
          greeting.endswith(b'Password:') and c.send(b'nope\r', 0.2) == b'\r\nPassword:'
          and c.send(b'extron\r', 0.2) == b'\r\nLogin Administrator\r\n' and c.send(b'1*1!', 0.2) == b'Out01 In01 All\r\n')
    c.close()
    c = RawClient(port)
    c.read_after(0.3)
    check('27 user login can tie but not reset (E24)',
          c.send(b'userpw\r', 0.2) == b'\r\nLogin User\r\n' and c.send(b'2*1!', 0.2) == b'Out01 In02 All\r\n'
          and c.send(b'WZXXX\r', 0.2) == b'E24\r\n')
    c.close()
    m = watch(DTP86.SSHClass('127.0.0.1', ssh_port, Credentials=('admin', 'wrong'), Model=MODULE_MODEL))
    bad_login = m.Connect()
    m = watch(DTP86.SSHClass('127.0.0.1', ssh_port, Credentials=('admin', 'extron'), Model=MODULE_MODEL))
    check('28 SSH: a wrong password is refused, the admin password gets in',
          bad_login != 'Connected' and m.Connect() == 'Connected')
    check('29 ...and the handshake completes', handshake(m))
    m.Disconnect()
    dev.set_option('admin_password', '')
    dev.set_option('user_password', '')

    # ----- timeouts, reboot, power
    raw.send(b'W1*1TC\r', 0.15)
    check('30 "W1*1TC" sets the global IP timeout to 10 s', dev.tcp_idle_timeout == 10)
    dev.set_option('reboot_seconds', 2)
    reply = raw.send(b'W1BOOT\r', 0.15)
    dropped = reply.startswith(b'Boot 1') and dev.state['rebooting'] and raw.closed_by_peer(1.5)
    back = wait_for(lambda: not dev.state['rebooting'], 4)
    after = RawClient(port)
    check('31 "W1BOOT" -> "Boot 1", the session drops while it reboots, then a new one gets the banner',
          dropped and back and after.read_after(0.3).startswith(b'(c) Copyright'))
    after.close()
    before = (dev.state['vid1'], dev.state['dsp'][30012]['G'], dev.state['names_in'][2])
    dev.faults['boot'] = 0.2
    dev.power_cycle()
    check('32 power cycle: ties, DSP and names are nonvolatile',
          wait_for(lambda: not dev.booting, 2) and (dev.state['vid1'], dev.state['dsp'][30012]['G'],
                                                    dev.state['names_in'][2]) == before)
    raw.close()
finally:
    dev.close()

# ----- the 82/84 module, on the 82 its comm-sheet examples are written for
dev, port = start_device('extron_dtp_crosspoint', model='DTP CrossPoint 82 4K IPCP MA 70')
try:
    m = watch(DTP84.SerialOverEthernetClass('127.0.0.1', port, Model='DTP CrossPoint 82 4K IPCP MA 70'))
    m.Connect()
    check('33 82/84 module over TCP: Vrb3 handshake, Connected', handshake(m))
    bad = sweep(m, SETS_84)
    check('34 {} comm-sheet Set examples on an 82 MA, incl. the A/B video mute "1A*1B"{}'.format(
        len(SETS_84), '' if not bad else ': ' + repr(bad)), not bad)
    m.Set('MatrixTieCommand', None, {'Input': '7', 'Output': '2', 'Tie Type': 'Audio/Video'})
    check('35 tie 7 -> 2 on the 82', wait_for(lambda: m.ReadStatus(
        'OutputTieStatus', {'Output': '2', 'Tie Type': 'Audio/Video'}) == '7', 2))
    m.Disconnect()
finally:
    dev.close()

# ----- in-process framing (what a harness running profiles without sockets calls): no link context needed
dev = PROFILES['extron_dtp_crosspoint']('sim', MODEL_86)
frames, rest = dev.frames(b'1*3!W3CV\r\nW0*1*1VC|WG50100AU\r3')
check('36 frames() alone splits simple + Escape commands and keeps a partial one',
      frames == [b'1*3!', b'W3CV', b'W0*1*1VC', b'WG50100AU'] and rest == b'3')
dev.close()

# ----- serial (pty pair): verbose 1 by default, and the module's SerialClass skips the echo step
if not hasattr(os, 'openpty'):
    print('SKIP 37 serial over a pty (no pty on Windows; test a real COM port instead)')
else:
    master, slave = os.openpty()
    dev = PROFILES['extron_dtp_crosspoint']('serial', MODEL_86)
    try:
        dev.add_transport(SerialTransport(dev, os.ttyname(slave), 9600))
        m = watch(DTP86.SerialClass(None, master, Model=MODULE_MODEL))
        connected = handshake(m)
        m.Set('MatrixTieCommand', None, {'Input': '2', 'Output': '1', 'Tie Type': 'Video'})
        check('37 serial: SerialClass gets Vrb3 and goes Connected; a tie lands',
              connected and wait_for(lambda: dev.state['vid1'] == 2, 2))
    finally:
        dev.close()

finish()
