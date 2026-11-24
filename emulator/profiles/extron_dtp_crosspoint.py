"""
Extron DTP CrossPoint 4K Series scaling presentation matrix switchers (82 / 84 / 86 / 108, plain,
IPCP SA, IPCP MA 70, and the 86 IPCP Q SA), SIS over RS-232 (9600 8N1), Telnet (TCP 23) and
SIS-over-SSH (TCP 22023).

Command:  SIS. A simple command ends at its command character ("1*3!", "3%", "0LS", "5.");
          an Escape command (Esc or W) ends at CR or "|" ("W3CV\\r", "WG50102*-200AU\\r").
          Not case sensitive; 1- or 2-digit numbers; stray CR/LF between commands is ignored.
Reply:    one line ending CR LF per command ("Out03 In01 All", "DsG50102*-200"); errors E01-E33.

Sources: DTP CrossPoint 4K Series User Guide 68-2368-02 rev R (Programming
Guide pp. 59-77, DSP SIS Commands pp. 158-167, insertion ports p. 51), checked against
the catalog modules extr_matrix_DTPCrossPoint_86_1084KSeriesv11000 (86/108)
and extr_matrix_DTP_CrossPoint_82_84_4kSeriesv1872 (82/84), both Extron Certified.

Fidelity:
- From the user guide: ties (!, %, $, to-all, untie, Esc+Q quick ties -> Qik), tie reads, the
  0*1*1VC / 0*1*2VC tie dumps, video mutes (per output and global), EDID assignment and values per
  matrix size, input/output audio selection (AFMT), HDCP authorization / output mode /
  notification / status, input and output format, bit depth, 0LS, names, global presets (1-32;
  E11 when not saved), resets (ZG, ZXXX, ZQQQ, ZY), BOOT, executive modes, I / N / Q / 0Q / S,
  scaler picture controls (BRIT, CONT, HDET, HCTR, VCTR, HSIZ, VSIZ, XIMG, APIX, ALIN, ASPR,
  OSCN, FILM, AMEM, TEST, SSAV, RATE, freeze, auto image, scaler presets), switching effects,
  logos, RS-232 insertion (LRPT, CP, MD), passwords, time zone, IP (CISG), port timeouts (TC),
  receive timeouts, flow control, verbose mode (CV), port maps (MT, MH, PMAP), SSH echo (E14 off
  SSH), the error codes, the copyright banner + Password: + Login lines, and the 5-minute TCP idle
  timeout. DSP: every object ID in the OID tables (gain G, digital gain H, mute M, phantom Z)
  limited to the model's inputs and outputs, with each block's range; group masters (GRPM:
  fader, raise/lower, mute, soft limits, type, members).
- Verbose: 0 on Telnet and SSH, 1 on RS-232. Modes 2/3 tag read replies with the set reply
  ("DsG50102*-200" instead of "-200"). Modes 1/3 get every change made from another connection
  or the front panel, as the reply that change got (front-panel ties as "Qik", preset recalls as
  "Rpr05", the Volume knobs as "GrpmD1*-200", lock changes as "Exe1").
- Reply shapes the guide doesn't give, taken from the catalog modules (written against a real
  unit): the verbose-tagged tie dump "Vgp00 Out01*01 02 ... -- --Vid", signal "Frq00 01010101",
  status "Sts00*12.250 38.000 750000 750000", unpadded outputs in "Vmt3A*1", "HdcpO1*3",
  "HdcpS1*0" and "LogoE3*4", tagged name reads "Nmi1,name" (a name write answers "Nmi01,name",
  per the guide), "Vkef003*0", "Test03*00", the mic meter read "DsV40000*1*600" (-60.0 dBFS) and
  the logo list "LogoQ00*<16>*<16>".
- Global preset recall answers "PrstR05" by default, which is what the 1.10 module parses; the
  guide says "Rpr05". The option switches. A front-panel recall pushes "Rpr05" either way.
- Ethernet insertion: LRPT / CP state is kept, but the emulator doesn't bridge ports 2001-2008 to
  the DTP lines. Put the device behind an insertion port on that port number in the bench (for
  example an NEC projector on TCP 2003), which is what the processor sees.
- Best guesses: an object ID the model doesn't have answers E14; a malformed command E10; SSH
  echo repeats the command and a CR LF before the reply; default names "Input 1" / "Output 1";
  DSP defaults 0 dB, mix-points muted except each output's own switcher path; groups 1-2 are
  gain groups (the Volume and Mic Volume knobs) and the option sets which groups are mute groups
  (the real layout comes from DSP Configurator; groups have no members here, so a group level
  doesn't move any object ID); the digital H prefix is its own value only on line-input gains
  (300xx) and is the same value as G elsewhere; input format reads 2 (HDMI RGB Full) with a
  signal and 0 without; HDCP output status is 3 with a display and a signal, 2 with a display
  and no signal, 0 with no display; the per-output "EVM" and "EOHDCP" lists are the status
  digits in output order; the part number is only real for the 86 IPCP Q SA (60-1382-92).
"""
import copy
import re
import time

from ..core import Device, Choice, Level, Readout, Toggle, Button, NumberOption, FlagOption, \
    ChoiceOption, TextOption

# Matrix sizes (user guide pp. 51, 63-68, 159-165)
SIZES = {
    '82': dict(inputs=8, outputs=2, ports=['1A', '1B', '2A', '2B'], scaled=[1, 2], dtp_inputs=[7, 8],
               insertion={2001: ('I', 7), 2002: ('I', 8), 2003: ('O', 1), 2004: ('O', 2)},
               code='DTPCP82', edid_default=38, edid_max=60),
    '84': dict(inputs=8, outputs=4, ports=['01', '02', '3A', '3B', '4A', '4B'], scaled=[3, 4],
               dtp_inputs=[7, 8], insertion={2001: ('I', 7), 2002: ('I', 8), 2003: ('O', 3), 2004: ('O', 4)},
               code='DTPCP84', edid_default=40, edid_max=62),
    '86': dict(inputs=8, outputs=6, ports=['01', '02', '3A', '3B', '4A', '4B', '05', '06'], scaled=[3, 4, 5, 6],
               dtp_inputs=[7, 8], insertion={2001: ('I', 7), 2002: ('I', 8), 2003: ('O', 3), 2004: ('O', 4),
                                             2005: ('O', 5), 2006: ('O', 6)},
               code='DTPCP86', edid_default=42, edid_max=64),
    '108': dict(inputs=10, outputs=8, ports=['01', '02', '03', '04', '5A', '5B', '6A', '6B', '07', '08'],
                scaled=[5, 6, 7, 8], dtp_inputs=[7, 8, 9, 10],
                insertion={2001: ('I', 7), 2002: ('I', 8), 2003: ('I', 9), 2004: ('I', 10),
                           2005: ('O', 5), 2006: ('O', 6), 2007: ('O', 7), 2008: ('O', 8)},
                code='DTPCP108', edid_default=44, edid_max=66),
}


RETURNS_SHOWN = 4   # virtual returns A-D get front-panel controls (the control programs use A-D)

# Mix-point rows (inputs) and columns (outputs) in the matrix's object-ID order:
# oid = 20000 + row * 100 + column. The names are the catalog modules' own.
MIX_ROWS = (['Out %d %s' % (n, side) for n in range(1, 9) for side in 'LR'] + ['Mic %d' % n for n in range(1, 5)]
            + ['V. Return %s' % c for c in 'ABCDEFGH'] + ['Exp. %d' % n for n in range(1, 17)])
MIX_COLS = ['Out %d %s' % (n, side) for n in range(1, 9) for side in 'LR'] + ['V. Send %s' % c for c in 'ABCDEFGH']
MIX_SHOWN_ROWS = list(range(0, 6)) + list(range(16, 24))   # outputs 1-3, the mics, V. Return A-D


def _models():
    models = {}
    for size in ('82', '84', '86', '108'):
        for suffix, amp in (('', None), (' IPCP SA', 'SA'), (' IPCP MA 70', 'MA')):
            models['DTP CrossPoint {} 4K{}'.format(size, suffix)] = dict(size=size, amp=amp, part='60-0000-00')
    models['DTP CrossPoint 86 4K IPCP Q SA'] = dict(size='86', amp='SA', part='60-1382-92')
    return models


# Scaler output rates (p. 73): code -> (width, height)
RATES = {10: (640, 480), 11: (800, 600), 12: (1024, 768), 13: (1280, 768), 14: (1280, 800), 15: (1280, 1024),
         16: (1360, 768), 17: (1366, 768), 18: (1440, 900), 19: (1400, 1050), 20: (1600, 900), 21: (1680, 1050),
         22: (1600, 1200), 23: (1920, 1200), 24: (720, 480), 25: (720, 480), 26: (720, 576), 64: (2048, 1200),
         65: (2048, 1536), 74: (2048, 2400), 76: (2560, 1080), 77: (2560, 1440), 78: (2560, 1600), 87: (3840, 2400)}
for _codes, _size in ((range(27, 35), (1280, 720)), (range(35, 38), (1920, 1080)), (range(38, 46), (1920, 1080)),
                      (range(46, 54), (2048, 1080)), (range(54, 62), (1920, 2160)), (range(62, 64), (1920, 2400)),
                      (range(66, 74), (2048, 2160)), (range(79, 84), (3840, 2160)), (range(89, 94), (4096, 2160)),
                      (range(201, 209), (1920, 1080))):
    for _c in _codes:
        RATES[_c] = _size

PRESET_REPLIES = ['PrstR05 (catalog module 1.10)', 'Rpr05 (user guide)']
NAME_CHARS = re.compile(r'[A-Za-z0-9_/ \-.()!#$%&*]{0,16}$')   # p. 77: the HTML-reserved characters are refused

SIMPLE = re.compile(rb'(\d{1,2}[AB](?:\*\d+)?)(B)|([0-9*+\-]*)(LS|[!%$BFXQINSA#.,\\])', re.I)
PARTIAL = re.compile(rb'(?:[0-9*+\-]*L?|\d{1,2}[AB](?:\*\d*)?)\Z', re.I)   # an A/B output ("3A*1B") is a video mute

# Escape-command keywords, longest first so VKEF wins over EF, ZXXX over ZG, ...
KEYWORDS = sorted(['AU', 'GRPM', 'VC', 'CV', 'ECHO', 'EDID', 'AFMT', 'HDCP', 'STAT', 'VTPO', 'BITD', 'VM', 'HDBT',
                   'NI', 'NO', 'UNAM', 'PNAM', 'ZG', 'ZXXX', 'ZQQQ', 'ZY', 'BOOT', 'BRIT', 'CONT', 'HDET', 'HCTR',
                   'VCTR', 'HSIZ', 'VSIZ', 'XIMG', 'APIX', 'ALIN', 'ASPR', 'OSCN', 'PRST', 'AMEM', 'TEST', 'SSAV',
                   'RATE', 'FILM', 'LOGO', 'VKEF', 'LRPT', 'CP', 'TC', 'MD', 'CA', 'CU', 'CN', 'TZON', 'CISG',
                   'CF', 'CE', 'MT', 'MH', 'PMAP', 'SWEF', 'EDUR', 'DF', 'EF'], key=len, reverse=True)

# Scaler settings indexed by an input (X$) or a scaled output (X!): keyword -> (index, values, default,
# reply tag, index padding, value padding, +/- steps)
SCALER = {
    'SWEF': ('scaled', range(0, 4), 0, 'Swef', 1, 1, False),
    'EDUR': ('scaled', range(1, 51), 2, 'Edur', 1, 1, False),
    'ASPR': ('input', (1, 2), 1, 'Aspr', 2, 1, False),
    'FILM': ('input', (0, 1), 1, 'Film', 2, 1, False),
    'BRIT': ('input', range(0, 128), 64, 'Brit', 2, 3, True),
    'CONT': ('input', range(0, 128), 64, 'Cont', 2, 3, True),
    'HDET': ('scaled', range(0, 128), 64, 'Hdet', 2, 3, True),
    'HCTR': ('scaled', range(-10240, 10241), 0, 'Hctr', 2, 1, True),
    'VCTR': ('scaled', range(-10240, 10241), 0, 'Vctr', 2, 1, True),
    'AMEM': ('scaled', (0, 1), 0, 'Amem', 2, 1, False),
    'TEST': ('scaled', range(0, 7), 0, 'Test', 2, 2, False),
    'RATE': ('scaled', tuple(RATES), 34, 'Rate', 2, 2, False),
    'OSCN': ('scaled', (0, 1, 2), 0, 'Oscn', 1, 1, False),
    'VKEF': ('any16', range(0, 5), 0, 'Vkef', 3, 1, False),
}


class SisError(Exception):
    pass


def err(code):
    raise SisError('E{:02d}'.format(code))


def number(text, code=13):
    try:
        return int(text)
    except (TypeError, ValueError):
        err(code)


class ExtronDTPCrossPoint(Device):
    profile_id = 'extron_dtp_crosspoint'
    profile_name = 'Extron DTP CrossPoint 4K matrix'
    kind = 'switcher'
    models = _models()
    log_as = 'ascii'
    default_tcp_port = 23
    default_ssh_port = 22023
    tcp_idle_timeout = 300
    options = [
        ChoiceOption('preset_reply', 'Global preset recall reply', PRESET_REPLIES, PRESET_REPLIES[0]),
        TextOption('admin_password', 'Administrator password (blank = none)', ''),
        TextOption('user_password', 'User password (blank = none)', ''),
        NumberOption('first_mute_group', 'Group masters from this number up are mute groups', 17, 3, 33),
        NumberOption('reboot_seconds', 'Reboot time after BOOT / CISG / ZQQQ (s)', 30, 0, 300),
    ]
    notes = __doc__

    def __init__(self, name, model=None, options=None, design=None):
        self._framing_link = None
        super().__init__(name, model, options, design)
        self.tcp_idle_timeout = self.state['port_timeout'] * 10

    # ----- model shape
    @property
    def inputs(self):
        return SIZES[self.spec['size']]['inputs']

    @property
    def outputs(self):
        return SIZES[self.spec['size']]['outputs']

    def _oids(self):
        """Every DSP object ID this model has: oid -> (low, high, has mute)."""
        n_in, n_out, amp = self.inputs, self.outputs, self.spec['amp']
        oids = {}
        for i in range(2 * n_in):
            oids[30000 + i] = (-180, 240, True)       # line input gain
            oids[30100 + i] = (-120, 120, False)      # line input trim
        for m in range(4):
            oids[40000 + m] = (-180, 800, True)       # mic gain
            oids[40100 + m] = (-1000, 120, True)      # mic pre-mixer gain
        for o in range(2 * n_out):
            oids[50000 + o] = (-1000, 120, True)      # post-matrix gain
            oids[60000 + o] = (-1000, 0, True)        # output volume
            oids[60100 + o] = (-120, 120, False)      # output trim
        for o in range(2 * min(4, n_out)):
            oids[60200 + o] = (-1000, 0, True)        # HDMI volume
        for v in range(8):
            oids[50100 + v] = (-1000, 120, True)      # virtual returns
        for e in range(16):
            oids[50200 + e] = (-1000, 120, True)      # expansion inputs
        if amp:
            oids[60016] = (-1000, 0, True)
            oids[60116] = (-120, 120, False)
            oids[60117] = (-120, 120, False)
            if amp == 'SA':
                oids[60017] = (-1000, 0, True)
        for row in range(44):
            if row < 16 and row // 2 >= n_out:
                continue
            for col in range(24):
                if col < 16 and col // 2 >= n_out:
                    continue
                if row < 16 and col < 16 and col != row:
                    continue   # a switcher output only reaches its own output (and the sends)
                if 20 <= row < 28 and col == 16 + row - 20:
                    continue   # a virtual return can't feed its own send
                oids[20000 + row * 100 + col] = (-350, 250, True)
        return oids

    # ----- state
    def reset(self):
        s = self.state
        n_in, n_out, size = self.inputs, self.outputs, SIZES[self.spec['size']]
        oids = self._oids()
        dsp = {oid: {'G': 0, 'M': 0} for oid in oids}
        for oid in oids:
            row, col = divmod(oid - 20000, 100)
            if 0 <= row < 44 and not (row < 16 and row == col):
                dsp[oid]['M'] = 1   # mix-points start muted, except each output's own switcher path
        for i in range(2 * n_in):
            dsp[30000 + i]['H'] = 0
        s.update(
            dsp=dsp, phantom={m: 0 for m in range(4)}, meter={m: 0 for m in range(4)},
            groups={g: {'level': 0, 'mute': 0, 'upper': 800, 'lower': -1000} for g in range(1, 33)},
            presets={}, scaler_presets={}, names_in={i: 'Input {}'.format(i) for i in range(1, n_in + 1)},
            names_out={o: 'Output {}'.format(o) for o in range(1, n_out + 1)}, edid_names={}, preset_names={},
            logo_names={}, logos={}, logo_files={}, files={},
            edid={i: size['edid_default'] for i in range(1, n_in + 1)},
            afmt_in={i: 0 for i in range(1, n_in + 1)}, afmt_out={o: 0 for o in range(1, n_out + 1)},
            hdcp_auth={i: 1 for i in range(1, n_in + 1)}, hdcp_mode={p: 0 for p in size['ports']},
            hdcp_notify={p: 1 for p in size['ports']}, vtpo={p: 0 for p in size['ports']},
            bitd={o: 0 for o in range(1, n_out + 1)}, vmute={p: 0 for p in size['ports']},
            scaler={key: {} for key in SCALER}, size_img={o: (3840, 2160) for o in size['scaled']},
            ssav={o: 501 for o in size['scaled']}, freeze={o: 0 for o in size['scaled']},
            lrpt_in={i: 0 for i in size['dtp_inputs']},
            lrpt_out={o: 0 for (_, o) in [v for v in size['insertion'].values() if v[0] == 'O']},
            cp={1: '9600,n,8,1'}, cf={1: 'N,0000'}, ce={1: '10,2,0,00000L'},
            port_timeout=30, uart_start=2000, telnet_port=23, web_port=80, ssh_port=22023, ssl_port=443,
            tz='CST', matrix_name='DTP-CrossPoint-{}'.format(self.spec['size']),
            ip='192.168.254.254', prefix=16, gateway='0.0.0.0',
            exe=2, temp=38, rebooting=False, preset_slot=1, mx_row=20, mx_col=0,
        )
        for port in size['insertion']:
            s['cp'][port - 1999] = '9600,n,8,1'
        for o in range(1, n_out + 1):
            s['vid%d' % o] = 0
            s['aud%d' % o] = 0
        for i in range(1, n_in + 1):
            s['sig%d' % i] = False
        for p in size['ports']:
            s['sink' + p] = True
        for m in range(1, 5):
            s['mic%d' % m] = -60
        self._mirror_knobs()
        self._mirror_dsp()

    def _mirror_dsp(self):
        """The virtual returns A-D (the program audio a control program raises and mutes) as flat
        state entries the front-panel page can show: vrg0-3 in dB, vrm0-3 muted."""
        s = self.state
        for v in range(RETURNS_SHOWN):
            block = s['dsp'][50100 + v]
            s['vrg%d' % v] = block['G'] / 10
            s['vrm%d' % v] = bool(block['M'])
        block = s['dsp'].get(20000 + s['mx_row'] * 100 + s['mx_col'])
        s['mx_mute'] = bool(block['M']) if block else True   # a mix-point this model doesn't have reads muted

    def _mix_row(self, row):
        """The columns a mix-point row is unmuted to, for the front-panel page."""
        dsp = self.state['dsp']
        cols = [MIX_COLS[c] for c in range(24) if 20000 + row * 100 + c in dsp and not dsp[20000 + row * 100 + c]['M']]
        return ', '.join(cols) or 'none'

    def _mirror_knobs(self):
        self.state['knob1'] = self.state['groups'][1]['level'] / 10
        self.state['knob2'] = self.state['groups'][2]['level'] / 10

    def power_cycle(self):
        """Ties, DSP, names and settings are nonvolatile: they survive mains off. The serial
        port gets the copyright banner when the unit is back."""
        with self.lock:
            saved = dict(self.state)
        super().power_cycle()
        with self.lock:
            self.state.update(saved)
            self.state['rebooting'] = False
        self.after('banner', self.faults['boot'] + 0.05, self._serial_banner)

    def _serial_banner(self):
        self.push(self._banner(), [link for link in self.links if link.kind == 'serial'])

    # ----- front panel
    def controls(self):
        n_in = self.inputs
        sources = list(range(0, n_in + 1))
        labels = {0: 'untied'}
        labels.update({i: 'In {}'.format(i) for i in range(1, n_in + 1)})
        c = [Readout('exe', 'Front panel lock mode'), Readout('rebooting', 'Rebooting')]
        for o in range(1, self.outputs + 1):
            c.append(Choice('vid%d' % o, 'Output %d video' % o, sources, labels))
            c.append(Choice('aud%d' % o, 'Output %d audio' % o, sources, labels))
        for i in range(1, n_in + 1):
            c.append(Toggle('sig%d' % i, 'Signal on input %d' % i))
        for p in SIZES[self.spec['size']]['ports']:
            c.append(Toggle('sink' + p, 'Display on output %s' % p.lstrip('0')))
        c += [Level('knob1', 'Volume (dB)', -100, 12), Level('knob2', 'Mic volume (dB)', -100, 12)]
        for m in range(1, 5):
            c.append(Level('mic%d' % m, 'Mic %d level (dBFS)' % m, -100, 0))
        for v in range(RETURNS_SHOWN):
            c.append(Level('vrg%d' % v, 'V. Return %s (dB)' % 'ABCD'[v], -100, 12))
            c.append(Toggle('vrm%d' % v, 'V. Return %s muted' % 'ABCD'[v]))
        for row in MIX_SHOWN_ROWS:
            if any(20000 + row * 100 + col in self.state['dsp'] for col in range(24)):
                c.append(Readout('mxr%d' % row, MIX_ROWS[row] + ' unmuted to', get=lambda row=row: self._mix_row(row)))
        rows = [r for r in range(44) if any(20000 + r * 100 + col in self.state['dsp'] for col in range(24))]
        c += [Choice('mx_row', 'Mix-point input', rows, {r: MIX_ROWS[r] for r in rows}),
              Choice('mx_col', 'Mix-point output', list(range(24)), {k: MIX_COLS[k] for k in range(24)}),
              Toggle('mx_mute', 'Mix-point muted')]
        c += [Level('temp', 'Temperature (°C)', 20, 80),
              Choice('preset_slot', 'Preset', list(range(1, 33))),
              Button('preset_save', 'Save preset'), Button('preset_recall', 'Recall preset'),
              Button('lock_cycle', 'Lock mode')]
        return c

    def panel(self, key, value):
        with self.lock:
            s = self.state
            if key in ('preset_save', 'preset_recall', 'lock_cycle', 'knob1', 'knob2') or \
                    key.startswith(('vid', 'aud')):
                if s['exe'] == 1 and key != 'lock_cycle':
                    self.log('info', 'front panel is locked (mode 1); ignored')
                    return
            if key.startswith(('vid', 'aud')):
                self.set(key, value)
                self._notify('Qik', front=True)
            elif key in ('knob1', 'knob2'):
                group = int(key[-1])
                level = int(round(value * 10))
                g = s['groups'][group]
                g['level'] = max(g['lower'], min(g['upper'], level))
                self._mirror_knobs()
                self._notify('GrpmD{}*{}'.format(group, g['level']), front=True)
            elif key in ('mx_row', 'mx_col'):
                self.set(key, value)
                self._mirror_dsp()
            elif key == 'mx_mute':
                oid = 20000 + s['mx_row'] * 100 + s['mx_col']
                if oid not in s['dsp']:
                    self.log('info', 'this model has no mix-point {} -> {}'.format(MIX_ROWS[s['mx_row']], MIX_COLS[s['mx_col']]))
                    self._mirror_dsp()
                    return
                s['dsp'][oid]['M'] = 1 if value else 0
                self._mirror_dsp()
                self._notify('DsM{}*{}'.format(oid, s['dsp'][oid]['M']), front=True)
            elif key.startswith(('vrg', 'vrm')):
                v = int(key[3:])
                block = s['dsp'][50100 + v]
                if key.startswith('vrg'):
                    block['G'] = max(-1000, min(120, int(round(value * 10))))
                    self._mirror_dsp()
                    self._notify('DsG{}*{}'.format(50100 + v, block['G']), front=True)
                else:
                    block['M'] = 1 if value else 0
                    self._mirror_dsp()
                    self._notify('DsM{}*{}'.format(50100 + v, block['M']), front=True)
            elif key == 'preset_save':
                s['presets'][s['preset_slot']] = self._snapshot()
                self.log('state', 'preset {} saved'.format(s['preset_slot']))
            elif key == 'preset_recall':
                if s['preset_slot'] in s['presets']:
                    self._recall(s['preset_slot'])
                    self._notify('Rpr{:02d}'.format(s['preset_slot']), front=True)
                else:
                    self.log('info', 'preset {} is not saved'.format(s['preset_slot']))
            elif key == 'lock_cycle':
                self.set('exe', (s['exe'] + 1) % 3)
                self._notify('Exe{}'.format(s['exe']), front=True)
            else:
                self.set(key, value)

    # ----- connections
    def _banner(self):
        return ('(c) Copyright 2016, Extron Electronics, {}, V{}, {}\r\n{}\r\n'.format(
            SIZES[self.spec['size']]['code'], self._firmware(), self.spec['part'],
            time.strftime('%a, %d %b %Y %H:%M:%S'))).encode()

    @staticmethod
    def _firmware():
        return '2.01'

    def _link_state(self, link):
        st = link.state
        if 'verbose' not in st:
            st.update(verbose=1 if link.kind == 'serial' else 0, echo=1 if link.kind == 'ssh' else 0,
                      auth='admin')
            if link.kind == 'tcp' and (self.opts['admin_password'] or self.opts['user_password']):
                st['auth'] = 'pending'
        return st

    def connected(self, link):
        st = self._link_state(link)
        if link.kind == 'ssh':
            st['auth'] = link.state.get('ssh_level', 'admin')
        banner = self._banner()
        return banner + (b'Password:' if st['auth'] == 'pending' else b'')

    def ssh_login(self, username, password):
        """SIS-over-SSH authenticates in SSH itself. Returns 'admin', 'user' or None."""
        admin, user = self.opts['admin_password'], self.opts['user_password']
        if not admin and not user:
            return 'admin'
        if admin and password == admin:
            return 'admin'
        if user and password == user:
            return 'user'
        return None

    def receive(self, link, data):
        self._framing_link = link
        try:
            super().receive(link, data)
        finally:
            self._framing_link = None

    def frames(self, buffer):
        link = self._framing_link
        out = []
        while buffer:
            if link is not None and link.state.get('auth') == 'pending':
                end = buffer.find(b'\r')
                if end < 0:
                    break
                out.append(buffer[:end].strip(b'\n'))
                buffer = buffer[end + 1:]
                continue
            first = buffer[:1]
            if first in b'\r\n \t\x00':
                buffer = buffer[1:]
                continue
            if first in (b'\x1b', b'W', b'w'):
                end = re.search(rb'[\r|]', buffer)
                if not end:
                    break
                out.append(buffer[:end.start()])
                buffer = buffer[end.end():]
                continue
            match = SIMPLE.match(buffer)
            if match:
                out.append(match.group(0))
                buffer = buffer[match.end():]
                continue
            if PARTIAL.match(buffer):
                break
            bad = re.match(rb'[0-9*+\-]*', buffer).end() + 1
            out.append(buffer[:bad])
            buffer = buffer[bad:]
        return out, buffer

    def _password(self, frame, link):
        text = frame.decode('latin-1')
        admin, user = self.opts['admin_password'], self.opts['user_password']
        if admin and text == admin:
            link.state['auth'] = 'admin'
            return b'\r\nLogin Administrator\r\n'
        if user and text == user:
            link.state['auth'] = 'user'
            return b'\r\nLogin User\r\n'
        return b'\r\nPassword:'

    def handle(self, frame, link):
        st = self._link_state(link)
        if st['auth'] == 'pending':
            return self._password(frame, link)
        self._cur, self._link, self._notices = st, link, []
        text = frame.decode('latin-1')
        try:
            if text[:1] in ('\x1b', 'W', 'w'):
                reply = self.escape(text[1:])
            elif SIMPLE.fullmatch(frame):
                reply = self.simple(text)
            else:
                err(10)
        except SisError as e:
            reply = str(e)
            self._notices = []
            self.log('info', 'SIS error {}'.format(reply), link)
        for notice in self._notices:
            self._notify(notice)
        out = self._lines(reply)
        if st['echo']:
            out = frame + b'\r\n' + out
        return out or None

    @staticmethod
    def _lines(reply):
        if reply is None:
            return b''
        if isinstance(reply, bytes):
            return reply
        if isinstance(reply, (list, tuple)):
            return b''.join(line.encode('latin-1') + b'\r\n' for line in reply)
        return reply.encode('latin-1') + b'\r\n'

    def _notify(self, text, front=False):
        """A change: every other connection in verbose mode 1 or 3 gets it."""
        data = text.encode('latin-1') + b'\r\n'
        this = None if front else getattr(self, '_link', None)
        targets = [link for link in self.links if link is not this
                   and self._link_state(link)['verbose'] in (1, 3) and link.state.get('auth') != 'pending']
        if targets:
            self.push(data, targets)

    def changed(self, text):
        self._notices.append(text)
        return text

    def read(self, tagged, bare):
        return tagged if self._cur['verbose'] in (2, 3) else bare

    def admin_only(self):
        if self._cur['auth'] != 'admin':
            err(24)

    # ----- helpers for numbers
    def _input(self, text, allow_zero=False):
        i = number(text, 1)
        if not (0 if allow_zero else 1) <= i <= self.inputs:
            err(1)
        return i

    def _output(self, text, allow_zero=False):
        o = number(text, 12)
        if not (0 if allow_zero else 1) <= o <= self.outputs:
            err(12)
        return o

    def _port(self, text):
        """An X& output connector: 01, 3A, 3b ... (a paired output without A/B is invalid)."""
        text = text.upper()
        for p in SIZES[self.spec['size']]['ports']:
            if text == p or (p[0] == '0' and text == p[1:]) or (len(text) == 3 and text[0] == '0' and text[1:] == p):
                return p
        err(12)

    @staticmethod
    def _short(port):
        return port.lstrip('0') or '0'

    def _scaled(self, text):
        o = number(text, 12)
        if o not in SIZES[self.spec['size']]['scaled']:
            err(12)
        return o

    def _port_output(self, port):
        return int(port[:-1]) if port[-1] in 'AB' else int(port)

    # ----- simple commands
    def simple(self, text):
        m = SIMPLE.fullmatch(text.encode('latin-1'))
        if m.group(2):
            return self._video_mute(m.group(1).decode())
        args, cmd = m.group(3).decode(), m.group(4).decode().upper()
        s = self.state
        if cmd in '!%$':
            return self._tie(args, cmd)
        if cmd == 'B':
            return self._video_mute(args)
        if cmd == 'F':
            if '*' in args:
                out, value = args.split('*', 1)
                o, v = self._scaled(out), number(value)
                if v not in (0, 1):
                    err(13)
                s['freeze'][o] = v
                return self.changed('Frz{:02d}*{:02d}'.format(o, v))
            o = self._scaled(args)
            return self.read('Frz{:02d}*{:02d}'.format(o, s['freeze'][o]), str(s['freeze'][o]))
        if cmd == 'X':
            if args:
                v = number(args)
                if v not in (0, 1, 2):
                    err(13)
                self.set('exe', v)
                return self.changed('Exe{}'.format(v))
            return self.read('Exe{}'.format(s['exe']), str(s['exe']))
        if cmd == 'Q':
            if args == '0':
                return '1.23-{0}(1.81LX-{1} -Fri, 31 Jul 2015 00:00:00 UTC)-{0}*(2.03LX-{1} -Fri, 30 Nov 2018 ' \
                       '16:39:21 UTC)'.format(self._firmware(), SIZES[self.spec['size']]['code'])
            if args:
                err(10)
            return self.read('Ver01*' + self._firmware(), self._firmware())
        if cmd == 'I':
            if args:
                err(10)
            return SIZES[self.spec['size']]['code']
        if cmd == 'N':
            if args:
                err(10)
            return self.read('Pno' + self.spec['part'], self.spec['part'])
        if cmd == 'S':
            if args:
                err(10)
            value = '12.250 {:.3f} 750000 750000'.format(s['temp'])
            return self.read('Sts00*' + value, value)
        if cmd == 'LS':
            if args != '0':
                err(10)
            value = ''.join('1' if s['sig%d' % i] else '0' for i in range(1, self.inputs + 1))
            return self.read('Frq00 ' + value, value)
        if cmd == '\\':
            if not args.endswith('*'):
                err(10)
            i = self._input(args[:-1])
            fmt = 2 if s['sig%d' % i] else 0
            return self.read('Ityp{:02d}*{}'.format(i, fmt), str(fmt))
        if cmd == 'A':
            if not args.endswith('*'):
                err(10)
            o = self._scaled(args[:-1])
            return self.changed('Img{:02d}'.format(o))
        if cmd == '.':
            parts = args.split('*')
            if len(parts) == 3 and parts[0] == '2':
                o, p = self._scaled(parts[1]), number(parts[2])
                if not 1 <= p <= 128 or (o, p) not in s['scaler_presets']:
                    err(13)
                for key, value in s['scaler_presets'][(o, p)].items():
                    s['scaler'][key][o] = value
                return self.changed('2Rpr{}*{:03d}'.format(o, p))
            if len(parts) != 1:
                err(10)
            p = number(args, 11)
            if not 1 <= p <= 32:
                err(11)
            if p not in s['presets']:
                err(11)
            self._recall(p)
            reply = 'PrstR{:02d}'.format(p) if self.opts['preset_reply'] == PRESET_REPLIES[0] else 'Rpr{:02d}'.format(p)
            return self.changed(reply)
        if cmd == ',':
            parts = args.split('*')
            if len(parts) != 3 or parts[0] != '2':
                err(10)
            o, p = self._scaled(parts[1]), number(parts[2])
            if not 1 <= p <= 128:
                err(13)
            s['scaler_presets'][(o, p)] = {key: s['scaler'][key].get(o) for key in s['scaler']}
            return self.changed('2Spr{}*{:03d}'.format(o, p))
        if cmd == '#':
            if args != '51':
                err(10)
            value = ''.join('1' if any(p == n for (_, p) in s['scaler_presets']) else '0' for n in range(1, 129))
            return self.read('PreI' + value, value)
        err(10)

    def _tie(self, args, cmd):
        s = self.state
        kind = {'!': 'All', '%': 'Vid', '$': 'Aud'}[cmd]
        if '*' not in args:
            if not args:
                err(10)
            o = self._output(args)
            if cmd == '!':
                err(10)
            value = s[('vid' if cmd == '%' else 'aud') + str(o)]
            return self.read('Out{:02d} In{:02d} {}'.format(o, value, kind), '{:02d}'.format(value))
        inp, out = args.split('*', 1)
        i = self._input(inp, allow_zero=True)
        if out == '':
            for o in range(1, self.outputs + 1):
                self._apply_tie(i, o, cmd)
            return self.changed('In{:02d} {}'.format(i, kind))
        o = self._output(out)
        self._apply_tie(i, o, cmd)
        return self.changed('Out{:02d} In{:02d} {}'.format(o, i, kind))

    def _apply_tie(self, i, o, cmd):
        if cmd in '!%':
            self.set('vid%d' % o, i)
        if cmd in '!$':
            self.set('aud%d' % o, i)

    def _video_mute(self, args):
        s = self.state
        if args.endswith('*') and args.count('*') == 1:
            v = number(args[:-1])
            if v not in (0, 1, 2):
                err(13)
            for p in s['vmute']:
                s['vmute'][p] = v
            self.log('state', 'all video mutes -> {}'.format(v))
            return self.changed('Vmt{}'.format(v))
        if '*' in args:
            port, value = args.split('*', 1)
            p, v = self._port(port), number(value)
            if v not in (0, 1, 2):
                err(13)
            s['vmute'][p] = v
            self.log('state', 'video mute {} -> {}'.format(p, v))
            return self.changed('Vmt{}*{}'.format(self._short(p), v))
        p = self._port(args)
        return self.read('Vmt{}*{}'.format(self._short(p), s['vmute'][p]), str(s['vmute'][p]))

    # ----- presets
    def _snapshot(self):
        s = self.state
        return {'vid': {o: s['vid%d' % o] for o in range(1, self.outputs + 1)},
                'aud': {o: s['aud%d' % o] for o in range(1, self.outputs + 1)},
                'dsp': copy.deepcopy(s['dsp'])}

    def _recall(self, p):
        preset = self.state['presets'][p]
        for o in range(1, self.outputs + 1):
            self.set('vid%d' % o, preset['vid'][o])
            self.set('aud%d' % o, preset['aud'][o])
        self.state['dsp'] = copy.deepcopy(preset['dsp'])
        self._mirror_dsp()
        self.log('state', 'preset {} recalled'.format(p))

    # ----- escape commands
    def escape(self, body):
        upper = body.upper()
        if upper.startswith('+Q'):
            return self._quick_tie(upper[2:])
        for key in KEYWORDS:
            if upper.endswith(key):
                args = body[:-len(key)]
                return getattr(self, 'esc_' + key.lower(), self.esc_scaler)(args, key)
        err(10)

    def _quick_tie(self, body):
        ties = re.findall(r'(\d+)\*(\d+)([!%$])', body)
        if not ties or re.sub(r'(\d+)\*(\d+)[!%$]', '', body):
            err(10)
        parsed = [(self._input(i, allow_zero=True), self._output(o), c) for i, o, c in ties]
        for i, o, c in parsed:
            self._apply_tie(i, o, c)
        return self.changed('Qik')

    # DSP (pp. 158-166)
    def esc_au(self, args, key):
        m = re.fullmatch(r'([GHMZVgmhzv])(\d{5})(?:\*([+-]?\d+))?', args)
        if not m:
            err(10)
        letter, oid, value = m.group(1).upper(), int(m.group(2)), m.group(3)
        s = self.state
        if letter == 'Z':
            if not 40000 <= oid <= 40003:
                err(14)
            m_ = oid - 40000
            if value is not None:
                v = number(value)
                if v not in (0, 1):
                    err(13)
                s['phantom'][m_] = v
                return self.changed('DsZ{}*{}'.format(oid, v))
            return self.read('DsZ{}*{}'.format(oid, s['phantom'][m_]), '{}*{}'.format(oid, s['phantom'][m_]))
        if letter == 'V':
            if not 40000 <= oid <= 40003:
                err(14)
            m_ = oid - 40000
            if value is not None:
                v = number(value)
                if v not in (0, 1):
                    err(13)
                s['meter'][m_] = v
                return 'DsV{}*{}'.format(oid, v)
            level = abs(int(round(s['mic%d' % (m_ + 1)] * 10)))
            return self.read('DsV{}*{}*{}'.format(oid, s['meter'][m_], level), str(-level))
        oids = self._oids()
        if oid not in oids:
            err(14)
        lo, hi, has_mute = oids[oid]
        block = s['dsp'][oid]
        if letter == 'M':
            if not has_mute:
                err(14)
            if value is not None:
                v = number(value)
                if v not in (0, 1):
                    err(13)
                block['M'] = v
                self._mirror_dsp()
                self.log('state', 'DSP {} mute -> {}'.format(oid, v))
                return self.changed('DsM{}*{}'.format(oid, v))
            return self.read('DsM{}*{}'.format(oid, block['M']), str(block['M']))
        slot = 'H' if letter == 'H' and 'H' in block else 'G'
        if value is not None:
            v = number(value)
            if not lo <= v <= hi:
                err(13)
            block[slot] = v
            self._mirror_dsp()
            self.log('state', 'DSP {} {} -> {}'.format(oid, slot, v))
            return self.changed('Ds{}{}*{}'.format(letter, oid, v))
        return self.read('Ds{}{}*{}'.format(letter, oid, block[slot]), str(block[slot]))

    def esc_grpm(self, args, key):
        s = self.state
        up = args.upper()
        m = re.fullmatch(r'D(\d{1,2})(?:\*([+-]?\d+)([+-])?)?', up)
        if m:
            g = self._group(m.group(1))
            grp = s['groups'][g]
            is_mute = g >= self.opts['first_mute_group']
            if m.group(2) is None:
                value = grp['mute'] if is_mute else grp['level']
                return self.read('GrpmD{}*{}'.format(g, value), str(value))
            v = number(m.group(2))
            if is_mute:
                if m.group(3) or v not in (0, 1):
                    err(13)
                grp['mute'] = v
                value = v
            else:
                if m.group(3):
                    step = abs(v) if m.group(3) == '+' else -abs(v)
                    level = max(grp['lower'], min(grp['upper'], grp['level'] + step))
                elif not grp['lower'] <= v <= grp['upper']:
                    err(13)
                else:
                    level = v
                grp['level'] = value = level
                self._mirror_knobs()
            self.log('state', 'group {} -> {}'.format(g, value))
            return self.changed('GrpmD{}*{}'.format(g, value))
        m = re.fullmatch(r'L(\d{1,2})(?:\*([+-]?\d+)\*([+-]?\d+))?', up)
        if m:
            g = self._group(m.group(1))
            grp = s['groups'][g]
            if m.group(2) is not None:
                upper, lower = number(m.group(2)), number(m.group(3))
                if not -1000 <= lower <= upper <= 800:
                    err(13)
                grp['upper'], grp['lower'] = upper, lower
                return self.changed('GrpmL{}*{}*{}'.format(g, upper, lower))
            value = '{}*{}'.format(grp['upper'], grp['lower'])
            return self.read('GrpmL{}*{}'.format(g, value), value)
        m = re.fullmatch(r'([PO])(\d{1,2})', up)
        if m:
            g = self._group(m.group(2))
            if m.group(1) == 'P':
                kind = 12 if g >= self.opts['first_mute_group'] else 6
                return self.read('GrpmP{}*{}'.format(g, kind), str(kind))
            return self.read('GrpmO{}*'.format(g), '')
        err(10)

    @staticmethod
    def _group(text):
        g = number(text)
        if not 1 <= g <= 32:
            err(13)
        return g

    # Ties and mutes
    def esc_vc(self, args, key):
        m = re.fullmatch(r'(\d+)\*(\d+)\*([12])', args)
        if not m:
            err(10)
        preset, start, which = int(m.group(1)), number(m.group(2)), m.group(3)
        if not 1 <= start <= self.outputs:
            err(12)
        if preset:
            if preset not in self.state['presets']:
                err(11)
            ties = self.state['presets'][preset]['vid' if which == '1' else 'aud']
        else:
            ties = {o: self.state[('vid' if which == '1' else 'aud') + str(o)] for o in range(1, self.outputs + 1)}
        entries = ['{:02d}'.format(ties[o]) for o in range(start, self.outputs + 1)]
        entries += ['--'] * (16 - len(entries))
        value = ' '.join(entries) + ('Vid' if which == '1' else 'Aud')
        return self.read('Vgp{:02d} Out{:02d}*{}'.format(preset, start, value), value)

    def esc_vm(self, args, key):
        if args:
            err(10)
        value = ''.join(str(v) for v in self.state['vmute'].values())
        return self.read('Mut' + value, value)

    # EDID, audio format, HDCP, formats
    def esc_edid(self, args, key):
        s, size = self.state, SIZES[self.spec['size']]
        up = args.upper()
        m = re.fullmatch(r'A(\d+)\*(\d+)', up)
        if m:
            i, v = self._input(m.group(1)), number(m.group(2))
            if not 1 <= v <= size['edid_max']:
                err(13)
            s['edid'][i] = v
            return self.changed('EdidA{:02d}*{:02d}'.format(i, v))
        m = re.fullmatch(r'A(\d+)\*', up)
        if m:
            v = number(m.group(1))
            if not 1 <= v <= size['edid_max']:
                err(13)
            for i in s['edid']:
                s['edid'][i] = v
            return self.changed('EdidA{:02d}'.format(v))
        m = re.fullmatch(r'A(\d+)', up)
        if m:
            i = self._input(m.group(1))
            return self.read('EdidA{:02d}*{:02d}'.format(i, s['edid'][i]), '{:02d}'.format(s['edid'][i]))
        m = re.fullmatch(r'S(\w+)\*(\d+)', up)
        if m:
            p, v = self._port(m.group(1)), number(m.group(2))
            if not size['edid_max'] - 9 <= v <= size['edid_max']:
                err(13)
            return self.changed('EdidS{}*{}'.format(self._short(p), v))
        m = re.fullmatch(r'([IE])(\d+),(.+)', args, re.I)
        if m:
            v = number(m.group(2))
            if not size['edid_max'] - 9 <= v <= size['edid_max']:
                err(13)
            if m.group(1).upper() == 'I' and m.group(3) not in s['files']:
                err(28)
            return '{}{}{}'.format('Edid', m.group(1).upper(), v)
        err(10)

    def esc_afmt(self, args, key):
        s = self.state
        up = args.upper()
        m = re.fullmatch(r'([IO])(\d+)?(?:\*(\d))?', up)
        if not m:
            err(10)
        side, index, value = m.groups()
        table = s['afmt_in'] if side == 'I' else s['afmt_out']
        if index is None:
            if value is not None:
                err(10)
            digits = ''.join(str(v) for v in table.values())
            return self.read('Afmt{}{}'.format(side, digits), digits)
        n = self._input(index) if side == 'I' else self._output(index)
        if value is not None:
            v = number(value)
            if v not in (0, 1, 2):
                err(13)
            table[n] = v
            return self.changed('Afmt{}{:02d}*{}'.format(side, n, v))
        return self.read('Afmt{}{:02d}*{}'.format(side, n, table[n]), str(table[n]))

    def esc_stat(self, args, key):
        m = re.fullmatch(r'40\*(\d+)', args)
        if not m:
            err(10)
        i = self._input(m.group(1))
        fmt = 1 if self.state['sig%d' % i] else 0
        return self.read('40Stat{:02d}*{}'.format(i, fmt), str(fmt))

    def esc_hdcp(self, args, key):
        s = self.state
        up = args.upper()
        m = re.fullmatch(r'([ESNIO])(\w*?)(?:\*(\d))?', up)
        if not m:
            err(10)
        side, index, value = m.groups()
        if side == 'E':
            i = self._input(index)
            if value is not None:
                v = number(value)
                if v not in (0, 1):
                    err(13)
                s['hdcp_auth'][i] = v
                return self.changed('HdcpE{:02d}*{}'.format(i, v))
            return self.read('HdcpE{:02d}*{}'.format(i, s['hdcp_auth'][i]), str(s['hdcp_auth'][i]))
        if side in 'SN':
            table, top = (s['hdcp_mode'], 3) if side == 'S' else (s['hdcp_notify'], 1)
            p = self._port(index)
            if value is not None:
                v = number(value)
                if not 0 <= v <= top:
                    err(13)
                table[p] = v
                return self.changed('Hdcp{}{}*{}'.format(side, self._short(p), v))
            return self.read('Hdcp{}{}*{}'.format(side, self._short(p), table[p]), str(table[p]))
        if value is not None:
            err(10)
        if side == 'I':
            status = {i: (1 if s['sig%d' % i] else 0) for i in range(1, self.inputs + 1)}
            if not index:
                return ''.join(str(v) for v in status.values())
            i = self._input(index)
            return self.read('HdcpI{:02d}*{}'.format(i, status[i]), str(status[i]))
        status = {p: self._hdcp_out(p) for p in SIZES[self.spec['size']]['ports']}
        if not index:
            return ''.join(str(v) for v in status.values())
        p = self._port(index)
        return self.read('HdcpO{}*{}'.format(self._short(p), status[p]), str(status[p]))

    def _hdcp_out(self, port):
        s = self.state
        if not s['sink' + port]:
            return 0
        tied = s['vid%d' % self._port_output(port)]
        return 3 if tied and s['sig%d' % tied] else 2

    def esc_vtpo(self, args, key):
        return self._port_setting(args, 'vtpo', 'Vtpo', range(0, 8))

    def esc_bitd(self, args, key):
        s = self.state
        m = re.fullmatch(r'(\d+)(?:\*(\d))?', args)
        if not m:
            err(10)
        o = self._output(m.group(1))
        if m.group(2) is not None:
            v = number(m.group(2))
            if v not in (0, 1):
                err(13)
            s['bitd'][o] = v
            return self.changed('Bitd{:02d}*{}'.format(o, v))
        return self.read('Bitd{:02d}*{}'.format(o, s['bitd'][o]), str(s['bitd'][o]))

    def _port_setting(self, args, table_key, tag, values):
        table = self.state[table_key]
        m = re.fullmatch(r'(\w+?)(?:\*(\d))?', args)
        if not m:
            err(10)
        p = self._port(m.group(1))
        if m.group(2) is not None:
            v = number(m.group(2))
            if v not in values:
                err(13)
            table[p] = v
            return self.changed('{}{}*{}'.format(tag, self._short(p), v))
        return self.read('{}{}*{}'.format(tag, self._short(p), table[p]), str(table[p]))

    def esc_hdbt(self, args, key):
        m = re.fullmatch(r'([IO])(\d+)', args.upper())
        if not m:
            err(10)
        n = number(m.group(2))
        size = SIZES[self.spec['size']]
        valid = size['dtp_inputs'] if m.group(1) == 'I' else [o for (k, o) in size['insertion'].values() if k == 'O']
        if n not in valid:
            err(1 if m.group(1) == 'I' else 12)
        return self.read('Hdbt{}{:02d}*0'.format(m.group(1), n), '0')

    # Names
    def esc_ni(self, args, key):
        return self._name(args, 'names_in', 'i', self._input)

    def esc_no(self, args, key):
        return self._name(args, 'names_out', 'o', self._output)

    def _name(self, args, table_key, letter, check):
        table = self.state[table_key]
        if ',' in args:
            index, name = args.split(',', 1)
            n = check(index)
            if not NAME_CHARS.match(name):
                err(13)
            table[n] = name
            return self.changed('Nm{}{:02d},{}'.format(letter, n, name))
        n = check(args)
        return self.read('Nm{}{},{}'.format(letter, n, table[n]), table[n])

    def esc_unam(self, args, key):
        s, size = self.state, SIZES[self.spec['size']]
        m = re.fullmatch(r'([EL])(\d+)(?:([*,])(.*))?', args, re.I)
        if not m:
            err(10)
        which, n = m.group(1).upper(), number(m.group(2))
        if which == 'E':
            table = s['edid_names']
            if not size['edid_max'] - 9 <= n <= size['edid_max']:
                err(13)
            default = 'USER {}'.format(n - size['edid_max'] + 10)
        else:
            table = s['logo_names']
            if not 1 <= n <= 16:
                err(13)
            default = ''
        if m.group(3):
            name = m.group(4)
            if which == 'E' and name == ' ':
                table.pop(n, None)
                return self.changed('UnamE{}*{}'.format(n, default))
            if not NAME_CHARS.match(name):
                err(13)
            table[n] = name
            return self.changed('Unam{}{}{}{}'.format(which, n, m.group(3), name))
        return table.get(n, default)

    def esc_pnam(self, args, key):
        m = re.fullmatch(r'2\*(\d+)(?:,(.*))?', args)
        if not m:
            err(10)
        p = number(m.group(1))
        if not 1 <= p <= 128:
            err(13)
        if m.group(2) is not None:
            if not NAME_CHARS.match(m.group(2)):
                err(13)
            self.state['preset_names'][p] = m.group(2)
            return self.changed('Pnam2*{:03d},{}'.format(p, m.group(2)))
        return self.state['preset_names'].get(p, 'Preset {}'.format(p))

    def esc_cn(self, args, key):
        if args:
            self.admin_only()
            self.state['matrix_name'] = args
            return self.changed('Ipn ' + args)
        return self.read('Ipn ' + self.state['matrix_name'], self.state['matrix_name'])

    # Resets and system
    def esc_zg(self, args, key):
        self.admin_only()
        if args:
            p = number(args, 11)
            if not 1 <= p <= 32:
                err(11)
            self.state['presets'].pop(p, None)
            return self.changed('Zpg{:02d}'.format(p))
        self.state['presets'].clear()
        return self.changed('Zpg')

    def _clear_ties(self):
        for o in range(1, self.outputs + 1):
            self.set('vid%d' % o, 0)
            self.set('aud%d' % o, 0)

    def esc_zxxx(self, args, key):
        self.admin_only()
        if args:
            err(10)
        self._clear_ties()
        self.state['presets'].clear()
        for g in (1, 2):
            self.state['groups'][g].update(level=0, mute=0)
        self._mirror_knobs()
        return self.changed('Zpx')

    def esc_zy(self, args, key):
        self.admin_only()
        if args:
            err(10)
        keep = {k: self.state[k] for k in ('ip', 'prefix', 'gateway', 'matrix_name', 'telnet_port', 'web_port',
                                             'ssh_port', 'ssl_port')}
        self.reset()
        self.state.update(keep)
        return self.changed('Zpy')

    def esc_zqqq(self, args, key):
        self.admin_only()
        if args:
            err(10)
        self.reset()
        self.reboot()
        return 'Zpq'

    def esc_boot(self, args, key):
        self.admin_only()
        if args != '1':
            err(10)
        self.reboot()
        return 'Boot 1'

    def reboot(self):
        """The unit restarts: every connection drops, and it's unreachable for the reboot time."""
        seconds = self.opts['reboot_seconds']
        if not seconds:
            return
        self.set('rebooting', True)
        self.booting = True
        self.after('lan', 0.2, self._apply_offline)

        def done():
            self.set('rebooting', False)
            self.booting = False
            self._apply_offline()
            self._serial_banner()
        self.after('reboot', seconds, done)

    def esc_df(self, args, key):
        if args:
            err(10)
        files = self.state['files']
        lines = ['{} {} {}'.format(name, date, size) for name, (date, size) in files.items()]
        return lines + ['{} Bytes Left'.format(1048576 - sum(size for _, size in files.values())), '']

    def esc_ef(self, args, key):
        if args not in self.state['files']:
            err(28)
        del self.state['files'][args]
        return 'Del ' + args

    # Scaler controls
    def esc_scaler(self, args, key):
        index_kind, values, default, tag, pad_i, pad_v, steps = SCALER[key]
        table = self.state['scaler'][key]
        m = re.fullmatch(r'(\d+)(?:(\*)([+-]?\d+)|([+\-–]))?', args)
        if not m:
            err(10)
        if index_kind == 'input':
            n = self._input(m.group(1))
        elif index_kind == 'scaled':
            n = self._scaled(m.group(1))
        else:   # VKEF: the 86/108 module keys it by output 1-8, the 82/84 module by logo 1-16
            n = number(m.group(1), 12)
            if not 1 <= n <= 16:
                err(12)
        current = table.get(n, default)
        idx = '{:0{}d}'.format(n, pad_i)
        if m.group(2):
            v = number(m.group(3))
        elif m.group(4):
            if not steps:
                err(10)
            v = current + (1 if m.group(4) == '+' else -1)
            v = max(min(values), min(max(values), v))
        else:
            return self.read('{}{}*{}'.format(tag, idx, self._fmt(current, pad_v)), self._fmt(current, pad_v))
        if v not in values:
            err(13)
        table[n] = v
        return self.changed('{}{}*{}'.format(tag, idx, self._fmt(v, pad_v)))

    @staticmethod
    def _fmt(value, pad):
        return '{:0{}d}'.format(value, pad)

    def esc_hsiz(self, args, key):
        return self._size(args, 0, 'Hsiz')

    def esc_vsiz(self, args, key):
        return self._size(args, 1, 'Vsiz')

    def _size(self, args, axis, tag):
        m = re.fullmatch(r'(\d+)(?:\*(\d+)|([+-]))?', args)
        if not m:
            err(10)
        o = self._scaled(m.group(1))
        current = list(self.state['size_img'][o])
        if m.group(2) is None and m.group(3) is None:
            return self.read('{}{:02d}*{}'.format(tag, o, current[axis]), str(current[axis]))
        v = number(m.group(2)) if m.group(2) is not None else current[axis] + (1 if m.group(3) == '+' else -1)
        if not 0 <= v <= 10240:
            err(13)
        current[axis] = v
        self.state['size_img'][o] = tuple(current)
        return self.changed('{}{:02d}*{}'.format(tag, o, v))

    def esc_ximg(self, args, key):
        m = re.fullmatch(r'(\d+)(?:,([+-]?\d+)\*([+-]?\d+)\*(\d+)\*(\d+))?', args)
        if not m:
            err(10)
        o = self._scaled(m.group(1))
        scaler = self.state['scaler']
        if m.group(2) is not None:
            h, v, w, t = (number(x) for x in m.group(2, 3, 4, 5))
            if not (-10240 <= h <= 10240 and -10240 <= v <= 10240 and 0 <= w <= 10240 and 0 <= t <= 10240):
                err(13)
            scaler['HCTR'][o], scaler['VCTR'][o] = h, v
            self.state['size_img'][o] = (w, t)
        h, v = scaler['HCTR'].get(o, 0), scaler['VCTR'].get(o, 0)
        w, t = self.state['size_img'][o]
        value = '{:+06d}*{:+06d}*{:05d}*{:05d}'.format(h, v, w, t)
        if m.group(2) is not None:
            return self.changed('Ximg{},{}'.format(o, value))
        return self.read('Ximg{},{}'.format(o, value), value)

    def esc_apix(self, args, key):
        o = self._scaled(args)
        value = RATES[self.state['scaler']['RATE'].get(o, 34)][0]
        return self.read('Apix{:02d}*{}'.format(o, value), str(value))

    def esc_alin(self, args, key):
        o = self._scaled(args)
        value = RATES[self.state['scaler']['RATE'].get(o, 34)][1]
        return self.read('Alin{:02d}*{}'.format(o, value), str(value))

    def esc_prst(self, args, key):
        m = re.fullmatch(r'(\d+)\*(\d+)', args)
        if not m:
            err(10)
        o, p = self._scaled(m.group(1)), number(m.group(2))
        if not 1 <= p <= 128:
            err(13)
        self.state['scaler_presets'].pop((o, p), None)
        return self.changed('Prst{}*{:03d}'.format(o, p))

    def esc_ssav(self, args, key):
        m = re.fullmatch(r'([TS])(\d+)(?:\*(\d+))?', args.upper())
        if not m:
            err(10)
        o = self._scaled(m.group(2))
        s = self.state
        if m.group(1) == 'S':
            if m.group(3) is not None:
                err(10)
            tied = s['vid%d' % o]
            status = 0 if tied and s['sig%d' % tied] else (2 if s['ssav'][o] == 0 else 1)
            return self.read('SsavS{}*{}'.format(o, status), str(status))
        if m.group(3) is not None:
            v = number(m.group(3))
            if not 0 <= v <= 501:
                err(13)
            s['ssav'][o] = v
            return self.changed('SsavT{}*{:03d}'.format(o, v))
        return self.read('SsavT{}*{:03d}'.format(o, s['ssav'][o]), '{:03d}'.format(s['ssav'][o]))

    # Logos (p. 73; the list and file assignment follow the catalog modules)
    def esc_logo(self, args, key):
        s = self.state
        up = args.upper()
        if up == 'Q':
            avail = ''.join('1' if n in s['logo_files'] else '0' for n in range(1, 17))
            return 'LogoQ00*{}*{}'.format(avail, avail)
        m = re.fullmatch(r'A(\d+),(.+)', args, re.I)
        if m:
            n = number(m.group(1))
            if not 1 <= n <= 16:
                err(13)
            s['logo_files'][n] = m.group(2)
            return self.changed('LogoA{},{}'.format(n, m.group(2)))
        if up == 'E':
            scaled = SIZES[self.spec['size']]['scaled']
            values = ['00'] * (self.outputs - len(scaled)) + ['{:02d}'.format(s['logos'].get(o, 0)) for o in scaled]
            return ' '.join(values)
        m = re.fullmatch(r'E(\d+)(?:\*(\d+))?', up)
        if not m:
            err(10)
        o = self._scaled(m.group(1))
        if m.group(2) is not None:
            n = number(m.group(2))
            if not 0 <= n <= 16:
                err(13)
            s['logos'][o] = n
            return self.changed('LogoE{}*{}'.format(o, n))
        n = s['logos'].get(o, 0)
        return self.read('LogoE{}*{}'.format(o, n), str(n))

    # RS-232 insertion and ports (pp. 74-77)
    def esc_lrpt(self, args, key):
        s = self.state
        m = re.fullmatch(r'([IO])(\d*)(\*)?(\d)?', args.upper())
        if not m:
            err(10)
        side, index, star, value = m.groups()
        table = s['lrpt_in'] if side == 'I' else s['lrpt_out']
        if index and star and value is None:        # EIX4$*LRPT: every insert on this side
            v = number(index)
            if v not in (0, 1):
                err(13)
            for n in table:
                table[n] = v
            return self.changed('Lrpt{}{}'.format(side, v))
        if not index:
            if star:
                err(10)
            return ''.join(str(v) for v in table.values())
        n = number(index)
        if n not in table:
            err(1 if side == 'I' else 12)
        if star:
            v = number(value)
            if v not in (0, 1):
                err(13)
            table[n] = v
            return self.changed('Lrpt{}{:02d}*{}'.format(side, n, v))
        return self.read('Lrpt{}{:02d}*{}'.format(side, n, table[n]), str(table[n]))

    def _serial_port(self, text):
        n = number(text)
        if n not in self.state['cp']:
            err(13)
        return n

    def esc_cp(self, args, key):
        m = re.fullmatch(r'(\d+)(?:\*(\d+),([OENMS]),([78]),([12]))?', args, re.I)
        if not m:
            err(10)
        n = self._serial_port(m.group(1))
        if m.group(2) is not None:
            baud = number(m.group(2))
            valid = (300, 600, 1200, 1800, 2400, 3600, 4800, 7200, 9600, 14400, 19200, 28800, 38400, 57600, 115200)
            if baud not in (valid if n > 1 else (9600, 19200, 38400, 115200)):
                err(13)
            value = '{},{},{},{}'.format(baud, m.group(3).lower(), m.group(4), m.group(5))
            self.state['cp'][n] = value
            return self.changed('Cpn{:02d} Ccp{}'.format(n, value))
        return self.read('Cpn{:02d} Ccp{}'.format(n, self.state['cp'][n]), self.state['cp'][n])

    def esc_cf(self, args, key):
        m = re.fullmatch(r'(\d+)(?:\*([HSN]),(\d+))?', args, re.I)
        if not m:
            err(10)
        n = self._serial_port(m.group(1))
        if m.group(2) is not None:
            pacing = number(m.group(3))
            if not 0 <= pacing <= 1000:
                err(13)
            self.state['cf'][n] = '{},{:04d}'.format(m.group(2).upper(), 0 if n == 1 else pacing)
            return self.changed('Cpn{:02d} Cfl{}'.format(n, self.state['cf'][n]))
        return self.state['cf'].get(n, 'N,0000')

    def esc_ce(self, args, key):
        m = re.fullmatch(r'(\d+)(?:\*(\d+),(\d+))?', args)
        if not m:
            err(10)
        n = self._serial_port(m.group(1))
        if m.group(2) is not None:
            wait, between = number(m.group(2)), number(m.group(3))
            if not (0 <= wait <= 32767 and 0 <= between <= 32767) or (wait == 0) != (between == 0):
                err(13)
            self.state['ce'][n] = '{},{},0,00000L'.format(wait, between)
            return self.changed('Cpn{:02d} Cce{}'.format(n, self.state['ce'][n]))
        return self.state['ce'].get(n, '10,2,0,00000L')

    def esc_md(self, args, key):
        if args:
            self.admin_only()
            v = number(args)
            if not 1024 <= v <= 65000:
                err(13)
            self.state['uart_start'] = v
            return self.changed('Pmd{}'.format(v))
        return str(self.state['uart_start'])

    def esc_tc(self, args, key):
        m = re.fullmatch(r'([01])(?:\*(\d+))?', args)
        if not m:
            err(10)
        which = m.group(1)
        current = self._cur.get('timeout', self.state['port_timeout']) if which == '0' else self.state['port_timeout']
        if m.group(2) is None:
            return self.read('Pti{}*{}'.format(which, current), str(current))
        v = number(m.group(2))
        if not 1 <= v <= 65000:
            err(13)
        if which == '1':
            self.state['port_timeout'] = v
            self.tcp_idle_timeout = v * 10
        else:
            self._cur['timeout'] = v
        return self.changed('Pti{}*{}'.format(which, v))

    def esc_cv(self, args, key):
        if args:
            v = number(args)
            if v not in (0, 1, 2, 3):
                err(13)
            self._cur['verbose'] = v
            return 'Vrb{}'.format(v)
        v = self._cur['verbose']
        return self.read('Vrb{}'.format(v), str(v))

    def esc_echo(self, args, key):
        if self._link.kind != 'ssh':
            err(14)
        if args:
            v = number(args)
            if v not in (0, 1):
                err(13)
            self._cur['echo'] = v
            return 'Echo{}'.format(v)
        v = self._cur['echo']
        return self.read('Echo{}'.format(v), str(v))

    def _password_cmd(self, args, option, tag):
        if args:
            self.admin_only()
            if args == ' ':
                self.opts[option] = ''
                return tag + ' '
            if '|' in args:
                err(13)
            self.opts[option] = args
            return '{} {}'.format(tag, args)
        return '****' if self.opts[option] else ' '

    def esc_ca(self, args, key):
        return self._password_cmd(args, 'admin_password', 'Ipa')

    def esc_cu(self, args, key):
        return self._password_cmd(args, 'user_password', 'Ipu')

    def esc_tzon(self, args, key):
        zones = {'CST': '(UTC-06:00) Central Time', 'EST': '(UTC-05:00) Eastern Time',
                 'MST': '(UTC-07:00) Mountain Time', 'PST': '(UTC-08:00) Pacific Time', 'GMT': '(UTC) Greenwich'}
        if args == '*':
            return ['{}*{}'.format(k, v) for k, v in zones.items()] + ['']
        if args:
            self.admin_only()
            zone = args.rstrip('*').upper()
            if zone not in zones:
                err(13)
            self.state['tz'] = zone
            return self.changed('Tzon{}*{}'.format(zone, zones[zone]))
        return '{}*{}'.format(self.state['tz'], zones[self.state['tz']])

    def esc_cisg(self, args, key):
        self.admin_only()
        m = re.fullmatch(r'1\*(\d+\.\d+\.\d+\.\d+)/(\d+)\*(\d+\.\d+\.\d+\.\d+)', args)
        if not m:
            err(10)
        prefix = number(m.group(2))
        if not 1 <= prefix <= 32:
            err(13)
        self.state.update(ip=m.group(1), prefix=prefix, gateway=m.group(3))
        self.reboot()
        return 'Cisg1*{}/{}*{}'.format(m.group(1), prefix, m.group(3))

    def _port_map(self, args, key, tag, default):
        if args == '':
            return str(self.state[key])
        self.admin_only()
        v = number(args)
        if v not in (0, default) and v < 1024:
            err(13)
        others = [self.state[k] for k in ('telnet_port', 'web_port', 'ssh_port', 'ssl_port') if k != key]
        if v and v in others:
            err(13)
        self.state[key] = v
        return self.changed('{}{:05d}'.format(tag, v))

    def esc_mt(self, args, key):
        return self._port_map(args, 'telnet_port', 'Pmt', 23)

    def esc_mh(self, args, key):
        return self._port_map(args, 'web_port', 'Pmh', 80)

    def esc_pmap(self, args, key):
        m = re.fullmatch(r'([BS])(\d*)', args.upper())
        if not m:
            err(10)
        if m.group(1) == 'B':
            return self._port_map(m.group(2), 'ssh_port', 'PmapB', 22023)
        return self._port_map(m.group(2), 'ssl_port', 'PmapS', 443)
