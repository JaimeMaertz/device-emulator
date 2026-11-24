"""Preset Control (buttons that each hold a preset ID) and Logic State (latching logic outputs)."""
from .base import BlockType, param

PRESET = BlockType(
    'Preset Control',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels'), 'preset': 1001},
    ranges={'preset': (1, 999999)},
    table='''
| Channel Count | numChannels | get |  | 1 - 10 |
| Preset ID | preset | get / set / increment / decrement | channel | ID of any preset |
''')

LOGIC_STATE = BlockType(
    'Logic State',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels')},
    panel=['state'],
    table='''
| Label | label | get / set | channel | name |
| Channel Count | numChannels | get |  | 1 - 32 |
| Set | state | get / set / toggle / subscribe / unsubscribe | channel | false, true |
| Get All States | states | get / subscribe / unsubscribe |  |  |
''')

TYPES = [PRESET, LOGIC_STATE]
