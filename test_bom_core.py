"""Tests for bom_core, run outside Fusion.

bom_core deliberately imports no adsk modules and touches its inputs only
through duck typing, so the whole of it can be exercised against the fakes
below -- no Fusion, no licence, no open document.

Run it with:

    python3 test_bom_core.py

There is no test framework here on purpose: an add-in that ships as a folder
of loose files into Fusion's Python should not need one installed to be
checked.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bom_core


# --- Stand-ins for the Fusion objects bom_core walks ------------------------

class Point:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class BoundingBox:
    """Fusion works in centimetres, so millimetres are converted on the way in
    and the test cases below can be written in the units a cut list uses."""

    def __init__(self, length, width, thickness):
        self.minPoint = Point(0, 0, 0)
        self.maxPoint = Point(length / 10.0, width / 10.0, thickness / 10.0)


class Material:
    def __init__(self, name):
        self.name = name


class Body:
    def __init__(self, name, length, width, thickness, material='Plywood 18',
                 visible=True, solid=True):
        self.name = name
        self.isSolid = solid
        self.isLightBulbOn = visible
        self.boundingBox = BoundingBox(length, width, thickness)
        self.material = Material(material)


class Occurrence:
    def __init__(self, component, visible=True):
        self.component = component
        self.isLightBulbOn = visible


class Component:
    def __init__(self, name, bodies=(), children=()):
        self.name = name
        self.bRepBodies = list(bodies)
        self.occurrences = [c if isinstance(c, Occurrence) else Occurrence(c)
                            for c in children]
        self.material = None


class Design:
    def __init__(self, root):
        self.rootComponent = root


def gather(root):
    return bom_core.gather(Design(root))


# --- Runner ----------------------------------------------------------------

_failures = []
_case = ['']


def check(label, got, want):
    if got == want:
        return
    _failures.append('{}: {}\n      got  {!r}\n      want {!r}'
                     .format(_case[0], label, got, want))


def report_rows(rows):
    for row in rows:
        print('      {!r} part of {!r}  x{}  {}x{}x{}  {}'.format(*row[:2] + row[2:]))


def run(cases):
    for case in cases:
        _case[0] = case.__name__
        before = len(_failures)
        print('  {}'.format(case.__name__.replace('_', ' ')))
        case()
        if len(_failures) > before:
            print('    FAILED')


# --- Cases -----------------------------------------------------------------

def three_identical_copies_become_one_row():
    panels, _, tally = gather(Component('Skrin', children=[
        Component('Police', [Body('Body1', 800, 300, 18)]),
        Component('Police (1)', [Body('Body1', 800, 300, 18)]),
        Component('Police (2)', [Body('Body1', 800, 300, 18)]),
    ]))
    report_rows(panels)
    check('rows', len(panels), 1)
    check('name', panels[0][0], 'Police')
    check('qty', panels[0][2], 3)
    # Merging moves quantities between rows; it must not invent or lose any.
    check('tally', tally, {'Plywood 18': 3})


def a_copy_of_a_different_size_keeps_its_number():
    panels, _, _ = gather(Component('Skrin', children=[
        Component('Police', [Body('Body1', 800, 300, 18)]),
        Component('Police (1)', [Body('Body1', 800, 300, 18)]),
        Component('Police (2)', [Body('Body1', 600, 300, 18)]),
    ]))
    report_rows(panels)
    # The two 800s are not merged either: with a third size in the family the
    # numbers are all that tell the rows apart, so none of them are dropped.
    check('rows', len(panels), 3)
    check('names', sorted(r[0] for r in panels), ['Police', 'Police (1)', 'Police (2)'])
    check('quantities', sorted(r[2] for r in panels), [1, 1, 1])


def a_copy_of_a_different_material_keeps_its_number():
    panels, _, _ = gather(Component('Skrin', children=[
        Component('Police', [Body('Body1', 800, 300, 18)]),
        Component('Police (1)', [Body('Body1', 800, 300, 18, 'MDF 18')]),
    ]))
    report_rows(panels)
    check('rows', len(panels), 2)
    check('names', sorted(r[0] for r in panels), ['Police', 'Police (1)'])


def a_copied_parent_stays_a_separate_row():
    panels, _, _ = gather(Component('Nabytek', children=[
        Component('Skrin', children=[Component('Police', [Body('Body1', 800, 300, 18)])]),
        Component('Skrin (1)', children=[Component('Police', [Body('Body1', 800, 300, 18)])]),
    ]))
    report_rows(panels)
    check('rows', len(panels), 2)
    check('parents', sorted(r[1] for r in panels), ['Skrin', 'Skrin (1)'])
    check('quantities', sorted(r[2] for r in panels), [1, 1])


def copies_merge_within_each_copied_parent():
    shelves = lambda: [Component('Police', [Body('Body1', 800, 300, 18)]),
                       Component('Police (1)', [Body('Body1', 800, 300, 18)])]
    panels, _, _ = gather(Component('Nabytek', children=[
        Component('Skrin', children=shelves()),
        Component('Skrin (1)', children=shelves()),
    ]))
    report_rows(panels)
    check('rows', len(panels), 2)
    check('parents', sorted(r[1] for r in panels), ['Skrin', 'Skrin (1)'])
    check('quantities', sorted(r[2] for r in panels), [2, 2])


def both_halves_of_a_multi_body_name_are_folded():
    bodies = lambda: [Body('Body1', 800, 300, 18), Body('Body2', 400, 200, 18)]
    panels, _, _ = gather(Component('Skrin', children=[
        Component('Police', bodies()),
        Component('Police (1)', bodies()),
    ]))
    report_rows(panels)
    check('names', sorted(r[0] for r in panels), ['Police / Body1', 'Police / Body2'])
    check('quantities', sorted(r[2] for r in panels), [2, 2])


def parts_of_one_size_but_different_names_stay_apart():
    panels, _, _ = gather(Component('Skrin', children=[
        Component('Police', [Body('Body1', 800, 300, 18)]),
        Component('Bok', [Body('Body1', 800, 300, 18)]),
    ]))
    report_rows(panels)
    check('names', sorted(r[0] for r in panels), ['Bok', 'Police'])
    check('quantities', sorted(r[2] for r in panels), [1, 1])


def a_lone_copy_loses_its_number():
    # The original was deleted and only Police (3) is left. Nothing else can
    # claim the name, so it takes it.
    panels, _, _ = gather(Component('Skrin', children=[
        Component('Police (3)', [Body('Body1', 800, 300, 18)]),
    ]))
    report_rows(panels)
    check('name', panels[0][0], 'Police')


def non_sheet_parts_merge_the_same_way():
    panels, others, _ = gather(Component('Skrin', children=[
        Component('Nozka', [Body('Body1', 100, 30, 30, 'Steel')]),
        Component('Nozka (1)', [Body('Body1', 100, 30, 30, 'Steel')]),
    ]))
    report_rows(others)
    check('no panels', panels, [])
    check('rows', len(others), 1)
    check('qty', others[0][2], 2)


def hidden_and_surface_bodies_are_still_skipped():
    panels, _, _ = gather(Component('Skrin', children=[
        Component('Police', [Body('Body1', 800, 300, 18)]),
        Component('Police (1)', [Body('Body1', 800, 300, 18, visible=False)]),
        Component('Police (2)', [Body('Body1', 800, 300, 18, solid=False)]),
        Occurrence(Component('Police (3)', [Body('Body1', 800, 300, 18)]), visible=False),
    ]))
    report_rows(panels)
    check('rows', len(panels), 1)
    check('qty', panels[0][2], 1)


def names_that_only_look_like_copies_are_left_alone():
    for name in ['Box (A)', 'Police 1', 'Police (1) Left', '(2)', 'Police (10)']:
        got = bom_core.base_name(name)
        want = 'Police' if name == 'Police (10)' else name
        print('      {!r} -> {!r}'.format(name, got))
        check(name, got, want)


# --- Filtering ---------------------------------------------------------------

def sample():
    """A cut list with enough variety for the filters to bite on."""
    panels, _, _ = gather(Component('Skrin', children=[
        Component('Police', [Body('Body1', 800, 300, 18)]),
        Component('BokL', [Body('Body1', 1770, 330, 18)]),
        Component('BokP', [Body('Body1', 1770, 330, 18, 'MDF 12')]),
        Component('Zada', [Body('Body1', 1770, 800, 4, 'HDF 4')]),
    ]))
    return panels


def names(rows):
    return sorted(row[bom_core.NAME] for row in rows)


def no_filters_let_everything_through():
    rows = sample()
    check('untouched', names(bom_core.filter_rows(rows)), names(rows))
    check('empty search', names(bom_core.filter_rows(rows, '')), names(rows))
    # A tick-list nobody has touched is not a filter that excludes everything.
    check('empty tick-lists',
          names(bom_core.filter_rows(rows, '', {bom_core.MATERIAL: set()})), names(rows))


def the_name_search_is_a_case_insensitive_substring():
    rows = sample()
    check('exact', names(bom_core.filter_rows(rows, 'Police')), ['Police'])
    check('lowercased', names(bom_core.filter_rows(rows, 'police')), ['Police'])
    check('substring', names(bom_core.filter_rows(rows, 'bok')), ['BokL', 'BokP'])
    check('surrounding space', names(bom_core.filter_rows(rows, '  bok  ')), ['BokL', 'BokP'])
    check('no match', bom_core.filter_rows(rows, 'zzz'), [])


def a_tick_list_keeps_only_the_ticked_values():
    rows = sample()
    got = bom_core.filter_rows(rows, '', {bom_core.MATERIAL: set(['MDF 12'])})
    check('one material', names(got), ['BokP'])
    got = bom_core.filter_rows(rows, '', {bom_core.MATERIAL: set(['MDF 12', 'HDF 4'])})
    check('two materials', names(got), ['BokP', 'Zada'])


def thickness_matches_as_the_text_on_screen():
    rows = sample()
    # The row holds 18.0 as a float; the tick-list holds "18.0" as a string.
    got = bom_core.filter_rows(rows, '', {bom_core.THICKNESS: set(['18.0'])})
    check('18mm', names(got), ['BokL', 'BokP', 'Police'])
    check('4mm', names(bom_core.filter_rows(rows, '', {bom_core.THICKNESS: set(['4.0'])})),
          ['Zada'])


def filters_combine_as_and():
    rows = sample()
    got = bom_core.filter_rows(rows, 'bok', {bom_core.THICKNESS: set(['18.0'])})
    check('name and thickness', names(got), ['BokL', 'BokP'])
    got = bom_core.filter_rows(rows, 'bok', {bom_core.MATERIAL: set(['MDF 12'])})
    check('name and material', names(got), ['BokP'])
    got = bom_core.filter_rows(rows, 'police', {bom_core.MATERIAL: set(['MDF 12'])})
    check('contradictory', got, [])


def distinct_deduplicates_and_sorts_numerically():
    rows = sample()
    check('thicknesses', bom_core.distinct(rows, bom_core.THICKNESS), ['4.0', '18.0'])
    check('parents', bom_core.distinct(rows, bom_core.PART_OF), ['Skrin'])


def filtering_does_not_alter_the_rows():
    rows = sample()
    before = [list(row) for row in rows]
    bom_core.filter_rows(rows, 'bok', {bom_core.MATERIAL: set(['MDF 12'])})
    check('rows untouched', [list(row) for row in rows], before)


CASES = [
    three_identical_copies_become_one_row,
    a_copy_of_a_different_size_keeps_its_number,
    a_copy_of_a_different_material_keeps_its_number,
    a_copied_parent_stays_a_separate_row,
    copies_merge_within_each_copied_parent,
    both_halves_of_a_multi_body_name_are_folded,
    parts_of_one_size_but_different_names_stay_apart,
    a_lone_copy_loses_its_number,
    non_sheet_parts_merge_the_same_way,
    hidden_and_surface_bodies_are_still_skipped,
    names_that_only_look_like_copies_are_left_alone,
    no_filters_let_everything_through,
    the_name_search_is_a_case_insensitive_substring,
    a_tick_list_keeps_only_the_ticked_values,
    thickness_matches_as_the_text_on_screen,
    filters_combine_as_and,
    distinct_deduplicates_and_sorts_numerically,
    filtering_does_not_alter_the_rows,
]


def main():
    run(CASES)
    print('')
    if _failures:
        for failure in _failures:
            print('  FAIL  {}'.format(failure))
        print('\n{} of {} cases failed.'.format(len(_failures), len(CASES)))
        return 1
    print('All {} cases passed.'.format(len(CASES)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
