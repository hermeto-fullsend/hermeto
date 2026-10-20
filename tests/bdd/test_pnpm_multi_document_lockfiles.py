# SPDX-License-Identifier: GPL-3.0-only
"""Bind the pnpm multi-document lockfile scenarios.

pytest-bdd never collects a .feature file on its own -- only the scenarios()
call below turns it into tests. A feature with no binding module is invisible
to the bdd session, which is why bdd-lint exists as a separate check.
"""

from pytest_bdd import scenarios

# The star import is how pytest-bdd makes step definitions visible to
# scenarios(). It cannot be narrowed and the step module cannot declare
# __all__: the decorators register each step under a generated fixture name
# such as "pytestbdd_stepdef_given_a project with a {shape} pnpm lockfile",
# which is not a valid identifier, and an __all__ that leaves those out hides
# every step from scenarios().
from tests.bdd.steps.pnpm_lockfiles import *  # noqa: F401,F403

scenarios("pnpm-multi-document-lockfiles.feature")
