"""Original CSV fidelity, rejection and sort boundaries, unchanged."""

import test_sheet_requirements as requirements_workflows
import test_sheet_workflows as sheet_workflows
from evolution_sheet_helpers import application, browser_errors
from original_workflows import original_workflow

for module, names in [
    (sheet_workflows, ("csv_roundtrip_preserves_quoted_unicode_and_empty_cells", "invalid_csv_does_not_create_a_workbook", "stable_sort_moves_whole_rows")),
    (requirements_workflows, ("sort_is_limited_to_selected_rectangle",)),
]:
    for name in names:
        wrapped = original_workflow(getattr(module, "test_sheet_full_" + name))
        globals()[wrapped.__name__] = wrapped
