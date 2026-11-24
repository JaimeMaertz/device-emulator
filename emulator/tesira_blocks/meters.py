"""
Audio Meter (peak / RMS) and Signal Present Meter. There's no audio in the emulator: a meter reads
what the UI (or a test) sets as its signal, and a subscription to a level publishes every interval,
as a real meter's does. Signal Present follows the level against the threshold, and the logic
state follows Signal Present.

Guessed: the threshold comparison uses the level as-is (the table gives the level in dB and the
threshold in dBu); on and off delays aren't applied.
"""
from .base import BlockType, param


class SignalPresent(BlockType):
    def write(self, block, attr, index, value):
        block.store(attr.code, index, value)
        if attr.code in ('level', 'threshold', 'invert'):
            self.follow(block, index)

    def setup(self, block):
        for c in range(1, block.params['channels'] + 1):
            self.follow(block, (c,))

    @staticmethod
    def follow(block, index):
        present = block.state[block.key('level', index)] >= block.state[block.key('threshold', index)]
        block.store('present', index, present)
        block.store('logicState', index, present != block.state[block.key('invert', index)])


METER = BlockType(
    'Audio Meter',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels'), 'level': -100.0},
    panel=['level'],
    metered=['level', 'levels'],
    table='''
| Hold Enabled | holdEnabled | get / set / toggle | channel | false, true |
| Hold Time | holdTime | get / set / increment / decrement | channel | 0.0 - 10000.0 ms |
| Hold Indefinitely | indefiniteHold | get / set / toggle | channel | false, true |
| Label | label | get / set | channel |  |
| Level | level | get / subscribe / unsubscribe | channel | -100.0 - 36.0 dB |
| All Levels | levels | get / subscribe / unsubscribe |  |  |
| Channel Count | numChannels | get |  | 1 - 32 |
| Type | type | get / set |  | PEAK, RMS |
''')

SIGNAL_PRESENT = SignalPresent(
    'Signal Present Meter',
    params={'channels': 1},
    counts={'channel': 'channels'},
    defaults={'numChannels': param('channels'), 'level': -100.0, 'threshold': -40.0},
    panel=['level', 'present'],
    metered=['level', 'levels'],
    table='''
| Invert | invert | get / set / toggle | channel | false, true |
| Label | label | get / set | channel |  |
| Signal Level | level | get / subscribe / unsubscribe | channel | -100.0 - 36.0 dB |
| All Levels | levels | get / subscribe / unsubscribe |  |  |
| Logic State | logicState | get | channel | false, true |
| Channel Count | numChannels | get |  | 1 - 16 |
| Off Delay | offDelay | get / set / increment / decrement | channel | 0 - 60000 ms |
| On Delay | onDelay | get / set / increment / decrement | channel | 0 - 60000 ms |
| Signal Present | present | get / subscribe / unsubscribe | channel | false, true |
| All Signal Indicators | presents | get / subscribe / unsubscribe |  |  |
| Threshold | threshold | get / set / increment / decrement | channel | -64.0 - 30.0 dBu |
''')

TYPES = [METER, SIGNAL_PRESENT]
