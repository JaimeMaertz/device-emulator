"""Source Selector (one output fed by one of N sources) and Router (each output picks an input)."""
from .base import BlockType, param

SOURCE_SELECTOR = BlockType(
    'Source Selector',
    params={'sources': 2, 'stereo': False},
    counts={'source': 'sources'},
    defaults={
        'numSources': param('sources'),
        'numInputs': lambda block, index: block.params['sources'] * (2 if block.params['stereo'] else 1),
        'numOutputs': lambda block, index: 2 if block.params['stereo'] else 1,
        'stereoEnable': param('stereo'),
    },
    ranges={'sourceSelection': (0, 'sources')},
    panel=['sourceSelection', 'outputLevel', 'outputMute'],
    table='''
| Label | label | get / set | source |  |
| Input Count | numInputs | get |  | 2 - 64 |
| Output Count | numOutputs | get |  | 1 - 2 |
| Source Count | numSources | get |  | 2 - 32 |
| Output Level | outputLevel | get / set / increment / decrement / subscribe / unsubscribe |  | outputMinLevel - outputMaxLevel dB |
| Max Output Level | outputMaxLevel | get / set / increment / decrement |  | outputMinLevel - 12.0 dB |
| Min Output Level | outputMinLevel | get / set / increment / decrement |  | -100.0 - outputMaxLevel dB |
| Output Mute | outputMute | get / set / toggle / subscribe / unsubscribe |  | false, true |
| Source Level | sourceLevel | get / set / increment / decrement / subscribe / unsubscribe | source | sourceMinLevel - sourceMaxLevel dB |
| Max Source Level | sourceMaxLevel | get / set / increment / decrement | source | sourceMinLevel - 12.0 dB |
| Min Source Level | sourceMinLevel | get / set / increment / decrement | source | -100.0 - sourceMaxLevel dB |
| Source is Mono | sourceMono | get / set / toggle | source | false, true |
| Source Selection | sourceSelection | get / set / increment / decrement / subscribe / unsubscribe |  | Source index or 0 for none |
| Stereo Enabled | stereoEnable | get |  | false, true |
''')

ROUTER = BlockType(
    'Router',
    params={'inputs': 2, 'outputs': 1},
    counts={'input': 'inputs', 'output': 'outputs'},
    defaults={'numInputs': param('inputs'), 'numOutputs': param('outputs')},
    ranges={'input': (0, 'inputs')},
    panel=['input'],
    table='''
| Selected Input | input | get / set / increment / decrement / subscribe / unsubscribe | output | Input index or 0 for no selected input |
| Input Label | inputLabel | get / set | input | Any alphanumeric string |
| Input Count | numInputs | get |  | 1 - 256 |
| Output Count | numOutputs | get |  | 1 - 256 |
| Output Label | outputLabel | get / set | output | Any alphanumeric string |
''')

TYPES = [SOURCE_SELECTOR, ROUTER]
