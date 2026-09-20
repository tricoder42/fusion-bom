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

# Fusion drops event handlers that are only referenced locally.
_handlers = []
_state = {}
_execute = None


def report(message):
    """Surface an error. Fusion swallows exceptions raised inside handlers,
    so anything that goes wrong in notify() has to be reported by hand or it
    shows up as nothing more than an empty dialog."""
    app = adsk.core.Application.get()
    if app and app.userInterface:
        app.userInterface.messageBox(message)


def build_table(inputs, panels):
    table = inputs.addTableCommandInput(
        'bomTable', 'Cut list', len(bom_core.COLUMNS), COLUMN_RATIO)
    table.hasGrid = True

    # Fusion pads every cell and then draws the grid around the padded box,
    # so the lines sit further apart than the single line of text they are
    # meant to box in. The gap accumulates down the table until the rules run
    # through the words. Zero the padding and the grid lands on the rows.
    table.rowSpacing = 0
    table.columnSpacing = 0

    cells = table.commandInputs
    for column, title in enumerate(bom_core.COLUMNS):
        header = cells.addTextBoxCommandInput(
            'head{}'.format(column), '', '<b>{}</b>'.format(html.escape(title)), 1, True)
        table.addCommandInput(header, 0, column, 0, 0)

    for index, row in enumerate(panels[:MAX_TABLE_ROWS]):
        for column, value in enumerate(row):
            cell = cells.addTextBoxCommandInput(
                'cell{}_{}'.format(index, column), '', html.escape(str(value)), 1, True)
            table.addCommandInput(cell, index + 1, column, 0, 0)

    # Order matters: maximumVisibleRows defaults to 4 and minimumVisibleRows
    # cannot be raised above it, so the ceiling has to go up first.
    table.maximumVisibleRows = VISIBLE_ROWS
    table.minimumVisibleRows = VISIBLE_ROWS


def build_notes(inputs, panels, others):
    notes = []
    if len(panels) > MAX_TABLE_ROWS:
        notes.append('Showing the first {} of {} rows. The export contains all of them.'
                     .format(MAX_TABLE_ROWS, len(panels)))
    if len(others) == 1:
        notes.append('1 part is not sheet goods. It goes to a separate '
                     '_other.csv beside the cut list.')
    elif others:
        notes.append('{} parts are not sheet goods. They go to a separate '
                     '_other.csv beside the cut list.'.format(len(others)))
    if notes:
        inputs.addTextBoxCommandInput('notes', '', '<br>'.join(notes), len(notes) + 1, True)


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

            panel_path = dialog.filename
            other_path = bom_core.export(_state['panels'], _state['others'], panel_path)

            ui.messageBox(
                bom_core.summarise(_state['tally'], _state['panels'], _state['others'],
                                   panel_path, other_path),
                'Cut list exported')
        except:
            report('Export failed:\n{}'.format(traceback.format_exc()))


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
            command.setDialogInitialSize(900, VISIBLE_ROWS * ROW_HEIGHT + 150)
            build_table(command.commandInputs, panels)
            build_notes(command.commandInputs, panels, others)
            command.execute.add(_execute)
        except:
            report('Could not build the dialog:\n{}'.format(traceback.format_exc()))


def run(context):
    global _execute
    try:
        ui = adsk.core.Application.get().userInterface

        stale = ui.commandDefinitions.itemById(COMMAND_ID)
        if stale:
            stale.deleteMe()
        definition = ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, COMMAND_NAME, COMMAND_TOOLTIP, ICON_FOLDER)

        _execute = ExecuteHandler()
        _handlers.append(_execute)

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
