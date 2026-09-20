"""Kraswerk cut-list add-in for Autodesk Fusion.

Adds a KRASWERK tab to the Design workspace with a Cut list button. The button
opens a dialog listing every sheet-goods panel in the active design; its OK
button, relabelled "Export CSV...", writes the cut list for a cutting
optimizer.

The measuring and grouping live in bom_core.py, one directory up.

This is an add-in rather than a script because only an add-in can put a button
on the ribbon at startup and take it off again cleanly when stopped.
"""

import os
import sys
import html
import importlib
import traceback

import adsk.core
import adsk.fusion

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import bom_core
importlib.reload(bom_core)

WORKSPACE_ID = 'FusionSolidEnvironment'
TAB_ID = 'KraswerkTab'
TAB_NAME = 'KRASWERK'
PANEL_ID = 'KraswerkBomPanel'
PANEL_NAME = 'Bill of materials'
COMMAND_ID = 'kraswerkCutList'
COMMAND_NAME = 'Cut list'
COMMAND_TOOLTIP = 'List every panel in this design and export it as a CSV cut list.'

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# A TableCommandInput builds one widget per cell, so a very long cut list is
# slow to show. Beyond this many rows the table is truncated -- the export
# always contains everything.
MAX_TABLE_ROWS = 150

# Relative widths of the seven bom_core.COLUMNS.
COLUMN_RATIO = '4:3:1:2:2:2:3'

# Height of the table, counted in rows and including the header. The table
# keeps this height whether the cut list is shorter (blank rows below) or
# longer (a scroll bar). Fusion's own default is 4, which is why an
# unconfigured table scrolls its header out of sight.
VISIBLE_ROWS = 15

# Roughly the pixel height of one table row, used to size the dialog so the
# table fills it rather than floating in empty space.
ROW_HEIGHT = 34

# Height of the note under the table. Fixed, because a text box cannot be
# resized once built and the notes come and go as the filters change.
NOTE_ROWS = 3

HEAD_TABLE_ID = 'bomHead'
TABLE_ID = 'bomTable'
NOTES_ID = 'notes'

# The columns that carry a filter, and the id of the widget for each. Qty,
# Length and Width have none: they are all but unique per row, so a tick-list
# of them would be as long as the cut list.
FILTER_IDS = {
    bom_core.NAME: 'filterName',
    bom_core.PART_OF: 'filterPartOf',
    bom_core.THICKNESS: 'filterThickness',
    bom_core.MATERIAL: 'filterMaterial',
}

# Clearing a table leaves its old cells behind under their old ids, so every
# rebuild numbers its cells afresh rather than colliding with them.
_generation = [0]

# Fusion drops event handlers that are only referenced locally.
_handlers = []
_state = {}
_execute = None
_filter = None


def report(message):
    """Surface an error. Fusion swallows exceptions raised inside handlers,
    so anything that goes wrong in notify() has to be reported by hand or it
    shows up as nothing more than an empty dialog."""
    app = adsk.core.Application.get()
    if app and app.userInterface:
        app.userInterface.messageBox(message)


def build_filters(inputs, panels):
    """The headings and the filter widgets, in a table of their own.

    They sit outside the cut-list table so they stay put while it scrolls. A
    filter you cannot reach without scrolling back up is no use, and the
    headings used to disappear the same way once the list ran past the
    fifteenth row.
    """
    head = inputs.addTableCommandInput(
        HEAD_TABLE_ID, 'Filters', len(bom_core.COLUMNS), COLUMN_RATIO)
    head.hasGrid = False
    head.rowSpacing = 0
    head.columnSpacing = 0
    head.maximumVisibleRows = 2
    head.minimumVisibleRows = 2

    cells = head.commandInputs
    for column, title in enumerate(bom_core.COLUMNS):
        heading = cells.addTextBoxCommandInput(
            'head{}'.format(column), '', '<b>{}</b>'.format(html.escape(title)), 1, True)
        head.addCommandInput(heading, 0, column, 0, 0)

    search = cells.addStringValueInput(FILTER_IDS[bom_core.NAME], '', '')
    search.tooltip = 'Show only parts whose name contains this text.'
    head.addCommandInput(search, 1, bom_core.NAME, 0, 0)

    for column in bom_core.CHOOSABLE:
        drop = cells.addDropDownCommandInput(
            FILTER_IDS[column], '', adsk.core.DropDownStyles.CheckBoxDropDownStyle)
        drop.tooltip = 'Tick the values to show. Nothing ticked shows them all.'
        for value in bom_core.distinct(panels, column):
            drop.listItems.add(value, False)
        head.addCommandInput(drop, 1, column, 0, 0)


def build_table(inputs):
    """The cut list itself, created empty. refresh() puts the rows in."""
    table = inputs.addTableCommandInput(
        TABLE_ID, 'Cut list', len(bom_core.COLUMNS), COLUMN_RATIO)
    table.hasGrid = True

    # Fusion pads every cell and then draws the grid around the padded box,
    # so the lines sit further apart than the single line of text they are
    # meant to box in. The gap accumulates down the table until the rules run
    # through the words. Zero the padding and the grid lands on the rows.
    table.rowSpacing = 0
    table.columnSpacing = 0

    # Order matters: maximumVisibleRows defaults to 4 and minimumVisibleRows
    # cannot be raised above it, so the ceiling has to go up first.
    table.maximumVisibleRows = VISIBLE_ROWS
    table.minimumVisibleRows = VISIBLE_ROWS


def read_filters(inputs):
    """What the user has typed and ticked, as filter_rows() wants it."""
    cells = inputs.itemById(HEAD_TABLE_ID).commandInputs
    search = cells.itemById(FILTER_IDS[bom_core.NAME]).value
    chosen = {}
    for column in bom_core.CHOOSABLE:
        drop = cells.itemById(FILTER_IDS[column])
        chosen[column] = set(item.name for item in drop.listItems if item.isSelected)
    return search, chosen


def fill_table(table, rows):
    _generation[0] += 1
    table.clear()
    cells = table.commandInputs
    for index, row in enumerate(rows[:MAX_TABLE_ROWS]):
        for column, value in enumerate(row):
            cell = cells.addTextBoxCommandInput(
                'cell{}_{}_{}'.format(_generation[0], index, column), '',
                html.escape(bom_core.cell_text(value)), 1, True)
            table.addCommandInput(cell, index, column, 0, 0)


def notes_text(shown, total, others):
    notes = []
    if not shown:
        notes.append('Nothing matches the filters.')
    elif len(shown) != total:
        notes.append('Showing {} of {} parts. The export follows the filters.'
                     .format(len(shown), total))
    if len(shown) > MAX_TABLE_ROWS:
        notes.append('Only the first {} are listed here. The export contains all of them.'
                     .format(MAX_TABLE_ROWS))
    if others:
        label = 'part is' if len(others) == 1 else 'parts are'
        notes.append('{} {} not sheet goods. They go to a separate _other.csv '
                     'beside the cut list, unfiltered.'.format(len(others), label))
    # A text box cannot grow after it is built, so it is made NOTE_ROWS tall
    # and padded out to keep its height steady as the notes come and go.
    return '<br>'.join(notes + [''] * (NOTE_ROWS - len(notes)))


def refresh(inputs):
    """Re-read the filters and rebuild the rows under them."""
    search, chosen = read_filters(inputs)
    shown = bom_core.filter_rows(_state['panels'], search, chosen)
    _state['shown'] = shown
    fill_table(inputs.itemById(TABLE_ID), shown)
    inputs.itemById(NOTES_ID).text = notes_text(
        shown, len(_state['panels']), _state['others'])


def explain(command, text):
    """Say why there is no table, and hide the export button."""
    command.isOKButtonVisible = False
    command.commandInputs.addTextBoxCommandInput(
        'message', '', html.escape(text).replace('\n', '<br>'), text.count('\n') + 2, True)


class ExecuteHandler(adsk.core.CommandEventHandler):
    """OK was pressed, so write the files."""

    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface

            dialog = ui.createFileDialog()
            dialog.title = 'Save cut list'
            dialog.filter = 'CSV files (*.csv)'
            dialog.initialFilename = _state['name'] + '.csv'
            if dialog.showSave() != adsk.core.DialogResults.DialogOK:
                return

            # What the filters left on screen is what gets exported: the
            # point of filtering a cut list is to cut some of it.
            shown = _state['shown']

            panel_path = dialog.filename
            other_path = bom_core.export(shown, _state['others'], panel_path)

            ui.messageBox(
                bom_core.summarise(_state['tally'], shown, _state['others'],
                                   panel_path, other_path),
                'Cut list exported')
        except:
            report('Export failed:\n{}'.format(traceback.format_exc()))


class FilterHandler(adsk.core.InputChangedEventHandler):
    """A filter was typed in or ticked, so rebuild the rows under it."""

    def notify(self, args):
        try:
            if args.input.id not in FILTER_IDS.values():
                return
            refresh(args.firingEvent.sender.commandInputs)
        except:
            report('Filtering failed:\n{}'.format(traceback.format_exc()))


class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    """The button was clicked. The design is read here, not at startup, because
    when the add-in loads there is usually no document open yet."""

    def notify(self, args):
        try:
            command = args.command
            command.cancelButtonText = 'Close'

            design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
            if not design:
                explain(command, 'Open a design first, then try again.')
                return

            panels, others, tally = bom_core.gather(design)
            if not tally:
                explain(command, 'No visible solid bodies found in this design.')
                return
            if not panels:
                found = '\n'.join('    {}  x{}'.format(m, tally[m])
                                  for m in sorted(tally, key=str.lower))
                explain(command,
                        'No sheet-goods parts found.\n\nMaterials in this design:\n' + found
                        + '\n\nAdd the ones that are sheet goods to SHEET_MATERIALS\n'
                          'in bom_core.py.')
                return

            _state.clear()
            _state.update(panels=panels, others=others, tally=tally,
                          name=design.rootComponent.name)

            command.okButtonText = 'Export CSV...'
            command.setDialogInitialSize(
                900, (VISIBLE_ROWS + 2 + NOTE_ROWS) * ROW_HEIGHT + 150)

            inputs = command.commandInputs
            build_filters(inputs, panels)
            build_table(inputs)
            inputs.addTextBoxCommandInput(NOTES_ID, '', '', NOTE_ROWS, True)
            refresh(inputs)

            command.execute.add(_execute)
            command.inputChanged.add(_filter)
        except:
            report('Could not build the dialog:\n{}'.format(traceback.format_exc()))


def run(context):
    global _execute, _filter
    try:
        ui = adsk.core.Application.get().userInterface

        stale = ui.commandDefinitions.itemById(COMMAND_ID)
        if stale:
            stale.deleteMe()
        definition = ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, COMMAND_NAME, COMMAND_TOOLTIP, ICON_FOLDER)

        _execute = ExecuteHandler()
        _handlers.append(_execute)

        _filter = FilterHandler()
        _handlers.append(_filter)

        on_created = CreatedHandler()
        definition.commandCreated.add(on_created)
        _handlers.append(on_created)

        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        tab = workspace.toolbarTabs.itemById(TAB_ID)
        if not tab:
            tab = workspace.toolbarTabs.add(TAB_ID, TAB_NAME)
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = tab.toolbarPanels.add(PANEL_ID, PANEL_NAME)
        control = panel.controls.itemById(COMMAND_ID)
        if not control:
            control = panel.controls.addCommand(definition)
        control.isPromoted = True
    except:
        report('Kraswerk add-in failed to start:\n{}'.format(traceback.format_exc()))


def stop(context):
    """Take the ribbon controls back off. Without this, stopping or removing
    the add-in leaves a dead KRASWERK tab behind until Fusion is reinstalled."""
    try:
        ui = adsk.core.Application.get().userInterface

        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        tab = workspace.toolbarTabs.itemById(TAB_ID) if workspace else None
        panel = tab.toolbarPanels.itemById(PANEL_ID) if tab else None
        if panel:
            control = panel.controls.itemById(COMMAND_ID)
            if control:
                control.deleteMe()
            panel.deleteMe()
        if tab and not tab.isNative and tab.toolbarPanels.count == 0:
            tab.deleteMe()

        definition = ui.commandDefinitions.itemById(COMMAND_ID)
        if definition:
            definition.deleteMe()

        _handlers.clear()
        _state.clear()
    except:
        report('Kraswerk add-in failed to stop cleanly:\n{}'.format(traceback.format_exc()))
