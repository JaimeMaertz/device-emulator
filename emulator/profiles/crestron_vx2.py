"""
A Crestron IV-SAM-VX2 (1 Beyond Automate VX2 camera switching) as its control processor sees it: a CIP client at its
IP ID (a "cip" transport in the bench: {"type": "cip", "host": "<processor>"}), through the crestron_cip package. Only what
a room's controls use is emulated: automatic switching on and off, the live camera, and camera presets saved and
recalled. No video.

Joins are the IV-SAM-VX2 symbol's cues numbered as CIP numbers them: `[~UNUSED2~]` placeholders take a join number,
`[~UNUSED~]` ones don't (crestron_cip.symlib). Checked live on an RMC4 for Stop_Autoswitch (5),
Toggle_Autoswitch (6), Manual_Switch_Camera (10), Camera_Select (analog 23) and Autoswitch_F (out, 4); the others are
the same rule, not yet seen on the wire:

    in   digital  4 Start_Autoswitch   5 Stop_Autoswitch   6 Toggle_Autoswitch   10 Manual_Switch_Camera
                 21 Recall_Camera_Preset   22 Save_Camera_Preset
    in   analog  23 Camera_Select   24 Camera_Preset
    out  digital  4 Autoswitch_F

A generic CIP sign-on is accepted at a VX2 slot (unlike a CCC-110-T slot). The processor keeps the last Autoswitch_F
it got after this client leaves.

Guessed, not taken from a real unit: a preset holds the camera live when it was saved; recalling an empty preset
changes nothing; Manual_Switch_Camera and a recall leave automatic switching as it is (the program stops it first);
with automatic switching on, the live camera moves to the next one every "Talker change" seconds, standing in for
the active talker.
"""
from ..core import Choice, Device, NumberOption, Readout, TextOption, Toggle

from crestron_cip import PanelSession, protocol as cip  # the package at the repository root

HEARTBEAT_SECONDS = 10
START, STOP, TOGGLE, MANUAL, RECALL, SAVE = 4, 5, 6, 10, 21, 22   # digital, processor -> VX2
CAMERA_SELECT, CAMERA_PRESET = 23, 24                             # analog, processor -> VX2
AUTOSWITCH_F = 4                                                  # digital, VX2 -> processor


class CrestronVx2(Device):
    profile_id = 'crestron_vx2'
    profile_name = 'Crestron IV-SAM-VX2 (camera switching) over CIP'
    kind = 'camera'
    models = {'IV-SAM-VX2': {}}
    options = [
        TextOption('ipid', 'IP ID (hex)', '04'),
        NumberOption('cameras', 'Cameras', 4, 1, 8),
        NumberOption('presets', 'Presets', 5, 1, 24),
        NumberOption('talker_seconds', 'Talker change while automatic, seconds (0 = never)', 8, 0, 3600),
    ]
    notes = __doc__

    def reset(self):
        self.state.update({'status': 'not connected', 'auto': True, 'camera': 1, 'preset': 0, 'presets': '', 'last': ''})
        self.saved = {}       # preset -> camera
        self.session = None

    def _ipid(self):
        try:
            return int(str(self.opts['ipid']), 16)
        except ValueError:
            return 0x04

    # ----- the link to the processor
    def connected(self, link):
        self.session = PanelSession(self._ipid())
        self.session.on_join(self._joined)
        self.session.digital(AUTOSWITCH_F, self.state['auto'])   # sent with the update the processor asks for
        self.set('status', 'connected, waiting for the registration request')
        self.after('heartbeat', HEARTBEAT_SECONDS, self._heartbeat)
        self._arm_talker()
        return None

    def disconnected(self, link):
        self.cancel('heartbeat')
        self.cancel('talker')
        self.set('status', 'not connected')

    def _heartbeat(self):
        if self.session and self.links:
            self.push(self.session.heartbeat())
            self.after('heartbeat', HEARTBEAT_SECONDS, self._heartbeat)

    def frames(self, buffer):
        return cip.split(buffer)

    def handle(self, frame, link):
        if self.session is None:
            return None
        out = self.session.receive(frame)
        status = self.session.status
        self.set('status', 'registered (working)' if status == 'registered' else status)
        return b''.join(out) or None

    # ----- what the processor asks for
    def _joined(self, kind, join, value):
        if kind != 'd' or not value:
            return   # act on rising edges; analogs are read when a digital asks for them
        preset = self.session.get('a', CAMERA_PRESET, 0)
        if join == START:
            self._auto(True, 'Start_Autoswitch')
        elif join == STOP:
            self._auto(False, 'Stop_Autoswitch')
        elif join == TOGGLE:
            self._auto(not self.state['auto'], 'Toggle_Autoswitch')
        elif join == MANUAL:
            cam = self.session.get('a', CAMERA_SELECT, 0)
            if 1 <= cam <= int(self.opts['cameras']):
                self._camera(cam, 'Manual_Switch_Camera to camera {}'.format(cam))
                self.set('preset', 0)
            else:
                self.log('warn', 'Manual_Switch_Camera with Camera_Select {} (no such camera)'.format(cam))
        elif join == RECALL:
            if not 1 <= preset <= int(self.opts['presets']):
                self.log('warn', 'Recall_Camera_Preset with Camera_Preset {} (no such preset)'.format(preset))
            elif preset not in self.saved:
                self.set('last', 'recall preset {}: empty, nothing changed'.format(preset))
            else:
                self._camera(self.saved[preset], 'recall preset {}'.format(preset))
                self.set('preset', preset)
        elif join == SAVE:
            if not 1 <= preset <= int(self.opts['presets']):
                self.log('warn', 'Save_Camera_Preset with Camera_Preset {} (no such preset)'.format(preset))
            else:
                self.saved[preset] = self.state['camera']
                self.set('preset', preset)
                self.set('last', 'saved camera {} as preset {}'.format(self.state['camera'], preset))
                self._show_presets()

    def _auto(self, on, why):
        self.set('auto', on)
        self.set('last', '{}: automatic switching {}'.format(why, 'on' if on else 'off'))
        if on:
            self.set('preset', 0)
        if self.session:
            pkt = self.session.digital(AUTOSWITCH_F, on)
            if self.links:
                self.push(pkt)
        self._arm_talker()

    def _camera(self, cam, why):
        self.set('camera', cam)
        self.set('last', why)

    def _show_presets(self):
        self.set('presets', '  '.join('{}: cam {}'.format(p, c) for p, c in sorted(self.saved.items())), log=False)

    def _arm_talker(self):
        secs = int(self.opts['talker_seconds'] or 0)
        if self.state['auto'] and secs and self.links:
            self.after('talker', secs, self._next_talker)
        else:
            self.cancel('talker')

    def _next_talker(self):
        if self.state['auto']:
            self._camera(self.state['camera'] % int(self.opts['cameras']) + 1, 'automatic: active talker moved')
            self._arm_talker()

    # ----- the front panel (what someone in the 1 Beyond software would do)
    def controls(self):
        return [Readout('status', 'Link'), Readout('auto', 'Automatic switching'), Readout('camera', 'Live camera'),
                Readout('preset', 'Last preset (0 = none)'), Readout('presets', 'Saved presets'),
                Readout('last', 'Last action'),
                Toggle('auto', 'Automatic switching (software)'),
                Choice('camera', 'Live camera (move it by hand)', list(range(1, int(self.opts['cameras']) + 1)))]

    def panel(self, key, value):
        if key == 'auto':
            self._auto(bool(value), 'software')
        elif key == 'camera':
            self._camera(int(value), 'camera {} by hand'.format(value))
        else:
            self.set(key, value)

    def option_changed(self, key, value):
        if key == 'talker_seconds':
            self._arm_talker()
