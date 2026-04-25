"""ProjectHermes — external webhook to NATS JetStream bridge."""

from importlib.metadata import PackageNotFoundError, version

from hermes.config import get_settings
from hermes.models import HermesEventBase

try:
    __version__ = version("hermes")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["HermesEventBase", "__version__", "get_settings"]
