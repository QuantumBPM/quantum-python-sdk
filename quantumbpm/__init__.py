"""
QuantumBPM Python SDK — DMN evaluation, BPMN orchestration, and external
job workers.

The public surface is curated: import from the top-level package or the
named modules. The generated package (``quantumbpm.api``, ``quantumbpm.models``,
``quantumbpm.api_client``, etc.) is reachable via ``QuantumBPM.raw`` for
endpoints not yet wrapped.
"""

from quantumbpm.auth import (
    StaticTokenProvider,
    TokenProvider,
    ZitadelTokenProvider,
)
from quantumbpm.bpmn import BpmnClient
from quantumbpm.client import QuantumBPM
from quantumbpm.dmn import DmnClient, DmnResult
from quantumbpm.variables import Vars
from quantumbpm.workers import BpmnError, Handler, Job, Worker

__version__ = "1.0.0"

__all__ = [
    "BpmnClient",
    "BpmnError",
    "DmnClient",
    "DmnResult",
    "Handler",
    "Job",
    "QuantumBPM",
    "StaticTokenProvider",
    "TokenProvider",
    "Vars",
    "Worker",
    "ZitadelTokenProvider",
]
