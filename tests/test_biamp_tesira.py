"""
Biamp Tesira profile: the block library against the reference tables, the Tesira help's own TTP
examples over Telnet (with the option negotiation), the Extron catalog module over SSH with no
password, the Room Combiner's documented rules, subscriptions (lost on a new configuration), and
a bench round trip.   Run:  python tests/test_biamp_tesira.py
"""
import os
import re
import time
from harness import (check, finish, load_module, start_device, add_ssh, wait_for, RawClient)
from emulator import bench
from emulator.tesira_blocks import BLOCK_TYPES

TESIRA = load_module('biam_dsp_TesiraSeries_v1_18_3_0.py')
# A1 compares each block type's table with a copy of Biamp's attribute tables, when one is given.
TABLES = os.environ.get('TESIRA_ATTRIBUTE_TABLES', '')

# Instance tags and sizes as the help's examples use them
DESIGN = {
    'Level1': {'block': 'Level Control', 'channels': 4},
    'MyLevel1': {'block': 'Level Control', 'channels': 8},
    'my level 2': {'block': 'Level Control', 'channels': 2, 'values': {'level 1': -10}},
    '123': {'block': 'Level Control', 'channels': 1, 'values': {'level 1': -10}},
    'Mute1': {'block': 'Mute Control', 'channels': 3},
    'Mixer1': {'block': 'Standard Mixer', 'inputs': 4, 'outputs': 2},
    'Matrix1': {'block': 'Matrix Mixer', 'inputs': 4, 'outputs': 4},
    'Input1': {'block': 'Audio Input', 'channels': 2, 'values': {'gain 1': 24}},
    'Output1': {'block': 'Audio Output', 'channels': 2},
    'AudioMeter2': {'block': 'Audio Meter', 'channels': 3},
    'Router1': {'block': 'Router', 'inputs': 4, 'outputs': 2},
    'SourceSelector1': {'block': 'Source Selector', 'sources': 4},
    'PresetButton1': {'block': 'Preset Control', 'channels': 2},
    'RoomCombiner1': {'block': 'Room Combiner', 'rooms': 3, 'walls': 2},
    'AGC1': {'block': 'AGC'},
}
# A small room's design: program level and mute, mic mutes, a three-room combiner
ROOM = {
    'ProgramLevel': {'block': 'Level Control', 'channels': 3},
    'ProgramMute': {'block': 'Mute Control', 'channels': 3},
    'MicMute': {'block': 'Mute Control', 'channels': 7},
    'RoomCombiner': {'block': 'Room Combiner', 'rooms': 3, 'walls': [[1, 2], [2, 3]]},
}


def lines(reply):
    return [l for l in reply.decode('latin-1').split('\r\n') if l]


def ttp(client, command, wait=0.15):
    return lines(client.send(command.encode() + b'\n', wait))


# ----- A. every block type is its reference table, row for row
text = open(TABLES, encoding='utf-8').read() if os.path.isfile(TABLES) else ''
sections = {m.group(1).strip().lower(): m.start() for m in re.finditer(r'^### (.+)$', text, re.M)}
starts = sorted(sections.values()) + [len(text)]


def section(name):
    """The reference's section for a block type ('Level Control Block' for 'level control')."""
    for title, start in sections.items():
        if (title[:-6] if title.endswith(' block') else title) == name:
            return text[start:starts[starts.index(start) + 1]]
    return None


missing = []
for name, btype in sorted(BLOCK_TYPES.items()) if text else []:
    body = section(name)
    if body is None:
        missing.append(name + ': no section in the reference')
        continue
    reference = set(l.strip() for l in body.splitlines())
    for row in btype.table.strip().splitlines():
        if row.strip() not in reference:
            missing.append('{}: {}'.format(name, row.strip()))
if text:
    check('A1 {} block types: every table row is verbatim in its reference section ({} not{})'.format(
        len(BLOCK_TYPES), len(missing), ': ' + '; '.join(missing[:4]) if missing else ''), not missing)
else:
    print('SKIP A1: set TESIRA_ATTRIBUTE_TABLES to a copy of the attribute tables to compare them')
opaque = sorted('{}.{}'.format(n, c) for n, t in BLOCK_TYPES.items() for c, a in t.attrs.items() if a.kind == 'opaque')
check('A2 every attribute has a value kind (opaque: {})'.format(opaque or 'none'), not opaque)

# ----- B. the help's examples over Telnet
dev, port = start_device('biamp_tesira', 'Tesira', design=DESIGN)

c = RawClient(port)
offer = c.read_after(0.3)
asked = re.findall(rb'\xff([\xfb\xfd])(.)', offer, re.S)
check('B1 Telnet: the server opens with the documented 14 option requests', len(asked) == 14)
check('B2 nothing works before the negotiation is answered', ttp(c, 'Level1 get level 1') == [])
answer = b''.join(b'\xff' + (b'\xfe' if cmd == b'\xfb' else b'\xfc') + opt for cmd, opt in asked)
banner = c.send(answer, 0.3)
check('B3 refusing every option gets the banner', b'Welcome to the Tesira Text Protocol Server' in banner)

EXAMPLES = [   # (command, the lines expected after the echo) from the TTP pages and attribute tables
    ('Level1 get level 1', ['+OK "value":0.000000']),
    ('"my level 2" get level 1', ['+OK "value":-10.000000']),
    ('my level 2 get level 1', ['-ERR address not found: {"deviceId":0 "classCode":0 "instanceNum":0}']),
    ('123 get level 1', ['+OK "value":-10.000000']),
    ('Session get aliases', ['-ERR address not found: {"deviceId":0 "classCode":0 "instanceNum":0}']),
    ('SESSION Get aliases', ['-ERR Parse error at 8: verb was not one of the commands supported by Services']),
    ('SESSION get Aliases', ["-ERR 'Aliases' is not supported by TextSession::Attributes"]),
    ('Mixer1 set inputMute 1', ['-ERR Parse error at 22: not enough parameters supplied']),
    ('Mixer1 get inputLevel 1', ['+OK "value":0.000000']),
    ('Input1 get gain channel1', ['-ERR Parse error at 16: could not parse value']),
    ('MyLevel1 get level 10', ['-ERR INVALID_PARAMETER Index out of range:channelIndex min:1 max:8 received:10']),
    ('Mixer1 set crosspoint 1 1 true', ['+OK']),
    ('Mixer1 set crosspoint "1""1" true', ['+OK']),
    ('Matrix1 set crosspointLevelState 1 1 true', ['+OK']),
    ('Level1 get numChannels', ['+OK "value":4']),
    # measured on a SERVER-IO, firmware 5.7.1.4
    ('Level1 set numChannels 4', ['-ERR operation set not supported on attribute: numChannels']),
    ('Level1 get levels', ['+OK "value":[0.000000 0.000000 0.000000 0.000000]']),
    ('Mute1 get numChannels', ['+OK "value":3']),
    ('Mute1 get mutes', ['+OK "value":[false false false]']),
    ('Input1 get gain 1', ['+OK "value":24.000000']),
    ('Input1 set gain 1 12', ['+OK']),
    ('Input1 get gain 1', ['+OK "value":12.000000']),
    ('Output1 set mute 1 true', ['+OK']),
    ('Router1 get input 1', ['+OK "value":0']),
    ('Router1 set input 1 1', ['+OK']),
    ('PresetButton1 get preset 1', ['+OK "value":1001']),
    ('PresetButton1 set preset 1 1001', ['+OK']),
    ('SourceSelector1 set sourceSelection 1', ['+OK']),
    ('RoomCombiner1 get wallState 1', ['+OK "value":true']),
    ('RoomCombiner1 set wallState 1 true', ['+OK']),
    ('AudioMeter2 subscribe level 3 mymeter 1000', ['! "publishToken":"mymeter" "value":-100.000000', '+OK']),
    ('AudioMeter2 unsubscribe level 3 mymeter', ['+OK']),
]
bad = []
for command, expected in EXAMPLES:
    got = ttp(c, command)
    if got != [command] + expected:
        bad.append('{} -> {}'.format(command, got))
check('B4 {} documented examples: echo, then the documented reply ({} wrong{})'.format(
    len(EXAMPLES), len(bad), ': ' + '; '.join(bad[:3]) if bad else ''), not bad)
check('B5 float32 formatting: -98.1 reads back as -98.099998',
      ttp(c, 'Level1 set level 2 -98.1') == ['Level1 set level 2 -98.1', '+OK']
      and ttp(c, 'Level1 get level 2')[1] == '+OK "value":-98.099998')
check('B6c a boolean is taken in any capitalisation (Biamp\'s Crestron library sends True / False)',
      ttp(c, 'Mute1 set mute 1 True')[1] == '+OK' and ttp(c, 'Mute1 get mute 1')[1] == '+OK "value":true'
      and ttp(c, 'Mute1 set mute 1 False')[1] == '+OK')
check('B6b SESSION get alias answers the block address, with the Level Control class code',
      ttp(c, 'SESSION get alias Level1')[1] == '+OK "value":{"deviceId":1 "classCode":5120 "instanceNum":1}')
check('B6 SESSION get aliases lists the design plus DEVICE',
      ttp(c, 'SESSION get aliases')[1].startswith('+OK "list":["123" "AGC1" "AudioMeter2" "DEVICE"'))
check('B7 non-verbose: "+OK 3" and "! "label" value" publishes',
      ttp(c, 'SESSION set verbose false')[1] == '+OK' and ttp(c, 'Mute1 get numChannels')[1] == '+OK 3'
      and ttp(c, 'Level1 subscribe level 1 myLevelName 500')[1:] == ['! "myLevelName" 0.000000', '+OK'])
check('B8 a subscription publishes the change, once, after the command\'s +OK',
      ttp(c, 'Level1 set level 1 -40', 0.1)[1:] == ['+OK', '! "myLevelName" -40.000000'])
held = ttp(c, 'Level1 set level 1 -41', 0.05)[1:] + ttp(c, 'Level1 set level 1 -42', 0.05)[1:]
check('B9 changes inside the 500 ms interval are held, then sent once when it ends (latest value)',
      held == ['+OK', '+OK'] and lines(c.read_after(0.7)) == ['! "myLevelName" -42.000000'])
check('B10 subscribing a live label again: -ERR ALREADY_SUBSCRIBED; unsubscribing a dead one: NOT_SUBSCRIBED',
      ttp(c, 'Level1 subscribe level 1 myLevelName 500')[1] == '-ERR ALREADY_SUBSCRIBED'
      and ttp(c, 'Level1 unsubscribe level 1 nobody')[1] == '-ERR NOT_SUBSCRIBED')
ttp(c, 'SESSION set verbose true')
sub = ttp(c, 'AudioMeter2 subscribe level 1 m1 200')
seen = [sum('"publishToken":"m1"' in l for l in sub[3:])]  # a publish can already follow the +OK on a busy PC


def more_publishes():
    seen[0] += c.read_after(0.05).count(b'"publishToken":"m1"')
    return seen[0] >= 3


check('B11 a meter publishes every interval, changed or not',
      sub[1:3] == ['! "publishToken":"m1" "value":-100.000000', '+OK'] and wait_for(more_publishes, 3.0))
ttp(c, 'AudioMeter2 unsubscribe level 1 m1')
check('B12 out of range is refused, increment stops at the bound',
      ttp(c, 'Level1 set level 3 20')[1].startswith('-ERR INVALID_PARAMETER Value out of range:level')
      and ttp(c, 'Level1 increment level 3 50')[1] == '+OK' and ttp(c, 'Level1 get level 3')[1] == '+OK "value":12.000000')
check('B13 a changed max level pulls the level down to it',
      ttp(c, 'Level1 set maxLevel 3 6')[1] == '+OK' and ttp(c, 'Level1 get level 3')[1] == '+OK "value":6.000000')
check('B14 the gain steps are 6 dB (13 lands on 12)',
      ttp(c, 'Input1 set gain 2 13')[1] == '+OK' and ttp(c, 'Input1 get gain 2')[1] == '+OK "value":12.000000')
check('B15 a mixer row sets every crosspoint of the input',
      ttp(c, 'Matrix1 set crosspointLevelStateRow 2 true')[1] == '+OK'
      and all(ttp(c, 'Matrix1 get crosspointLevelState 2 {}'.format(o))[1] == '+OK "value":true' for o in (1, 2, 3, 4)))
check('B16 presets: recall of one never saved fails; save, change, recall restores',
      ttp(c, 'DEVICE recallPreset 1002')[1] == '-GENERAL_FAILURE' and ttp(c, 'DEVICE savePreset 1002')[1] == '+OK'
      and ttp(c, 'Mute1 set mute 1 true')[1] == '+OK' and ttp(c, 'DEVICE recallPreset 1002')[1] == '+OK'
      and ttp(c, 'Mute1 get mute 1')[1] == '+OK "value":false')
check('B16b the AGC table\'s example, and bypass (block-wide, no index) sets and reads back',
      ttp(c, 'AGC1 set speech true')[1] == '+OK' and ttp(c, 'AGC1 get speech')[1] == '+OK "value":true'
      and ttp(c, 'AGC1 set bypass true')[1] == '+OK' and ttp(c, 'AGC1 get bypass')[1] == '+OK "value":true')
c.close()

raw = start_device('biamp_tesira', 'raw', options={'telnet': False})
r = RawClient(raw[1])
check('B17 with negotiation off, a raw client gets the banner and a session at once',
      b'Welcome' in r.read_after(0.2) and ttp(r, 'Level1 get level 1')[1] == '+OK "value":0.000000')
r.close()
raw[0].close()

# ----- C. the Extron catalog module over SSH, no password
gdev, gport = start_device('biamp_tesira', 'Room Tesira', design=ROOM)
sport = add_ssh(gdev)
m = TESIRA.SSHClass('127.0.0.1', sport, Credentials=('default', None), Model='Tesira SERVER-IO')
m.errors = []
m.Error = lambda message: m.errors.append(message[0])
m.Discard = lambda message: m.errors.append('discard ' + str(message))
check('C1 SSHClass logs in as default with no password', m.Connect() == 'Connected')
import paramiko  # noqa: E402
probe = paramiko.Transport(('127.0.0.1', sport))
probe.start_client(timeout=5)
try:
    probe.auth_none('default')
    none_refused = False
except paramiko.BadAuthenticationType as e:
    none_refused = sorted(e.allowed_types) == ['keyboard-interactive', 'publickey']
try:
    probe.auth_password('default', '', fallback=False)
    password_refused = False
except paramiko.AuthenticationException:
    password_refused = True
probe.close()
check('C1b "none" is refused and publickey and keyboard-interactive are offered, as on a SERVER-IO '
      '5.7.1.4 (an Extron processor given "none" never finishes Connect)', none_refused)
check('C1c a plain password login is refused (the real unit offers none; clients fall back)', password_refused)
shell = paramiko.SSHClient()
shell.set_missing_host_key_policy(paramiko.AutoAddPolicy())
shell.connect('127.0.0.1', sport, username='default', password='', allow_agent=False, look_for_keys=False)
check('C1d the server identifies as the unit\'s OpenSSH', shell.get_transport().remote_version == 'SSH-2.0-OpenSSH_10.4')
chan = shell.invoke_shell()
opened = time.monotonic()
seen, last_login_at, welcome_at = b'', None, None
while time.monotonic() - opened < 3 and welcome_at is None:
    if chan.recv_ready():
        seen += chan.recv(4096)
        if last_login_at is None and b'Last login: ' in seen:
            last_login_at = time.monotonic() - opened
        if b'Welcome' in seen:
            welcome_at = time.monotonic() - opened
    time.sleep(0.005)
shell.close()
check('C1e the shell prints the last-login line at once and the welcome about 0.78 s later',
      last_login_at is not None and last_login_at < 0.3 and welcome_at is not None and 0.6 < welcome_at < 1.2
      and seen.index(b'Last login: ') < seen.index(b'Welcome')
      and b' from 127.0.0.1\r\r\n\r\n\r\n' in seen)
m.Update('FirmwareVersion')
check('C2 verbose first, then DEVICE get version', wait_for(lambda: m.ReadStatus('FirmwareVersion') == '4.5.0.10', 3))
for tag in ('ProgramLevel',):
    m.Update('LevelControl', {'Instance Tag': tag})
for tag in ('ProgramMute', 'MicMute'):
    m.Update('MuteControl', {'Instance Tag': tag})
check('C3 level and mute subscriptions fan out to every channel',
      wait_for(lambda: m.ReadStatus('LevelControl', {'Instance Tag': 'ProgramLevel', 'Channel': '3'}) == 0
               and m.ReadStatus('MuteControl', {'Instance Tag': 'MicMute', 'Channel': '7'}) == 'Off', 3))
m.Set('LevelControl', -20, {'Instance Tag': 'ProgramLevel', 'Channel': '2'})
m.Set('MuteControl', 'On', {'Instance Tag': 'ProgramMute', 'Channel': '1'})
check('C4 a Set comes back through the subscription',
      wait_for(lambda: m.ReadStatus('LevelControl', {'Instance Tag': 'ProgramLevel', 'Channel': '2'}) == -20
               and m.ReadStatus('MuteControl', {'Instance Tag': 'ProgramMute', 'Channel': '1'}) == 'On', 3))
gdev.panel('MicMute mute 5', True)
check('C5 a change made at the Tesira (the UI) reaches the module unasked',
      wait_for(lambda: m.ReadStatus('MuteControl', {'Instance Tag': 'MicMute', 'Channel': '5'}) == 'On', 3))
m.Set('RoomCombinerWall', 'Open', {'Instance Tag': 'RoomCombiner', 'Wall': '1'})
time.sleep(0.3)
m.Update('RoomCombinerWall', {'Instance Tag': 'RoomCombiner', 'Wall': '1'})
m.Update('RoomCombinerGroup', {'Instance Tag': 'RoomCombiner', 'Room': '2'})
check('C6 wall Open = wallState false: rooms 1 and 2 are combined (group 1)',
      wait_for(lambda: m.ReadStatus('RoomCombinerWall', {'Instance Tag': 'RoomCombiner', 'Wall': '1'}) == 'Open'
               and m.ReadStatus('RoomCombinerGroup', {'Instance Tag': 'RoomCombiner', 'Room': '2'}) == '1', 3)
      and gdev.state['RoomCombiner wallState 1'] is False)
gdev.panel('resend_config', True)
check('C7 resending the configuration drops every subscription', gdev.state['subscriptions'] == 0)
gdev.panel('ProgramLevel level 3', -30)
time.sleep(0.4)
check('C8 ...so a change made after it isn\'t published',
      m.ReadStatus('LevelControl', {'Instance Tag': 'ProgramLevel', 'Channel': '3'}) == 0)
m.Update('LevelControl', {'Instance Tag': 'ProgramLevel'})
check('C9 the module\'s next Update re-subscribes and catches up',
      wait_for(lambda: m.ReadStatus('LevelControl', {'Instance Tag': 'ProgramLevel', 'Channel': '3'}) == -30, 3))
m.Update('LevelControl', {'Instance Tag': 'NoSuchTag'})
check('C10 an instance tag not in the design: the module reports the address error',
      wait_for(lambda: any('address not found' in e for e in m.errors), 3))
echo_off = [e for e in m.errors if 'address not found' not in e]
check('C11 no other module errors ({})'.format(echo_off), not echo_off)
m.Disconnect()

# Planted break: without the echo the module's reply patterns never match
gdev.set_option('echo', False)
m2 = TESIRA.SSHClass('127.0.0.1', sport, Credentials=('default', None), Model='Tesira SERVER-IO')
m2.Error = m2.Discard = lambda message: None
m2.Connect()
m2.Update('FirmwareVersion')
check('C12 planted break: with the echo off the module reads nothing (so C2 proves the echo matters)',
      not wait_for(lambda: m2.ReadStatus('FirmwareVersion') is not None, 2.5))
m2.Disconnect()
gdev.close()

# ----- D. Room Combiner rules (object page), in-process
d = start_device('biamp_tesira', 'rc', design={'RC': {'block': 'Room Combiner', 'rooms': 3, 'walls': 2}})[0]


class Link:
    kind, label = 'ssh', 'test'

    def __init__(self):
        self.state, self.buffer = {}, b''


L = Link()


def say(command):
    with d.lock:
        return lines(d.handle(command.encode(), L))[1:]


say('RC set sourceSelection 1 2')
say('RC set sourceSelection 2 4')
say('RC set muteOut 2 true')
say('RC set levelOut 1 -20')
say('RC set wallState 1 false')
check('D1 combining takes the lower room\'s source, either room\'s mute, the lower level',
      [d.state['RC sourceSelection 2'], d.state['RC muteOut 1'], d.state['RC levelOut 2']] == [2, True, -20.0])
say('RC set levelOut 2 -5')
check('D2 a linked control set in one room moves the whole group', d.state['RC levelOut 1'] == -5.0)
say('RC set sourceSelection 3 3')
say('RC set preferredRoom 2 3')
say('RC set wallState 2 false')
check('D3 the wall\'s preferredRoom picks the source when it opens (room 3 wins)',
      [d.state['RC sourceSelection {}'.format(r)] for r in (1, 2, 3)] == [3, 3, 3]
      and [d.state['RC group {}'.format(r)] for r in (1, 2, 3)] == [1, 1, 1])
say('RC set wallState 1 true')
check('D4 closing wall 1 splits room 1 off (group 0) and leaves 2+3 combined',
      [d.state['RC group {}'.format(r)] for r in (1, 2, 3)] == [0, 1, 1])
check('D5 wall and room indexes are checked',
      say('RC get wallState 3') == ['-ERR INVALID_PARAMETER Index out of range:wallNumberIndex min:1 max:2 received:3'])
d.close()

# ----- E. the bench carries the design
cfg = {'profile': 'biamp_tesira', 'name': 'T', 'design': ROOM, 'transports': []}
built = bench.build(cfg)
check('E1 a bench entry\'s design builds those blocks and saves back unchanged',
      sorted(built.blocks) == sorted(ROOM) and built.to_config()['design'] == ROOM)
built.close()

# ----- F. a protected system (measured on a SERVER-IO, 5.7.1.4)
pdev, _ = start_device('biamp_tesira', 'Protected Tesira', design=ROOM,
                       options={'ssh_user': 'admin', 'ssh_password': 'secret'})
pport = add_ssh(pdev)


def ssh_shell(user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect('127.0.0.1', pport, username=user, password=password, allow_agent=False, look_for_keys=False)
    except paramiko.AuthenticationException:
        return None, None
    chan = client.invoke_shell()
    t0 = time.monotonic()
    seen = b''
    while b'Welcome' not in seen and time.monotonic() - t0 < 3:
        if chan.recv_ready():
            seen += chan.recv(4096)
        time.sleep(0.01)
    return client, chan


def ask(chan, line):
    chan.send(line + '\n')
    out, t0 = b'', time.monotonic()
    # the echo line, then the reply line
    while time.monotonic() - t0 < 1 and not (out.count(b'\r\n') >= 2 and out.endswith(b'\r\n')):
        if chan.recv_ready():
            out += chan.recv(4096)
        time.sleep(0.01)
    return out.decode().split('\r\n')[1:-1]


cl, ch = ssh_shell('default', 'anything')
check('F1 protected: default still logs in, with any password', ch is not None)
check('F2 ...but a block get answers PERMISSION_DENIED',
      ch is not None and ask(ch, 'ProgramLevel get level 1') == ['-ERR PERMISSION_DENIED'])
check('F3 ...the verb check still answers first',
      ch is not None and ask(ch, 'ProgramLevel set numChannels 4')
      == ['-ERR operation set not supported on attribute: numChannels'])
check('F4 ...and SESSION still answers', ch is not None and ask(ch, 'SESSION get verbose') == ['+OK "value":true'])
cl and cl.close()
cl, ch = ssh_shell('admin', 'wrong')
check('F5 the configured user with the wrong password is refused', ch is None)
cl, ch = ssh_shell('admin', 'secret')
check('F6 the configured user with its password gets control',
      ch is not None and ask(ch, 'ProgramLevel get level 1') == ['+OK "value":0.000000'])
cl and cl.close()
pm = TESIRA.SSHClass('127.0.0.1', pport, Credentials=('admin', 'secret'), Model='Tesira SERVER-IO')
pm.errors = []
pm.Error = lambda message: pm.errors.append(message[0])
check('F7 the catalog module logs in as admin', pm.Connect() == 'Connected')
pm.Update('LevelControl', {'Instance Tag': 'ProgramLevel'})
check('F8 ...and reads a level with no errors',
      wait_for(lambda: pm.ReadStatus('LevelControl', {'Instance Tag': 'ProgramLevel', 'Channel': '1'}) == 0, 3)
      and pm.errors == [])
pm.Disconnect()
pd = TESIRA.SSHClass('127.0.0.1', pport, Credentials=('default', 'secret'), Model='Tesira SERVER-IO')
pd.errors = []
pd.Error = lambda message: pd.errors.append(message[0])
check('F9 the catalog module as default connects (as MTX-002 did)...', pd.Connect() == 'Connected')
pd.Update('LevelControl', {'Instance Tag': 'ProgramLevel'})
check('F10 ...and every command reports PERMISSION_DENIED', wait_for(lambda: 'PERMISSION_DENIED' in pd.errors, 3))
pd.Disconnect()

finish()
