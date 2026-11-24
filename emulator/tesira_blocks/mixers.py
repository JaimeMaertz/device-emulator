"""
Matrix Mixer and Standard Mixer. The row / column / diagonal / all attributes are set-only and
act on the crosspoints: a row is one input to every output, a column every input to one output.

Guessed (the docs don't say): a diagonal set at (input, output) walks (input + k, output + k)
to the edge of the matrix; toggling a row, column, diagonal or all flips it from its last set
value, not per crosspoint.
"""
from .base import BlockType, param


class Mixer(BlockType):
    def __init__(self, *args, crosspoint, **kwargs):
        super().__init__(*args, **kwargs)
        self.crosspoint = crosspoint
        self.fans = {crosspoint + suffix: suffix.lower() for suffix in ('All', 'Row', 'Column', 'Diagonal')}

    def write(self, block, attr, index, value):
        block.store(attr.code, index, value)
        shape = self.fans.get(attr.code)
        if not shape:
            return
        ins, outs = block.params['inputs'], block.params['outputs']
        if shape == 'all':
            points = [(i, o) for i in range(1, ins + 1) for o in range(1, outs + 1)]
        elif shape == 'row':
            points = [(index[0], o) for o in range(1, outs + 1)]
        elif shape == 'column':
            points = [(i, index[0]) for i in range(1, ins + 1)]
        else:
            points = [(index[0] + k, index[1] + k) for k in range(max(ins, outs))
                      if index[0] + k <= ins and index[1] + k <= outs]
        for point in points:
            block.store(self.crosspoint, point, value)


MATRIX = Mixer(
    'Matrix Mixer',
    crosspoint='crosspointLevelState',
    params={'inputs': 2, 'outputs': 1},
    counts={'input': 'inputs', 'output': 'outputs'},
    defaults={'numInputs': param('inputs'), 'numOutputs': param('outputs')},
    panel=['crosspointLevelState', 'inputMute', 'outputLevel', 'outputMute'],
    table='''
| Crosspoint Delay | crosspointDelay | get / set / increment / decrement | input, output | 0.0 - 250.0 ms |
| Crosspoint Delay On | crosspointDelayState | get / set / toggle | input, output | false, true |
| All Delay Crosspoints | crosspointDelayStateAll | set / toggle |  | false, true |
| Delay Crosspoint Column | crosspointDelayStateColumn | set / toggle | output | false, true |
| Delay Crosspoint Diagonal | crosspointDelayStateDiagonal | set / toggle | input, output | false, true |
| Delay Crosspoint Row | crosspointDelayStateRow | set / toggle | input | false, true |
| Crosspoint Level | crosspointLevel | get / set / increment / decrement / subscribe / unsubscribe | input, output | -100.0 - 0.0 dB |
| Crosspoint On | crosspointLevelState | get / set / toggle / subscribe / unsubscribe | input, output | false, true |
| All Crosspoints | crosspointLevelStateAll | set / toggle |  | false, true |
| Crosspoint Column | crosspointLevelStateColumn | set / toggle | output | false, true |
| Crosspoint Diagonal | crosspointLevelStateDiagonal | set / toggle | input, output | false, true |
| Crosspoint Row | crosspointLevelStateRow | set / toggle | input | false, true |
| Delay Enabled | delayEnabled | get |  | false, true |
| Input Label | inputLabel | get / set | input |  |
| Input Level | inputLevel | get / set / increment / decrement / subscribe / unsubscribe | input | inputMinLevel - inputMaxLevel dB |
| Max Input Level | inputMaxLevel | get / set / increment / decrement | input | inputMinLevel - 12.0 dB |
| Min Input Level | inputMinLevel | get / set / increment / decrement | input | -100.0 - inputMaxLevel dB |
| Input Mute | inputMute | get / set / toggle / subscribe / unsubscribe | input | false, true |
| Input Count | numInputs | get |  | 2 - 256 |
| Output Count | numOutputs | get |  | 1 - 256 |
| Output Label | outputLabel | get / set | output |  |
| Output Level | outputLevel | get / set / increment / decrement / subscribe / unsubscribe | output | outputMinLevel - outputMaxLevel dB |
| Max Output Level | outputMaxLevel | get / set / increment / decrement | output | outputMinLevel - 12.0 dB |
| Min Output Level | outputMinLevel | get / set / increment / decrement | output | -100.0 - outputMaxLevel dB |
| Output Mute | outputMute | get / set / toggle / subscribe / unsubscribe | output | false, true |
''')

STANDARD = Mixer(
    'Standard Mixer',
    crosspoint='crosspoint',
    params={'inputs': 2, 'outputs': 1},
    counts={'input': 'inputs', 'output': 'outputs'},
    defaults={'numInputs': param('inputs'), 'numOutputs': param('outputs')},
    indexes={'crosspoint': ['input', 'output']},
    panel=['crosspoint', 'inputMute', 'outputLevel', 'outputMute'],
    table='''
| Crosspoint On | crosspoint | get / set / toggle / subscribe / unsubscribe |  | false, true |
| All Crosspoints | crosspointAll | set / toggle |  | false, true |
| Crosspoint Column | crosspointColumn | set / toggle | output | false, true |
| Crosspoint Diagonal | crosspointDiagonal | set / toggle | input, output | false, true |
| Crosspoint Row | crosspointRow | set / toggle | input | false, true |
| Input Label | inputLabel | get / set | input | name |
| Input Level | inputLevel | get / set / increment / decrement / subscribe / unsubscribe | input | inputMinLevel - inputMaxLevel dB |
| Max Input Level | inputMaxLevel | get / set / increment / decrement | input | inputMinLevel - 12.0 dB |
| Min Input Level | inputMinLevel | get / set / increment / decrement | input | -100.0 - inputMaxLevel dB |
| Input Mute | inputMute | get / set / toggle / subscribe / unsubscribe | input | false, true |
| Input Count | numInputs | get |  | 2 - 256 |
| Output Count | numOutputs | get |  | 1 - 256 |
| Output Label | outputLabel | get / set | output | name |
| Output Level | outputLevel | get / set / increment / decrement / subscribe / unsubscribe | output | outputMinLevel - outputMaxLevel dB |
| Max Output Level | outputMaxLevel | get / set / increment / decrement | output | outputMinLevel - 12.0 dB |
| Min Output Level | outputMinLevel | get / set / increment / decrement | output | -100.0 - outputMaxLevel dB |
| Output Mute | outputMute | get / set / toggle / subscribe / unsubscribe | output | false, true |
''')

TYPES = [MATRIX, STANDARD]
