"""Original external paste, move, undo and recalculation assertions, unchanged."""

import test_sheet_requirements as requirements_workflows
import test_sheet_workflows as sheet_workflows
from evolution_sheet_helpers import application, browser_errors
from original_workflows import original_workflow

for module, names in [
    (requirements_workflows, ("external_paste_is_rectangular_and_undoable", "invalid_bulk_paste_rejects_entire_rectangle", "copy_and_cut_preserve_rectangle_and_outside_cells", "invalid_cut_preserves_source_and_target")),
    (sheet_workflows, ("undo_redo_and_new_branch", "formula_chain_recalculates_and_persists")),
]:
    for name in names:
        wrapped = original_workflow(getattr(module, "test_sheet_full_" + name))
        globals()[wrapped.__name__] = wrapped
