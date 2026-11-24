"""
Dynamics blocks. The AGC block: what a room controller touches is `bypass` (automatic vs manual
level control). Its meters and voice detection have no value range
in the table; they're given plain kinds here and hold still (nothing drives them).
"""
from .base import BlockType, param

TYPE = BlockType(
    'AGC',
    params={'channels': 1},
    defaults={'numChannels': param('channels')},
    kinds={'allChannelMeters': 'float', 'allVoiceDetect': 'bool', 'channelMeters': 'float',
           'meters': 'bool', 'voiceDetect': 'bool'},
    counts={'channel': 'channels'},
    panel=['bypass'],
    table='''
| AGC Active | agcActive | get |  | false, true |
| All channel meters | allChannelMeters | get / subscribe / unsubscribe |  |  |
| All voice detect | allVoiceDetect | get / subscribe / unsubscribe |  |  |
| Bypass | bypass | get / set / toggle |  | false, true |
| Meter by channel | channelMeters | get / subscribe / unsubscribe | channel |  |
| Gain Level | gainLevel | get |  | -30.0 - 30.0 dB |
| Hold Time | holdTime | get / set / increment / decrement |  | 0 - 350000 s |
| Input Level | inputLevel | get |  | -100.0 - 36.0 dBu |
| Limiter On | limiter | get / set / toggle |  | false, true |
| Limiter Active | limiterActive | get |  | false, true |
| Max Attenuation | maxAtten | get / set / increment / decrement |  | 0.0 - 30.0 dB |
| Max Gain | maxGain | get / set / increment / decrement |  | 0.0 - 30.0 dB |
| Max Gain Adj. Rate | maxGainRate | get / set / increment / decrement |  | 0.0 - 15.0 dB/s |
| All Meter States | meters | get / subscribe / unsubscribe |  | (will only work on the first channel of a multi-channel block) |
| Min SNR | minSnr | get / set / increment / decrement |  | 10.0 - 50.0 dB |
| Min Threshold | minThreshold | get / set / increment / decrement |  | -30.0 - 20.0 dBu (Max Value equal to Target Level) |
| Noise Floor Level | noiseFloorLevel | get |  | -100.0 - 36.0 dBu |
| Number of channels | numChannels | get |  | 1 - 32 |
| Side Chain Level | sideChainLevel | get |  | -100.0 - 36.0 dBu |
| SNR Level | snrLevel | get |  | 0.0 - 136.0 dB |
| Speech On | speech | get / set / toggle |  | false, true |
| Target Level | targetLevel | get / set / increment / decrement |  | -20.0 - 20.0 dB |
| Voice detect per channel | voiceDetect | get / subscribe / unsubscribe |  |  |
''')
