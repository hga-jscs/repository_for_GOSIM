"""Unmodified original public workflows impacted by metadata integration."""

import pytest
import test_sheet_requirements as baseline
from evolution_sheet_helpers import application, browser_errors


@pytest.mark.parametrize(("workflow",), [
    ("copy_formula_adjusts_relative_and_keeps_absolute_references",),
    ("formula_copy_outside_bounds_reports_reference_error",),
    ("aggregate_functions_ignore_text_and_blank_cells",),
    ("indirect_formula_cycle_does_not_block_other_cells",),
    ("export_uses_formula_results_and_preserves_formula_bar",),
])
@pytest.mark.requirements("REQ-7-1-1")
def test_evolution_sheet_original_formula_workflows(page, application, workflow):
    getattr(baseline, "test_sheet_full_" + workflow)(page, application)


@pytest.mark.parametrize(("expression", "error"), [
    ("=1/0", "#DIV/0!"), ("=MISSING(1)", "#NAME?"), ("=1+", "#ERROR!"), ("=A1", "#REF!"),
])
@pytest.mark.requirements("REQ-7-1-1")
def test_evolution_sheet_original_formula_errors(page, application, expression, error):
    baseline.test_sheet_full_formula_error_preserves_expression_and_can_be_repaired(page, application, expression, error)


@pytest.mark.parametrize(("axis", "heading", "command"), [
    ("rows", "2", "Insert 1 row above"), ("rows", "1", "Insert 1 row below"),
    ("columns", "B", "Insert 1 column left"), ("columns", "A", "Insert 1 column right"),
])
@pytest.mark.requirements("REQ-8-1-1")
def test_evolution_sheet_original_structure_workflows(page, application, axis, heading, command):
    if axis == "rows":
        baseline.test_sheet_full_rows_shift_values_rules_and_formulas_as_one_operation(page, application, heading, command)
    else:
        baseline.test_sheet_full_columns_shift_rules_and_deleted_references_show_error(page, application, heading, command)
