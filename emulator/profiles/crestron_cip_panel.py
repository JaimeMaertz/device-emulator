"""
A Crestron panel at one IP ID: a CCC-110-T (Teams Rooms room controls), or any TSW / XPanel slot. It dials the
control processor over CIP (a "cip" transport in the bench: {"type": "cip", "host": "<processor>"}) and speaks the
panel side of the protocol through the crestron_cip package (`crestron_cip.PanelSession`), so the program sees a panel
come online at its IP ID, gets presses from it, and sends it feedback.

Behavior from a real CCC-110-T's session with an RMC4, recorded through crestron_cip's proxy, and checked live
with this profile's sign-on:
- **Sign-on:** the CCC model signs on as the real one does (packet 0x26: IP ID, MAC, "Crestron", "UC-ENGINE", a
  name). At a CCC-110-T slot the processor takes this panel's presses; a generic XPanel sign-on (packet 0x01, the TSW
  / XPanel model) gets feedback there but its presses do nothing.
- **The update never ends at a CCC slot:** the processor stops after step 0x16, to the real CCC too, so "registered"
  is the working state there.
- **Meeting Room extender:** Show Project and Hide Project are digital 18503 / 18504, in both directions. The
  processor pulses 18504 to hide the room controls; the CCC answers with its state on the same joins
  (Show Project_fb, Hide Project_fb). This profile answers the same way and counts each command.
- **System4 extender:** "MTR APP in meeting_fb" (O468) is digital 27767, so the extender starts at 27300. The real
  CCC raised it as its meeting state (serial 33049) went to "TeamsMeeting", and dropped it at "Idle".

Not emulated: the CCC's other reserved traffic (a pulse on 27811 every 5 s, 27793/27794/27797/27798 toggling every
second or two, the paired TS-1080-UC's model and firmware on serials 34007/34008, its own address and versions on
serials 17300, 27710, 33026 and others).
"""
from ..core import Button, Device, Level, NumberOption, Readout, TextOption, Toggle

from crestron_cip import PanelSession, protocol as cip  # the package at the repository root

HEARTBEAT_SECONDS = 10


class CrestronCipPanel(Device):
    profile_id = 'crestron_cip_panel'
    profile_name = 'Crestron panel over CIP (CCC-110-T, TSW, XPanel)'
    kind = 'panel'
    log_as = 'hex'
    models = {
        'CCC-110-T': {'ipid': '03', 'device': True, 'reserved_in': {18503: 'Show Project', 18504: 'Hide Project'}},
        'TSW / XPanel': {'ipid': '05', 'device': False, 'reserved_in': {}},
    }
    SHOW_FB, HIDE_FB, IN_MEETING = 18503, 18504, 27767
    options = [
        TextOption('ipid', 'IP ID (hex)', '03'),
        TextOption('mac', 'MAC the CCC signs on with', '02:00:00:00:00:03'),
        TextOption('room_name', 'Name field of the CCC sign-on (the real one sent its Room ID)', 'TEST'),
        TextOption('register_body', 'XPanel sign-on body (hex, ii = IP ID; empty = the generic one)', ''),
        NumberOption('in_meeting_join', 'In-meeting join (System4 O468)', 27767, 0, 32768),
    ]
    notes = __doc__

    def reset(self):
        self.state.update({'status': 'not connected', 'update': '', 'hide_project': 0, 'show_project': 0,
                           'in_meeting': False, 'controls_shown': False, 'join': 1, 'high_in': ''})
        self.session = None

    def _ipid(self):
        try:
            return int(str(self.opts['ipid'] or self.spec['ipid']), 16)
        except ValueError:
            return int(self.spec['ipid'], 16)

    def _body(self):
        text = (self.opts['register_body'] or '').replace(' ', '')
        if not text:
            return cip.XPANEL_REGISTER_BODY
        return b'{ipid}'.join(bytes.fromhex(part) for part in text.lower().split('ii'))

    # ----- the link to the processor
    def connected(self, link):
        device = {'mac': self.opts['mac'] or '02:00:00:00:00:03', 'name': self.opts['room_name'] or ''} \
            if self.spec['device'] else None
        self.session = PanelSession(self._ipid(), self._body(), device)
        self.session.on_join(self._joined)
        self.set('status', 'connected, waiting for the registration request')
        self.after('heartbeat', HEARTBEAT_SECONDS, self._heartbeat)
        return None   # the processor speaks first

    def disconnected(self, link):
        self.cancel('heartbeat')
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
        if status == 'registered' and self.session.update_steps[-1:] == ['update-penultimate']:
            status = ('registered with {} (a CCC slot ends its update at 0x16: working)'.format(self.session.processor_name)
                      if self.spec['device'] else 'registered; update stopped after 0x16 (a CCC slot: presses '
                      'from this generic sign-on will be ignored)')
        if self.session.processor_name and status == 'registered':
            status = 'registered with ' + self.session.processor_name
        self.set('status', status)
        self.set('update', ' > '.join(self.session.update_steps), log=False)
        return b''.join(out) or None

    def _joined(self, kind, join, value):
        if kind == 'd':
            name = self.spec['reserved_in'].get(join)
            if name and value:
                key = 'hide_project' if name == 'Hide Project' else 'show_project'
                self.set(key, self.state.get(key, 0) + 1)
                self.log('state', '{} (digital {}) from the processor'.format(name, join))
                self._report_controls(name == 'Show Project')
        highs = sorted(j for j, v in self.session.joins_in.d.items() if v)
        self.set('high_in', ' '.join(map(str, highs[:40])) + (' …' if len(highs) > 40 else ''), log=False)

    def _report_controls(self, shown):
        """The room controls shown or hidden, reported on Show / Hide Project_fb as the real CCC does."""
        self.set('controls_shown', shown)
        if self.session and self.links:
            self.push(self.session.digital(self.SHOW_FB, 1 if shown else 0))
            self.push(self.session.digital(self.HIDE_FB, 0 if shown else 1))

    # ----- the front panel
    def controls(self):
        c = [Readout('status', 'Link'), Readout('update', 'Update steps'),
             Readout('high_in', 'Digitals the processor holds high')]
        if self.spec['reserved_in']:
            c += [Readout('hide_project', 'Hide Project received (count)'),
                  Readout('show_project', 'Show Project received (count)'),
                  Toggle('controls_shown', 'Room controls showing (Show / Hide Project_fb)'),
                  Toggle('in_meeting', 'In a Teams meeting (System4 feedback)')]
        c += [Level('join', 'Digital join to press', 1, 32768), Button('press', 'Press and release that join')]
        return c

    def panel(self, key, value):
        if key == 'controls_shown':
            self._report_controls(bool(value))
        elif key == 'in_meeting':
            self.set('in_meeting', bool(value))
            join = int(self.opts['in_meeting_join'] or 0)
            if not join:
                self.log('warn', 'In-meeting join not set (option): nothing sent')
            elif self.session:
                self.push(self.session.digital(join, 1 if value else 0))
        elif key == 'press' and self.session:
            join = int(self.state['join'])
            self.push(self.session.press(join))
            self.after('release', 0.2, lambda: self.push(self.session.release(join)) if self.session else None)
        else:
            self.set(key, value)
