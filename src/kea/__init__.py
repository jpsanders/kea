"""kea: a second brain that checks its facts."""

try:
    from kea._version import __version__
except ImportError:  # a source tree that was never installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
