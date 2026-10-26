"""Packaged default configuration, OSM samples and device-location helper."""
from contextlib import ExitStack
from importlib.resources import as_file, files
import atexit

__version__ = "0.2.0"

_resources = ExitStack()
atexit.register(_resources.close)


def resource_file(*parts):
    return _resources.enter_context(as_file(files(__package__).joinpath(*parts)))
