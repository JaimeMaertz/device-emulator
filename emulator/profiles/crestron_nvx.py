"""
A Crestron DM NVX endpoint as its control processor sees it: a CIP client at its IP ID (a "cip" transport in the bench:
{"type": "cip", "host": "<processor>"}), through the crestron_cip package. What a room program uses is emulated: the stream
URL a transmitter reports, the stream a receiver is routed to (and reports back), HDMI input sync, USB pairing, and the
COM port, carried to a display profile on this bench over TCP as if a serial cable ran there. No video.

Models (iodev.tio slots: Stream Transmit 1, Stream Receive 2, HDMI In 4, COM 8, USB 30):
    DM-NVX-D30    receiver; COM
    DM-NVX-E30    transmitter; HDMI In, COM
    DM-NVX-E30C   transmitter card (DMF-CI-8); HDMI In
    DM-NVX-360    transmitter or receiver ("Mode"); HDMI In, COM, USB
    DM-NVX-360C   transmitter or receiver card; HDMI In, USB

Measured on the wire against an RMC4 running a program with NVX Basic Routing:
- The device's own joins: StreamLocation (in) and StreamLocation_F (out) are serial 1 on every model;
  MulticastAddress / MulticastAddress_F are serial 4.
- The processor does not send a serial in the update it gives a device that connects: NVX Basic Routing sets each
  encoder's MulticastAddress once, at program start (base + 8 x the encoder's number), so an encoder that connects
  later never hears it. A real NVX keeps its multicast address in its own settings; this one keeps what it was sent
  and starts from the "Multicast address" option.
- A slot's joins come wrapped: data 20 <slot> <len> <body>. HDMI In SyncDetected_F is the slot's digital 1. USB: Pair
  (in, digital 11), RemovePairing (in, 12), Paired_F (out, 13), UsbRemoteDeviceId (in, serial 11),
  UsbRemoteDeviceId_F (out, 11), UsbLocalDeviceId_F (out, 12). COM data is the COM slot's serial 1 both ways; the
  processor sends the port's settings at sign-on (logged, not applied).

Guessed, not taken from a real unit: a receiver reports the stream it is sent as StreamLocation_F half a second
later, whatever the URL, and reports its current stream (empty included) when it connects; a receiver's
MulticastAddress_F is the multicast address of the emulated transmitter whose URL it is routed to (looked up across
this bench); Pair reports paired when a remote device ID is set, and RemovePairing unpairs, without
asking the other end; a transmitter's stream URL is rtsp://<address>:554/live.sdp; the USB local ID is a MAC-style
string from the IP ID unless set.
"""
import socket
import threading
import time

from ..core import ChoiceOption, Device, FlagOption, Readout, TextOption, Toggle

from crestron_cip import PanelSession, protocol as cip  # the package at the repository root

HEARTBEAT_SECONDS = 10
ECHO_SECONDS = 0.5
STREAM_LOCATION = 1                                      # serial, both directions, on the device's own symbol
MULTICAST = 4                                            # serial, both directions, on the device's own symbol
HDMI_IN, COM, USB = 4, 8, 30                             # slots (iodev.tio FixedN)
SYNC_F = 1                                               # HDMI In, digital out
PAIR, REMOVE_PAIRING, PAIRED_F = 11, 12, 13              # USB, digital
REMOTE_ID, REMOTE_ID_F, LOCAL_ID_F = 11, 11, 12          # USB, serial
NO_SOURCE = 'rtsp://NoSource/live.sdp'
STREAMS = {}   # stream URL -> multicast address, every emulated transmitter in this process (what a receiver reports)

MODELS = {
    'DM-NVX-D30': {'modes': ('Receiver',), 'hdmi_in': False, 'com': True, 'usb': False},
    'DM-NVX-E30': {'modes': ('Transmitter',), 'hdmi_in': True, 'com': True, 'usb': False},
    'DM-NVX-E30C': {'modes': ('Transmitter',), 'hdmi_in': True, 'com': False, 'usb': False},
    'DM-NVX-360': {'modes': ('Receiver', 'Transmitter'), 'hdmi_in': True, 'com': True, 'usb': True},
    'DM-NVX-360C': {'modes': ('Transmitter', 'Receiver'), 'hdmi_in': True, 'com': False, 'usb': True},
}


class CrestronNvx(Device):
    profile_id = 'crestron_nvx'
    profile_name = 'Crestron DM NVX (encoder / decoder) over CIP'
    kind = 'video'
    models = MODELS
    options = [
        TextOption('ipid', 'IP ID (hex)', '31'),
        ChoiceOption('mode', 'Mode (the 360 and 360C; others are fixed)', ['Default', 'Transmitter', 'Receiver'], 'Default'),
        TextOption('address', 'Address in the stream URL (blank = 10.0.0.<IP ID>)', ''),
        TextOption('multicast', 'Multicast address until the program sets one (transmitter)', ''),
        FlagOption('sync', 'HDMI input has a signal at start', True),
        TextOption('usb_id', 'USB local device ID (blank = from the IP ID)', ''),
        TextOption('com_target', 'COM port wired to (host:port of a display on this bench; blank = nothing)', ''),
    ]
    notes = __doc__

    def reset(self):
        self.session = None
        if not hasattr(self, 'com_running'):   # the COM link outlives a power cycle's reset(); started once
            self.com_sock, self.com_running = None, False
        self.state.update({'status': 'not connected', 'mode': self._mode(), 'stream': '', 'route': '',
                           'multicast': self.opts['multicast'], 'sync': bool(self.opts['sync']), 'usb_local': self._usb_id(), 'usb_remote': '',
                           'usb_paired': False, 'com': 'not wired' if not self.opts['com_target'] else 'closed',
                           'last': ''})
        if self.state['mode'] == 'Transmitter':
            self.state['stream'] = self._stream_url()
            STREAMS[self.state['stream']] = self.state['multicast']
        if self.spec['com'] and self.opts['com_target'] and not self.com_running:
            self._start_com()

    # ----- identity
    def _ipid(self):
        try:
            return int(str(self.opts['ipid']), 16)
        except ValueError:
            return 0x31

    def _mode(self):
        m = self.opts['mode']
        return m if m in self.spec['modes'] else self.spec['modes'][0]

    def _stream_url(self):
        return 'rtsp://{}:554/live.sdp'.format(self.opts['address'] or '10.0.0.{}'.format(self._ipid()))

    def _usb_id(self):
        return self.opts['usb_id'] or '00:10:7f:00:00:{:02x}'.format(self._ipid())

    # ----- the link to the processor
    def connected(self, link):
        self.session = PanelSession(self._ipid())
        self.session.on_join(self._joined)
        self.session.on_slot_join(self._slot_joined)
        if self.state['mode'] == 'Transmitter':
            self.session.serial(STREAM_LOCATION, self.state['stream'])   # replayed in every update the processor asks for
            if self.state['multicast']:
                self.session.serial(MULTICAST, self.state['multicast'])
        else:   # a receiver reports what it is subscribed to, empty included, so the program's feedback is current
            self.session.serial(STREAM_LOCATION, self.state['route'])
        self.set('status', 'connected, waiting for the registration request')
        self.after('heartbeat', HEARTBEAT_SECONDS, self._heartbeat)
        return None

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
        d = cip.decode(frame)
        if d.get('slot_config'):
            self.log('state', 'slot {} settings from the processor: {}'.format(d['slot'], d['slot_config']))
        before = self.session.status
        out = self.session.receive(frame)
        status = self.session.status
        self.set('status', 'registered (working)' if status == 'registered' else status)
        if d.get('control') == 'update-request' or (status == 'registered' and before != 'registered'):
            out.extend(self._slot_state())   # once accepted, and in every update the processor asks for
        return b''.join(out) or None

    def _slot_state(self):
        """Every slot join this device reports, for the update the processor asks for."""
        out = []
        if self.spec['hdmi_in']:
            out.append(cip.slot_digital(HDMI_IN, SYNC_F, self.state['sync']))
        if self.spec['usb']:
            out.append(cip.slot_serial(USB, LOCAL_ID_F, self.state['usb_local']))
            out.append(cip.slot_serial(USB, REMOTE_ID_F, self.state['usb_remote']))
            out.append(cip.slot_digital(USB, PAIRED_F, self.state['usb_paired']))
        return out

    def _report(self, pkt):
        if self.links:
            self.push(pkt)

    # ----- what the processor sends
    def _joined(self, kind, join, value):
        if kind == 's' and join == STREAM_LOCATION and self.state['mode'] == 'Receiver':
            self.set('route', value)
            self.set('last', 'routed to {}'.format(value or '(nothing)'))
            self.after('echo', ECHO_SECONDS, self._echo_route)
        elif kind == 's' and join == MULTICAST and self.state['mode'] == 'Transmitter':
            self.set('multicast', value)
            STREAMS[self.state['stream']] = value
            self.set('last', 'multicast address set to {}'.format(value or '(none)'))
            self._report(self.session.serial(MULTICAST, value))

    def _echo_route(self):
        if self.session:
            self._report(self.session.serial(STREAM_LOCATION, self.state['route']))
            self.set('multicast', STREAMS.get(self.state['route'], ''))
            self._report(self.session.serial(MULTICAST, self.state['multicast']))

    def _slot_joined(self, slot, kind, join, value):
        if slot == COM and kind == 's' and join == 1:
            self._com_write(value.encode('latin-1'))
        elif slot == USB and self.spec['usb']:
            if kind == 's' and join == REMOTE_ID:
                self.set('usb_remote', value)
                self._report(cip.slot_serial(USB, REMOTE_ID_F, value))
            elif kind == 'd' and join == PAIR and value:
                self._paired(bool(self.state['usb_remote']), 'Pair')
            elif kind == 'd' and join == REMOVE_PAIRING and value:
                self._paired(False, 'RemovePairing')

    def _paired(self, on, why):
        self.set('usb_paired', on)
        self.set('last', '{}: {}'.format(why, 'paired to ' + self.state['usb_remote'] if on else 'not paired'))
        self._report(cip.slot_digital(USB, PAIRED_F, on))

    # ----- the COM port, carried to a display on this bench
    def _start_com(self):
        self.com_running = True
        threading.Thread(target=self._com_run, daemon=True).start()

    def _com_target(self):
        host, _, port = str(self.opts['com_target']).rpartition(':')
        return host or '127.0.0.1', int(port)

    def _com_run(self):
        while self.com_running:
            try:
                sock = socket.create_connection(self._com_target(), timeout=5)
                sock.settimeout(None)
            except (OSError, ValueError) as e:
                self.set('com', 'cannot reach {} ({})'.format(self.opts['com_target'], e), log=False)
                time.sleep(5)
                continue
            self.com_sock = sock
            self.set('com', 'wired to {}'.format(self.opts['com_target']))
            while self.com_running:
                try:
                    data = sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                with self.lock:
                    self._report(cip.slot_serial(COM, 1, data))
            self.com_sock = None
            try:
                sock.close()
            except OSError:
                pass
            if self.com_running:
                self.set('com', 'closed, reconnecting', log=False)
                time.sleep(2)

    def _com_write(self, data):
        sock = self.com_sock
        if sock is None:
            self.log('warn', 'COM data with nothing wired: {}'.format(data.hex(' ')))
            return
        try:
            sock.sendall(data)
        except OSError:
            pass

    def close(self):
        self.com_running = False
        sock = self.com_sock
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        super().close()

    # ----- the front panel
    def controls(self):
        c = [Readout('status', 'Link'), Readout('mode', 'Mode')]
        if self.state['mode'] == 'Transmitter':
            c += [Readout('stream', 'Stream URL'), Readout('multicast', 'Multicast address')]
        else:
            c += [Readout('route', 'Routed to'), Readout('multicast', 'Receiving multicast')]
        if self.spec['hdmi_in']:
            c.append(Toggle('sync', 'HDMI input signal'))
        if self.spec['usb']:
            c += [Readout('usb_local', 'USB local ID'), Readout('usb_remote', 'USB remote ID'),
                  Readout('usb_paired', 'USB paired')]
        if self.spec['com']:
            c.append(Readout('com', 'COM port'))
        c.append(Readout('last', 'Last action'))
        return c

    def panel(self, key, value):
        if key == 'sync':
            self.set('sync', bool(value))
            self._report(cip.slot_digital(HDMI_IN, SYNC_F, bool(value)))
        else:
            self.set(key, value)
