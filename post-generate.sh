#!/bin/bash
# Post-generation cleanup. openapi-generator emits its own setup.py,
# pyproject.toml, README, and per-model unit tests; we maintain those by hand,
# so this script restores them after every regeneration.
set -e

SDK_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Restoring hand-maintained package metadata and removing generator scaffolding..."

cat > "$SDK_DIR/pyproject.toml" <<'EOF'
[project]
name = "quantumbpm-sdk"
version = "1.0.0"
description = "QuantumBPM Python SDK — DMN evaluation, BPMN orchestration, and external job workers."
authors = [
  {name = "QuantumBPM",email = "support@quantumbpm.com"},
]
readme = "README.md"
keywords = ["quantumbpm", "dmn", "bpmn", "workflow", "feel", "sdk"]
requires-python = ">=3.10"

dependencies = [
  "urllib3 (>=2.1.0,<3.0.0)",
  "python-dateutil (>=2.8.2)",
  "pydantic (>=2)",
  "typing-extensions (>=4.7.1)",
  "PyJWT[crypto] (>=2.8.0)",
  "requests (>=2.31.0)",
]

[project.urls]
Repository = "https://github.com/QuantumBPM/quantum-python-sdk"

[tool.poetry]
requires-poetry = ">=2.0"

[tool.poetry.group.dev.dependencies]
pytest = ">= 7.2.1"
pytest-asyncio = ">= 0.23"
pytest-cov = ">= 2.8.1"
mypy = ">= 1.5"

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.pylint.'MESSAGES CONTROL']
extension-pkg-whitelist = "pydantic"

[tool.mypy]
files = ["quantumbpm"]
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true
strict_equality = true
extra_checks = true
check_untyped_defs = true
disallow_subclassing_any = true
disallow_untyped_decorators = true
disallow_any_generics = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
EOF

cat > "$SDK_DIR/setup.py" <<'EOF'
# coding: utf-8

"""
QuantumBPM Python SDK — DMN evaluation, BPMN orchestration, and external
job workers.
"""

from setuptools import setup, find_packages

NAME = "quantumbpm-sdk"
VERSION = "1.0.0"
PYTHON_REQUIRES = ">= 3.10"
REQUIRES = [
    "urllib3 >= 2.1.0, < 3.0.0",
    "python-dateutil >= 2.8.2",
    "pydantic >= 2",
    "typing-extensions >= 4.7.1",
    "PyJWT[crypto] >= 2.8.0",
    "requests >= 2.31.0",
]

setup(
    name=NAME,
    version=VERSION,
    description="QuantumBPM Python SDK — DMN evaluation, BPMN orchestration, and external job workers.",
    author="QuantumBPM",
    author_email="support@quantumbpm.com",
    url="https://quantumbpm.com",
    keywords=["quantumbpm", "dmn", "bpmn", "workflow", "feel", "sdk"],
    install_requires=REQUIRES,
    python_requires=PYTHON_REQUIRES,
    packages=find_packages(exclude=["test", "tests"]),
    include_package_data=True,
    long_description_content_type="text/markdown",
    long_description="QuantumBPM Python SDK — DMN evaluation, BPMN orchestration, and external job workers.",
    package_data={"quantumbpm": ["py.typed"]},
)
EOF

# Restore the curated public surface — openapi-generator's __init__.py
# re-exports every generated symbol; ours instead exposes the SDK's
# user-facing classes (QuantumBPM, Vars, Worker, ZitadelTokenProvider, …).
# Generated APIs and models stay reachable via fully-qualified imports
# (quantumbpm.api.default_api.DefaultApi, etc.) for power users.
cat > "$SDK_DIR/quantumbpm/__init__.py" <<'PYEOF'
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
PYEOF

# Drop scaffolding we don't ship.
rm -rf "$SDK_DIR/test"
rm -rf "$SDK_DIR/docs"
rm -f "$SDK_DIR/.gitlab-ci.yml" "$SDK_DIR/.travis.yml" "$SDK_DIR/git_push.sh"

echo "Python SDK post-generation cleanup complete."
