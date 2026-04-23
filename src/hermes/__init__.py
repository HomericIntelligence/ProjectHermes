"""ProjectHermes — external webhook to NATS JetStream bridge."""

from importlib.metadata import PackageNotFoundError, version

from hermes.models import AgentEvent, HermesEventBase, TaskEvent

try:
    __version__ = version("hermes")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["AgentEvent", "HermesEventBase", "TaskEvent", "__version__"]
