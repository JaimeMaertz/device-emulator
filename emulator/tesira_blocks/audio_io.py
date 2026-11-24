"""Audio Input and Audio Output: a device's analog I/O channels."""
from .base import BlockType, param

INPUT = BlockType(
    'Audio Input',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels')},
    panel=['gain', 'level', 'mute', 'peak'],
    metered=['peak', 'peaks'],
    table='''
| Gain | gain | get / set / increment / decrement | channel | 0 - 66 dB in 6 dB increments |
| Invert | invert | get / set / toggle | channel | false, true |
| Level | level | get / set / increment / decrement | channel | minLevel - maxLevel dB |
| Max Level | maxLevel | get / set / increment / decrement | channel | minLevel - 12.0 dB |
| Min Level | minLevel | get / set / increment / decrement | channel | -100.0 - maxLevel dB |
| Mute | mute | get / set / toggle | channel | false, true |
| Channel Count | numChannels | get |  | 1 - 24 |
| Peak Occurring | peak | get / subscribe / unsubscribe | channel | false, true |
| All Peaks | peaks | get / subscribe / unsubscribe |  |  |
| Phantom Power On | phantomPower | get / set / toggle | channel | false, true |
''')

# Full Scale's range is "-31 or 0 - 24 dB in 6 dB increments" (the parser reads single ranges):
# taken here as -31 to 24 with no step.
OUTPUT = BlockType(
    'Audio Output',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels'), 'maxLevel': 0.0},
    ranges={'fullScale': (-31.0, 24.0)},
    kinds={'fullScale': 'float'},
    panel=['level', 'mute'],
    table='''
| Full Scale | fullScale | get / set / increment / decrement | channel | -31 or 0 - 24 dB in 6 dB increments |
| Invert | invert | get / set / toggle | channel | false, true |
| Level | level | get / set / increment / decrement | channel | minLevel - maxLevel dB |
| Max Level | maxLevel | get / set / increment / decrement | channel | minLevel - 0.0 dB |
| Min Level | minLevel | get / set / increment / decrement | channel | -100.0 - maxLevel dB |
| Mute | mute | get / set / toggle | channel | false, true |
| Channel Count | numChannels | get |  | 1 - 24 |
''')

TYPES = [INPUT, OUTPUT]
