"""Headless harness package.

Import concrete modules explicitly:

- ``opengis_backend.harness.headless`` for agent execution.
- ``opengis_backend.harness.metrics`` for archive metrics.

Keeping this package initializer light prevents CLI help and benchmark dry-runs
from importing the LLM stack.
"""
