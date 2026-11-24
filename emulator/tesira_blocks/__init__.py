"""
Tesira block types for the `biamp_tesira` profile: one module per type (or per family), each
defining `TYPE` or a `TYPES` list of `BlockType`s (see `base.py`). Every module here is imported and its type registered by name, so
adding a type = adding a module. A bench's design names a type by its `name`
('Level Control'); lookup ignores case and a trailing ' Block'.

To add one: copy the block's attribute rows from Biamp's attribute table for it
into `table`, give the instance parameters (`params`) and which one counts each index
(`counts`), then fill in only the defaults and ranges the table can't express.
`tests/test_biamp_tesira.py` checks every type's rows against the reference, so a row
that isn't copied exactly fails it.
"""
import importlib
import pkgutil

from .base import BlockType, Block, TtpError, fmt, f32, param  # noqa: F401

BLOCK_TYPES = {}


def normalize(name):
    name = name.strip().lower()
    return name[:-6] if name.endswith(' block') else name


def lookup(name):
    return BLOCK_TYPES.get(normalize(name))


for _module in pkgutil.iter_modules(__path__):
    if _module.name == 'base':
        continue
    _mod = importlib.import_module('{}.{}'.format(__name__, _module.name))
    for _type in getattr(_mod, 'TYPES', [getattr(_mod, 'TYPE', None)]):
        if isinstance(_type, BlockType):
            BLOCK_TYPES[normalize(_type.name)] = _type
