"""Retain original workflow assertions and parameter cases without claiming new scenarios."""

from functools import wraps

import pytest


def original_workflow(workflow):
    @wraps(workflow)
    def run(*args, **kwargs):
        return workflow(*args, **kwargs)

    run.__name__ = "test_evolution_" + workflow.__name__.removeprefix("test_")
    return pytest.mark.shared(pytest.mark.full_evolution(run))
