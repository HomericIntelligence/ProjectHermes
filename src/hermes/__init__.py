"""ProjectHermes — external webhook to NATS JetStream bridge."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("hermes")
except PackageNotFoundError:
    __version__ = "unknown"
