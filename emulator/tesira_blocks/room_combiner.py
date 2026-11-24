"""
Room Combiner: rooms joined by removable walls. `wallState true` = the wall is closed and the rooms
on either side are separate; `false` removes it and combines them (attribute table: "Wall Closed";
the Extron module sends Close as true).

Parameters: `rooms` (count), `walls` (a list of [room, room] pairs, one per removable wall in
wall-number order, or a count for a row of rooms where wall n joins rooms n and n+1), `sources` (4).

From the Room Combiner object page (Tesira help, Audio Objects > Mixer > Room Combiner):
- rooms in the same combine group share output level and mute, source selection, and source
  level and mute; group 0 is uncombined
- when rooms combine, the combined space takes the lower-numbered room's source unless the
  wall's `preferredRoom` names the other; if either room had output or source mute on, the
  combined space is muted; where levels differ, the lower one wins

Guessed (the docs don't say): how group numbers are assigned when walls open (here each combined
space is numbered 1, 2, ... in order of its lowest room; a lone room is 0); that rooms keep the
combined values when a wall closes again; that setting `group` directly combines rooms without
moving walls.
"""
from .base import BlockType, param

LINKED = ('levelOut', 'muteOut', 'sourceSelection', 'levelSource', 'muteSource')


def wall_pairs(block):
    walls = block.params['walls']
    if isinstance(walls, int):
        return [(n, n + 1) for n in range(1, walls + 1)]
    return [tuple(w) for w in walls]


class RoomCombiner(BlockType):
    def write(self, block, attr, index, value):
        code = attr.code
        if code == 'wallState':
            block.store(code, index, value)
            self.regroup(block, opened=None if value else index[0])
        elif code == 'group':
            block.store(code, index, value)
            if value:
                rooms = [r for r in range(1, block.params['rooms'] + 1)
                         if block.state[block.key('group', (r,))] == value]
                self.merge(block, rooms, winner=min(rooms))
        elif code in LINKED:
            for room in self.group_rooms(block, index[0]):
                block.store(code, (room,), value)
        else:
            block.store(code, index, value)

    @staticmethod
    def group_rooms(block, room):
        g = block.state[block.key('group', (room,))]
        if not g:
            return [room]
        return [r for r in range(1, block.params['rooms'] + 1) if block.state[block.key('group', (r,))] == g]

    def regroup(self, block, opened):
        rooms = block.params['rooms']
        parent = list(range(rooms + 1))

        def find(r):
            while parent[r] != r:
                r = parent[r]
            return r
        pairs = wall_pairs(block)
        for n, (a, b) in enumerate(pairs, 1):
            if not block.state[block.key('wallState', (n,))]:
                parent[find(a)] = find(b)
        spaces = {}
        for r in range(1, rooms + 1):
            spaces.setdefault(find(r), []).append(r)
        number = 0
        for members in sorted(spaces.values(), key=min):
            if len(members) > 1:
                number += 1
                winner = min(members)
                if opened is not None and set(pairs[opened - 1]) <= set(members):
                    winner = block.state[block.key('preferredRoom', (opened,))]
                self.merge(block, members, winner)
            for r in members:
                block.store('group', (r,), number if len(members) > 1 else 0)

    @staticmethod
    def merge(block, members, winner):
        value = lambda code, r: block.state[block.key(code, (r,))]
        merged = {
            'sourceSelection': value('sourceSelection', winner),
            'muteOut': any(value('muteOut', r) for r in members),
            'muteSource': any(value('muteSource', r) for r in members),
            'levelOut': min(value('levelOut', r) for r in members),
            'levelSource': min(value('levelSource', r) for r in members),
        }
        for r in members:
            for code, v in merged.items():
                block.store(code, (r,), v)


TYPE = RoomCombiner(
    'Room Combiner',
    params={'rooms': 2, 'walls': 1, 'sources': 4},
    counts={'room': 'rooms', 'wall number': 'walls', 'source': 'sources'},
    defaults={
        'wallState': True,
        'preferredRoom': lambda block, index: min(wall_pairs(block)[index[0] - 1]),
        'numInputs': param('rooms'),
        'numOutputs': param('rooms'),
    },
    ranges={'group': (0, 'rooms'), 'preferredRoom': (1, 'rooms'), 'sourceSelection': (0, 'sources')},
    panel=['wallState', 'group', 'sourceSelection', 'levelOut', 'muteOut'],
    table='''
| Room Group | group | get / set / increment / decrement / subscribe / unsubscribe | room | 0 - room count |
| Last Mic Hold Enabled | lastMicHoldEnable | get / set / toggle |  | false, true |
| Input Level | levelIn | get / set / increment / decrement / subscribe / unsubscribe | room | levelInMin - levelInMax dB |
| Max Input Level | levelInMax | get / set / increment / decrement | room | levelInMin - 12.0 dB |
| Min Input Level | levelInMin | get / set / increment / decrement | room | -100.0 - levelInMax dB |
| Output Level | levelOut | get / set / increment / decrement / subscribe / unsubscribe | room | levelOutMin - levelOutMax dB |
| Max Output Level | levelOutMax | get / set / increment / decrement | room | levelOutMin - 12.0 dB |
| Min Output Level | levelOutMin | get / set / increment / decrement | room | -100.0 - levelOutMax dB |
| Source Level | levelSource | get / set / increment / decrement / subscribe / unsubscribe | room | levelSourceMin - levelSourceMax dB |
| Max Source Level | levelSourceMax | get / set / increment / decrement | room | levelSourceMin - 12.0 dB |
| Min Source Level | levelSourceMin | get / set / increment / decrement | room | -100.0 - levelSourceMax dB |
| Input Mute | muteIn | get / set / toggle / subscribe / unsubscribe | room | false, true |
| Output Mute | muteOut | get / set / toggle / subscribe / unsubscribe | room | false, true |
| Source Mute | muteSource | get / set / toggle / subscribe / unsubscribe | room | false, true |
| Open Mic Limit | nomLimit | get / set / increment / decrement |  | 1 - 7 |
| Open Mic Limit Enabled | nomLimitEnable | get / set / toggle |  | false, true |
| Input Count | numInputs | get |  | 2 - 32 |
| Output Count | numOutputs | get |  | 2 - 32 |
| Wall Room Precedence | preferredRoom | get / set / increment / decrement / subscribe / unsubscribe | Wall Number | A room index |
| Room Label | roomLabel | get / set | room |  |
| Source Label | sourceLabel | get / set | source |  |
| Source Selection | sourceSelection | get / set / increment / decrement / subscribe / unsubscribe | room | 0 - 4 |
| Wall Closed | wallState | get / set / toggle / subscribe / unsubscribe | wall number | false, true |
''')
