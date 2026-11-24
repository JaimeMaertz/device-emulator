"""
Device profiles. Every module in this folder is imported, and each Device subclass with a
`profile_id` registers itself in core.PROFILES. Adding a profile = adding a module here.
"""
import importlib
import pkgutil

for _module in pkgutil.iter_modules(__path__):
    importlib.import_module('{}.{}'.format(__name__, _module.name))
