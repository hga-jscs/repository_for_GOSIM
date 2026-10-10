"""Original value/condition filtering and default validation behavior, unchanged."""

import test_sheet_requirements as requirements_workflows
import test_sheet_workflows as sheet_workflows
from evolution_sheet_helpers import application, browser_errors
from original_workflows import original_workflow

for module, names in [
    (requirements_workflows, ("value_and_condition_filters_combine_and_export_hidden_rows", "filter_conditions_preserve_row_order", "dropdown_trims_values_and_rule_can_be_edited_then_removed", "numeric_validation_includes_boundaries_and_preserves_range")),
    (sheet_workflows, ("number_validation_persists",)),
]:
    for name in names:
        wrapped = original_workflow(getattr(module, "test_sheet_full_" + name))
        globals()[wrapped.__name__] = wrapped
