from .base import BlockType, param

TYPE = BlockType(
    'Level Control',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels')},
    panel=['level', 'mute'],
    table='''
| Channels Ganged | ganged | get |  | false, true |
| Label | label | get / set | channel |  |
| Level | level | get / set / increment / decrement / subscribe / unsubscribe | channel | minLevel - maxLevel dB |
| All Levels | levels | get / subscribe / unsubscribe |  |  |
| Max Level | maxLevel | get / set / increment / decrement | channel | minLevel - 12.0 dB |
| Min Level | minLevel | get / set / increment / decrement | channel | -100.0 - maxLevel dB |
| Mute | mute | get / set / toggle / subscribe / unsubscribe | channel | false, true |
| All Mute States | mutes | get / subscribe / unsubscribe |  |  |
| Channel Count | numChannels | get |  | 1 - 32 |
| Ramp Interval | rampInterval | get / set / increment / decrement | channel | 250.0 - 30000.0 ms in 250.0 ms increments |
| Ramp Step | rampStep | get / set / increment / decrement | channel | 1.0 - 15.0 dB |
| Use Ramping | useRamping | get |  | false, true |
''')
