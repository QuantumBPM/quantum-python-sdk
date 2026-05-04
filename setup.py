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
