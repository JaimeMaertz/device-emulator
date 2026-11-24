from .base import BlockType, param

TYPE = BlockType(
    'Mute Control',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels')},
    panel=['mute'],
    table='''
| Channels Ganged | ganged | get |  | false, true |
| Label | label | get / set | channel |  |
| Mute | mute | get / set / toggle / subscribe / unsubscribe | channel | false, true |
| All Mute States | mutes | get / subscribe / unsubscribe |  |  |
| Channel Count | numChannels | get |  | 1 - 16 |
''')
