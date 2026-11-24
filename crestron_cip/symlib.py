"""
A SIMPL device symbol's signals as CIP join numbers, read from Crestron's symbol library (symlib.tio).

A symbol lists its pins as cues per list (InputCue<n>, OutputList2Cue<n>, ...). On the wire the joins are numbered in
cue order, where an `[~UNUSED2~]` placeholder takes a number and an `[~UNUSED~]` one does not. Measured on the
IV-SAM-VX2 against an RMC4: Stop_Autoswitch is cue 6 and join 5, Toggle_Autoswitch cue 7 and
join 6, Manual_Switch_Camera cue 13 and join 10, Autoswitch_F (out) cue 5 and join 4; Camera_Select (analog) cue 23
and join 23. A touch panel's own symbol has no `[~UNUSED~]` rows, so its joins are its cue numbers.

    joins('IV-SAM-VX2')  ->  {'Toggle_Autoswitch': ('digital', 'in', 6), 'Autoswitch_F': ('digital', 'out', 4), ...}
"""
import re

SYMLIB = r'C:\Program Files (x86)\Crestron\Cresdb\Programming\symlib.tio'
_LISTS = (('InputCue', 'digital', 'in'), ('OutputCue', 'digital', 'out'), ('InputList2Cue', 'analog', 'in'),
          ('OutputList2Cue', 'analog', 'out'), ('InputList3Cue', 'serial', 'in'), ('OutputList3Cue', 'serial', 'out'))


def _path(path):
    if path:
        return path
    import os
    if os.path.exists(SYMLIB):
        return SYMLIB
    return '/mnt/c' + SYMLIB[2:].replace('\\', '/')   # the same file from WSL


def block(name, path=None):
    """The symbol's definition lines (stripped), from its Name= line to [END]."""
    out, inside = [], False
    with open(_path(path), encoding='latin-1') as f:
        for line in f:
            s = line.strip()
            if not inside and s == 'Name=' + name:
                inside = True
            if inside:
                if s == '[END]':
                    return out
                out.append(s)
    raise KeyError('no symbol named {!r} in symlib.tio'.format(name))


def joins(name, path=None):
    """{signal name: (kind, direction, join)} for every named pin of the symbol."""
    lines = block(name, path)
    result = {}
    for prefix, kind, direction in _LISTS:
        cues = sorted((int(m.group(1)), m.group(2)) for m in (re.fullmatch(prefix + r'(\d+)=(.*)', s) for s in lines) if m)
        j = 0
        for _, cue in cues:
            if cue == '[~UNUSED~]':
                continue
            j += 1
            if not cue.startswith('[~'):
                result[cue] = (kind, direction, j)
    return result
