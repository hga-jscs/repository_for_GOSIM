"""Original workbook, worksheet, selection and editing assertions, unchanged."""

import test_browser as browser_workflows
import test_sheet_requirements as requirements_workflows
import test_sheet_workflows as sheet_workflows
from evolution_sheet_helpers import application, browser_errors
from original_workflows import original_workflow

for module, names in [
    (browser_workflows, ("blank_workbook_state_and_reload", "workbook_direct_url_in_new_browser", "rename_validation_and_persistence")),
    (requirements_workflows, ("full_worksheet_rename_rejects_empty_and_duplicate_names", "full_delete_worksheet_keeps_one_and_reuses_first_unused_name", "full_drag_rectangle_survives_switch_and_reload", "full_edit_cancel_and_blur_commit_preserve_original_text", "full_grid_edit_commits_and_escape_cancels")),
    (sheet_workflows, ("full_worksheet_switch_keeps_data_and_selection",)),
]:
    for name in names:
        wrapped = original_workflow(getattr(module, "test_sheet_" + name))
        globals()[wrapped.__name__] = wrapped
