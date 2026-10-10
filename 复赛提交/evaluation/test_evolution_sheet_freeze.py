"""Public freeze scenarios, actual scrolling, and the existing grid interactions."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_browser import create_workbook
from evolution_sheet_helpers import application, browser_errors, active_sheet, cell, cell_value, command, drag_range, formula_entry, open_workbook, rename_sheet, workbook


def freeze(page, address, label, rows, columns):
    cell(page, address).click()
    command(page, "View", label)
    expect(page.get_by_role("button", name=f"Frozen rows: {rows}; columns: {columns}", exact=True)).to_be_visible()


def scroll_container(page, horizontal, vertical):
    return page.get_by_role("grid", name="Worksheet grid", exact=True).evaluate_handle(
        """(grid, axes) => {
            for (let node = grid; node; node = node.parentElement) {
                const css = getComputedStyle(node);
                const horizontal = /auto|scroll/.test(css.overflowX) && node.scrollWidth > node.clientWidth + 150;
                const vertical = /auto|scroll/.test(css.overflowY) && node.scrollHeight > node.clientHeight + 150;
                if ((!axes.horizontal || horizontal) && (!axes.vertical || vertical)) return node;
            }
            throw new Error('The actual grid must provide enough scrollable space to demonstrate freezing');
        }""", {"horizontal": horizontal, "vertical": vertical},
    )


def positioned(locator):
    result = locator.bounding_box()
    assert result is not None
    return result


def scroll_and_record(page, request, name, anchor, horizontal, vertical):
    host = scroll_container(page, horizontal, vertical)
    host.evaluate("node => { node.scrollLeft = 0; node.scrollTop = 0; }")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    targets = {
        "frozen": cell(page, anchor),
        "moving": cell(page, "D12"),
        "column_header": page.get_by_role("columnheader", name=anchor.rstrip("0123456789"), exact=True),
        "row_header": page.get_by_role("rowheader", name=anchor.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), exact=True),
    }
    before = {key: positioned(locator) for key, locator in targets.items()}
    directory = Path(request.config.getoption("--application")).resolve() / "检查证据" / "冻结滚动" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True)
    page.screenshot(path=str(directory / (name + "-滚动前.png")))
    position = host.evaluate("""(node, axes) => {
        if (axes.horizontal) node.scrollLeft = 208;
        if (axes.vertical) node.scrollTop = 224;
        return {left: node.scrollLeft, top: node.scrollTop};
    }""", {"horizontal": horizontal, "vertical": vertical})
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    after = {key: positioned(locator) for key, locator in targets.items()}
    hit = cell(page, anchor).evaluate("""element => {
        const box = element.getBoundingClientRect();
        const top = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return top?.closest('[role="gridcell"]')?.getAttribute('aria-label');
    }""")
    page.screenshot(path=str(directory / (name + "-滚动后.png")))
    metrics = {"anchor": anchor, "horizontal": horizontal, "vertical": vertical, "scroll": position, "before": before, "after": after, "topmost_cell": hit}
    (directory / "位置测量.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Freeze browser evidence:", directory)
    if horizontal:
        assert position["left"] >= 150
        assert abs(after["frozen"]["x"] - before["frozen"]["x"]) <= 2, metrics
        assert abs(after["column_header"]["x"] - before["column_header"]["x"]) <= 2, metrics
        assert abs(after["row_header"]["x"] - before["row_header"]["x"]) <= 2, metrics
        assert after["moving"]["x"] < before["moving"]["x"] - 100, metrics
    if vertical:
        assert position["top"] >= 150
        assert abs(after["frozen"]["y"] - before["frozen"]["y"]) <= 2, metrics
        assert abs(after["row_header"]["y"] - before["row_header"]["y"]) <= 2, metrics
        assert abs(after["column_header"]["y"] - before["column_header"]["y"]) <= 2, metrics
        assert after["moving"]["y"] < before["moving"]["y"] - 100, metrics
    assert hit == anchor, "A moving cell must not cover the frozen cell"
    host.dispose()


@pytest.mark.requirements("REQ-6-1-1")
@pytest.mark.parametrize(("suffix", "address", "label", "rows", "columns", "anchor"), [
    pytest.param("ROW", "B1", "Freeze rows through 1", 1, 0, "B1", marks=pytest.mark.scenarios("REQ-6-1-1#1")),
    pytest.param("COLUMN", "A8", "Freeze columns through A", 0, 1, "A8", marks=pytest.mark.scenarios("REQ-6-1-1#2")),
    pytest.param("BOTH", "C4", "Freeze panes at C4", 3, 2, "B3", marks=pytest.mark.scenarios("REQ-6-1-1#3")),
])
def test_evolution_sheet_freeze_public_state_and_real_scroll(page, application, request, suffix, address, label, rows, columns, anchor):
    page.set_viewport_size({"width": 900, "height": 600})
    endpoint = open_workbook(page, application, "EVO-N01-FREEZE-" + suffix, "ScrollLedger")
    original = active_sheet(workbook(page, endpoint))["cells"]
    if rows:
        assert any(int(key.split(",")[0]) >= 40 for key in original)
    if columns:
        assert any(int(key.split(",")[1]) >= 12 for key in original)
    freeze(page, address, label, rows, columns)
    for phase in ("保存后", "刷新后"):
        expect(page.get_by_role("button", name=f"Frozen rows: {rows}; columns: {columns}", exact=True)).to_be_visible()
        scroll_and_record(page, request, suffix + "-" + phase, anchor, bool(columns), bool(rows))
        assert active_sheet(workbook(page, endpoint))["cells"] == original
        page.reload()


@pytest.mark.requirements("REQ-6-1-1")
def test_evolution_sheet_freeze_is_per_sheet_and_survives_rename(page, application):
    endpoint = open_workbook(page, application, "EVO-N01-FREEZE-BOTH", "ScrollLedger")
    original = active_sheet(workbook(page, endpoint))
    freeze(page, "C4", "Freeze panes at C4", 3, 2)
    page.get_by_role("button", name="Add worksheet", exact=True).click()
    expect(page.get_by_role("button", name="Frozen rows: 0; columns: 0", exact=True)).to_be_visible()
    assert active_sheet(workbook(page, endpoint))["id"] != original["id"]
    page.get_by_role("tab", name="ScrollLedger", exact=True).click()
    expect(page.get_by_role("button", name="Frozen rows: 3; columns: 2", exact=True)).to_be_visible()
    dialog = rename_sheet(page, "ScrollLedger")
    dialog.get_by_role("textbox", name="Worksheet name", exact=True).fill("Saved heading positions")
    dialog.get_by_role("button", name="Save", exact=True).click()
    page.reload()
    expect(page.get_by_role("tab", name="Saved heading positions", exact=True)).to_have_attribute("aria-selected", "true")
    expect(page.get_by_role("button", name="Frozen rows: 3; columns: 2", exact=True)).to_be_visible()
    assert active_sheet(workbook(page, endpoint))["id"] == original["id"]
    freeze(page, "A1", "Freeze panes at A1", 0, 0)
    page.reload()
    expect(page.get_by_role("button", name="Frozen rows: 0; columns: 0", exact=True)).to_be_visible()
    assert active_sheet(workbook(page, endpoint))["cells"] == original["cells"]


@pytest.mark.requirements("REQ-6-1-1", "REQ-5-1-2", "REQ-3-1-1")
def test_evolution_sheet_frozen_filtered_grid_keeps_cell_coordinates_and_selection(page, application, request):
    page.set_viewport_size({"width": 900, "height": 600})
    create_workbook(page, application)
    endpoint = application + "/api/workbooks/" + page.url.rstrip("/").split("/")[-1]
    source = active_sheet(workbook(page, endpoint))
    sheet_path = endpoint + f'/worksheets/{source["id"]}'
    cells = {"1,1": "Group", "1,2": "Amount", "1,3": "Tag", "1,4": "Load", "1,12": "Boundary", "40,12": "Last source column"}
    for row in range(2, 41):
        cells.update({f"{row},1": f"Group {row}", f"{row},2": "0" if row in {2, 4} else str(row), f"{row},3": f"Tag {row}", f"{row},4": str(row * 2)})
    assert page.request.put(sheet_path + "/cells", data={"cells": cells}).ok
    assert page.request.post(sheet_path + "/filter", data={"range": {"rowStart": 1, "colStart": 1, "rowEnd": 40, "colEnd": 4}}).ok
    assert page.request.put(sheet_path + "/filter", data={"conditions": [{"col": 2, "type": "condition", "condition": "Greater than", "value": "0"}]}).ok
    page.reload()
    freeze(page, "C5", "Freeze panes at C5", 4, 2)
    # Hidden rows 2 and 4 must not displace the visible frozen row 3.
    scroll_and_record(page, request, "筛选坐标", "B3", True, True)
    host = scroll_container(page, True, True)
    host.evaluate("node => { node.scrollLeft = 0; node.scrollTop = 0; }")
    host.dispose()
    expect(page.get_by_role("gridcell", name="B2", exact=True, include_hidden=True)).not_to_be_visible()
    formula_entry(page, "B3", "44")
    cell_value(page, "B3", "44")
    drag_range(page, "A3", "B5")
    for address in ("A3", "B3", "A5", "B5"):
        expect(cell(page, address)).to_have_attribute("aria-selected", "true")
    page.reload()
    cell_value(page, "B3", "44")
    cell_value(page, "B5", "5")
    data = active_sheet(workbook(page, endpoint))
    assert data["cells"]["2,2"] == "0" and data["cells"]["4,2"] == "0"
    assert data["selection"] == {"rowStart": 3, "colStart": 1, "rowEnd": 5, "colEnd": 2}
