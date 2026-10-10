"""Original pivot aggregation, preservation and source-deletion checks, unchanged."""

import test_sheet_requirements as requirements_workflows
from evolution_sheet_helpers import application, browser_errors
from original_workflows import original_workflow

for name in (
    "pivot_sum_refresh_and_source_deletion_constraint",
    "pivot_count_columns_includes_nonnumeric_values_and_zero_combinations",
    "pivot_average_ignores_text_and_invalid_configuration_keeps_old_result",
    "pivot_header_deletion_preserves_last_successful_summary",
):
    wrapped = original_workflow(getattr(requirements_workflows, "test_sheet_full_" + name))
    globals()[wrapped.__name__] = wrapped
