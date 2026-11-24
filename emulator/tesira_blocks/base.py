"""
The engine every Tesira block type runs on.

A block type is its attribute table, pasted from Biamp's Tesira Text Protocol attribute tables,
plus the instance parameters a design gives it and the few defaults the table can't express.
`BlockType` parses the table into one `Attr` per row. `Block` is one instance in a design (an
instance tag), and does get / set / increment / decrement / toggle with the table's index and
range rules for every type. A type only adds code (a `BlockType` subclass overriding `write`)
where the real block has rules its table can't express.

Values live in the device's state dict under flat keys, `'<tag> <attr> <index> <index>'`, so the
emulator UI can bind a control straight to one.

How a table row becomes an `Attr`:

    | Level | level | get / set / increment / decrement / subscribe / unsubscribe | channel | minLevel - maxLevel dB |

    commands  the Command column
    indexes   the Indexes column, lower-cased ('channel'; 'input', 'output'; 'wall number')
    kind      from Value Range: 'bool' (false, true), 'enum' (PEAK, RMS), 'float' / 'int' (a
              "lo - hi unit" range; float when a bound has a decimal point or the unit is dB),
              'string' (labels), 'array' (a plural attribute: see below) or 'opaque' (anything
              else: served from the type's defaults, read-only)
    lo, hi    range bounds: numbers, or the code of another attribute at the same index
              ('minLevel - maxLevel dB'), or a parameter name when the type says so

A **plural attribute** (`levels`, `mutes`, `states`, `presents`) is a row with no index whose code
is another row's code plus `s`, where that row has exactly one index. It reads and publishes the
whole block as one list.
"""
import re
import struct


class TtpError(Exception):
    """A command the block refuses. The message is the reply line, without the CRLF."""


# ----- values on the wire

def f32(value):
    """A number as the Tesira holds it: float32, so -98.1 reads back as -98.099998."""
    return struct.unpack('f', struct.pack('f', float(value)))[0]


def fmt(value, kind='float'):
    """A value as TTP prints it."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (list, tuple)):
        return '[' + ' '.join(fmt(v, kind) for v in value) + ']'
    if kind == 'enum':
        return str(value)
    if isinstance(value, str):
        return '"' + value + '"'
    if kind == 'int' or (isinstance(value, int) and kind != 'float'):
        return str(int(value))
    return '%.6f' % f32(value)


def param(name):
    """A default taken from one of the instance's parameters (numChannels = channels)."""
    return lambda block, index: block.params[name]


# ----- the table

ROW = re.compile(r'^\|(.*)\|\s*$')
RANGE = re.compile(r'^(?P<lo>\S+) - (?P<hi>\S+)(?: (?P<unit>[A-Za-z/%]+))?(?: in (?P<step>[\d.]+) \S+ increments)?')
ENUM = re.compile(r'^[A-Z][A-Z0-9_]*(, [A-Z][A-Z0-9_]*)+$')
NUMBER = re.compile(r'^-?\d+(\.\d+)?$')


class Attr:
    def __init__(self, name, code, commands, indexes, text):
        self.name, self.code, self.text = name, code, text
        self.commands = set(c.strip() for c in commands.split('/') if c.strip())
        self.indexes = [i.strip().lower() for i in indexes.split(',') if i.strip()]
        self.kind, self.lo, self.hi, self.step, self.choices, self.unit = 'opaque', None, None, None, None, ''
        self.element = None   # a plural attribute: the per-index attribute it lists
        t = text.strip()
        if t.startswith('false, true'):
            self.kind = 'bool'
        elif ENUM.match(t):
            self.kind, self.choices = 'enum', t.split(', ')
        elif RANGE.match(t):
            m = RANGE.match(t)
            lo, hi, unit = m.group('lo'), m.group('hi'), m.group('unit') or ''
            self.lo = float(lo) if NUMBER.match(lo) else lo
            self.hi = float(hi) if NUMBER.match(hi) else hi
            self.unit = unit
            self.step = float(m.group('step')) if m.group('step') else None
            decimal = '.' in lo or '.' in hi or unit.lower().startswith('db')
            self.kind = 'float' if decimal else 'int'
        elif 'label' in code.lower() or t in ('name', 'Any alphanumeric string'):
            self.kind = 'string'

    def __repr__(self):
        return '<Attr {} {} {}>'.format(self.code, self.kind, self.indexes)


def parse_table(text):
    """Attribute rows from a pasted table: header and separator rows are skipped, and a row with
    four columns (no Indexes column in the source) gets an empty one."""
    attrs = {}
    for line in text.strip().splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split('|')]
        if cells[0] in ('Attribute', 'Reason Code', 'Attributes') or set(cells[0]) <= set('-'):
            continue
        if len(cells) == 4:
            cells.insert(3, '')
        name, code, commands, indexes, text = cells[:5]
        attrs[code] = Attr(name, code, commands, indexes, text)
    return attrs


class BlockType:
    """One kind of Tesira block.

    name      the block's name in the attribute tables ('Level Control')
    table     its attribute rows, pasted from tesira-attribute-tables.md
    params    instance parameters and their defaults ({'channels': 1}); a design overrides them
    counts    index name -> the parameter giving its count ({'channel': 'channels'}); an index
              with no count takes any positive number
    defaults  attribute code -> starting value, or a callable(block, index) (see `param`); a
              value the table implies (bool false, enum first choice, a range's 0 or nearest
              bound, '' for a string) needs no entry
    ranges    attribute code -> (lo, hi) replacing a Value Range the parser can't read
              ('0 - room count'); a bound may be a number, an attribute code or a parameter name
    kinds     attribute code -> kind, replacing the parsed one
    indexes   attribute code -> index names, replacing the Indexes column where the vendor's
              table is wrong (Standard Mixer's `crosspoint` lists none; its example uses two)
    panel    attribute codes the emulator UI shows as controls, one per index
    metered   attribute codes whose value moves on its own (meters): a subscription to one
              publishes every interval, changed or not
    source    where the table came from
    """

    def __init__(self, name, table, params=None, counts=None, defaults=None, ranges=None, kinds=None,
                 indexes=None, panel=(), metered=(), source=''):
        self.name = name
        self.table = table
        self.attrs = parse_table(table)
        self.params = dict(params or {})
        self.counts = dict(counts or {})
        self.defaults = dict(defaults or {})
        self.panel = list(panel)
        self.metered = set(metered)
        self.source = source or 'tesira-attribute-tables.md § ' + name
        for code, (lo, hi) in (ranges or {}).items():
            a = self.attrs[code]
            a.lo, a.hi = lo, hi
            if a.kind not in ('float', 'int'):
                a.kind = 'int'
        for code, kind in (kinds or {}).items():
            self.attrs[code].kind = kind
        for code, names in (indexes or {}).items():
            self.attrs[code].indexes = list(names)
        for code, a in self.attrs.items():
            single = self.attrs.get(code[:-1]) if code.endswith('s') else None
            if not a.indexes and single is not None and len(single.indexes) == 1:
                a.kind, a.element = 'array', single

    @property
    def ttp_class(self):
        return self.name.replace(' ', '')

    # ----- hooks a type overrides
    def setup(self, block):
        """After the starting values are in place (a derived value, a link between attributes)."""

    def write(self, block, attr, index, value):
        """Store one accepted change. Override for a block whose changes ripple (linked rooms,
        a crosspoint row); call block.store for each value that actually changes."""
        block.store(attr.code, index, value)

    def controls(self, block):
        """The UI controls for one instance: `panel` attributes, one per index."""
        from ..core import Toggle, Level, Choice, Readout
        out = []
        for code in self.panel:
            a = self.attrs[code]
            for index in block.all_indexes(a):
                key = block.key(code, index)
                label = '{} {}{}'.format(block.tag, a.name, ''.join(' ' + str(i) for i in index))
                settable = 'set' in a.commands or code in self.metered
                if a.kind == 'bool':
                    out.append(Toggle(key, label) if settable else Readout(key, label))
                elif a.kind in ('float', 'int') and settable:
                    lo, hi = block.bounds(a, index)
                    out.append(Level(key, label, lo, hi, a.step or (1 if a.kind == 'int' else 0.5)))
                elif a.kind == 'enum' and settable:
                    out.append(Choice(key, label, a.choices))
                else:
                    out.append(Readout(key, label))
        return out


class Block:
    """One block in a design: an instance tag of a BlockType, its values in `state`."""

    def __init__(self, tag, btype, params, state, on_change):
        self.tag, self.type = tag, btype
        self.params = dict(btype.params)
        self.params.update(params or {})
        self.state = state
        self.on_change = on_change    # (block, code, index, value) after every stored change
        self.reset()

    # ----- keys and indexes
    def key(self, code, index=()):
        return ' '.join([self.tag, code] + [str(i) for i in index])

    def count(self, index_name):
        pname = self.type.counts.get(index_name)
        if pname is None:
            return None
        value = self.params[pname]
        return len(value) if isinstance(value, (list, tuple)) else int(value)

    def all_indexes(self, attr):
        """Every index tuple an attribute has (one () for an unindexed one)."""
        combos = [()]
        for name in attr.indexes:
            n = self.count(name)
            if n is None:
                return []
            combos = [c + (i,) for c in combos for i in range(1, n + 1)]
        return combos

    def reset(self):
        for code, a in self.type.attrs.items():
            if a.kind == 'array':
                continue
            for index in self.all_indexes(a):
                self.state[self.key(code, index)] = self._default(a, index)
        self.type.setup(self)

    def _default(self, a, index):
        d = self.type.defaults.get(a.code)
        if d is not None:
            return d(self, index) if callable(d) else d
        if a.kind == 'bool':
            return False
        if a.kind == 'enum':
            return a.choices[0]
        if a.kind == 'string':
            return ''
        if a.kind in ('float', 'int'):
            lo, hi = (x if isinstance(x, (int, float)) else None for x in (a.lo, a.hi))
            v = 0
            if re.search(r'^max|Max', a.code) and hi is not None:     # maxLevel, levelOutMax
                v = hi
            elif re.search(r'^min|Min', a.code) and lo is not None:   # minLevel, inputMinLevel
                v = lo
            if lo is not None and v < lo:
                v = lo
            if hi is not None and v > hi:
                v = hi
            return f32(v) if a.kind == 'float' else int(v)
        return None

    def bound(self, b, index):
        """A range bound as a number: a literal, another attribute's value, or a parameter."""
        if isinstance(b, (int, float)) or b is None:
            return b
        if b in self.type.attrs:
            return self.state.get(self.key(b, index))
        if b in self.params:
            v = self.params[b]
            return len(v) if isinstance(v, (list, tuple)) else v
        return None

    def bounds(self, a, index):
        return self.bound(a.lo, index), self.bound(a.hi, index)

    # ----- reads and writes
    def get(self, a, index):
        if a.kind == 'array':
            return [self.state.get(self.key(a.element.code, (i,)))
                    for i in range(1, self.count(a.element.indexes[0]) + 1)]
        return self.state.get(self.key(a.code, index))

    def store(self, code, index, value):
        key = self.key(code, index)
        if self.state.get(key) != value:
            self.state[key] = value
            self.on_change(self, code, index, value)

    def check_value(self, a, index, value, clamp=False):
        """A value within the attribute's range (and on its step), or TtpError."""
        if a.kind not in ('float', 'int'):
            return value
        lo, hi = self.bounds(a, index)
        if a.step and lo is not None:
            value = lo + round((value - lo) / a.step) * a.step
        if clamp:
            if lo is not None and value < lo:
                value = lo
            if hi is not None and value > hi:
                value = hi
        elif (lo is not None and value < lo - 1e-6) or (hi is not None and value > hi + 1e-6):
            raise TtpError('-ERR INVALID_PARAMETER Value out of range:{} min:{} max:{} received:{}'.format(
                a.code, fmt(lo, a.kind), fmt(hi, a.kind), fmt(value, a.kind)))
        return f32(value) if a.kind == 'float' else int(round(value))

    def apply(self, verb, a, index, value=None, clamp=False):
        """set / increment / decrement / toggle, after the caller has checked the verb and index."""
        if verb == 'toggle':
            value = not self.get(a, index)
        elif verb in ('increment', 'decrement'):
            delta = value if verb == 'increment' else -value
            value = self.check_value(a, index, self.get(a, index) + delta, clamp=True)
        else:
            value = self.check_value(a, index, value, clamp=clamp)
        self.type.write(self, a, index, value)
        # a changed bound pulls the values it bounds back inside it
        for other in self.type.attrs.values():
            if a.code in (other.lo, other.hi) and other.indexes == a.indexes:
                current = self.get(other, index)
                if isinstance(current, (int, float)):
                    fixed = self.check_value(other, index, current, clamp=True)
                    if fixed != current:
                        self.type.write(self, other, index, fixed)
