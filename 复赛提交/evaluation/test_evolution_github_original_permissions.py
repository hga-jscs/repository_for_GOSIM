"""Original role combinations, revocation and personal-data preservation."""

import test_github_permissions as permissions_workflows
from evolution_github_baseline_helpers import application, browser_errors
from original_workflows import original_workflow

for name in (
    "issue_roles_separate_writing_from_triage",
    "role_downgrade_revokes_stale_editing",
    "membership_removal_revokes_private_access_but_keeps_personal_data",
):
    wrapped = original_workflow(getattr(permissions_workflows, "test_github_full_" + name))
    globals()[wrapped.__name__] = wrapped
