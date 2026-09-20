"""Shared cut-list logic for the FusionBOM scripts.

Walks a design, measures every visible solid body, and groups identical parts.
Used by FusionBOM (straight to CSV) and FusionBOMView (dialog first).

Deliberately imports no adsk modules: everything here works on duck-typed
objects, so it can be exercised outside Fusion.

Dimensions come from each body's own bounding box, read natively off its
component rather than from the world-space occurrence proxy, so a panel
rotated in the assembly still reports its true size. A panel modelled rotated
*inside* its own component is the one case this gets wrong.

Sizes are rough blank sizes: dadoes, rabbets and shaped profiles do not
shrink the bounding box.

Fusion numbers copies -- Police, Police (1), Police (2) -- and those numbers
are folded away again here, so one part copied three times is one row of
three rather than three rows of one.
"""

import os
import re
import csv

# Materials treated as sheet goods and written to the cut list. Matched
# case-insensitively as a substring of the physical material name, so
# "plywood" matches "Plywood 18". Edit to match your own materials.
SHEET_MATERIALS = [
    'plywood',
    'mdf',
    'hdf',
    'chipboard',
    'particle',
    'osb',
    'blockboard',
    'laminate',
    'dyha',
    'lamino',
]

CM_TO_MM = 10.0

# The number Fusion hangs off the end of a copy: "Police (1)". It says which
# copy a part is, not what the part is.
COPY_SUFFIX = re.compile(r'\s*\(\d+\)$')

COLUMNS = ['Name', 'Part Of', 'Qty', 'Length', 'Width', 'Thickness', 'Material']


def is_sheet_material(name):
    low = name.lower()
    return any(m in low for m in SHEET_MATERIALS)


def material_of(body, component):
    if body.material:
        return body.material.name
    if component.material:
        return component.material.name
    return '(none)'


def dimensions_of(body):
    """Return (length, width, thickness) in mm, largest first.

    Rounded to 0.1 mm, which is also the granularity at which parts are
    considered identical for grouping.
    """
    box = body.boundingBox
    extents = [
        (box.maxPoint.x - box.minPoint.x) * CM_TO_MM,
        (box.maxPoint.y - box.minPoint.y) * CM_TO_MM,
        (box.maxPoint.z - box.minPoint.z) * CM_TO_MM,
    ]
    extents.sort(reverse=True)
    return tuple(round(e, 1) for e in extents)


def visible_solids(component):
    return [b for b in component.bRepBodies if b.isSolid and b.isLightBulbOn]


def collect(component, parent_name, counts):
    """Recursively tally every visible solid body under component.

    Bodies are read from the component itself, so a component placed four
    times is visited four times and its parts counted four times over.
    """
    solids = visible_solids(component)
    for body in solids:
        name = component.name if len(solids) == 1 else '{} / {}'.format(component.name, body.name)
        length, width, thickness = dimensions_of(body)
        key = (name, parent_name, length, width, thickness, material_of(body, component))
        counts[key] = counts.get(key, 0) + 1

    for occurrence in component.occurrences:
        if not occurrence.isLightBulbOn:
            continue
        collect(occurrence.component, component.name, counts)


def base_name(name):
    """Drop the copy number from every segment of a part name.

    Names are either "Component" or "Component / Body", and either half can
    be a numbered copy, so both are stripped. A part actually named "(2)" is
    left alone rather than stripped down to nothing.
    """
    return ' / '.join(COPY_SUFFIX.sub('', part) or part for part in name.split(' / '))


def merge_copies(counts):
    """Fold numbered copies of a part into a single tally.

    Police, Police (1) and Police (2) are one part in three copies, and are
    counted as such -- but only when they agree on size and material. When
    they disagree the copy number is the one thing telling the rows apart, so
    that family is left as it was found: three sizes under one name would be
    worse than a number nobody asked for.

    Only the part's own name is folded. Parents keep their copy numbers, so a
    shelf in Skrin and the same shelf in Skrin (1) stay two rows.
    """
    families = {}
    for key, qty in counts.items():
        family = (base_name(key[0]), key[1])
        families.setdefault(family, []).append((key, qty))

    merged = {}
    for (name, parent), members in families.items():
        shapes = set(key[2:] for key, _ in members)
        if len(shapes) == 1:
            merged[(name, parent) + shapes.pop()] = sum(qty for _, qty in members)
        else:
            merged.update(members)
    return merged


def sorted_rows(counts, want_sheet):
    """Rows for either the panels or the leftovers, grouped by material."""
    selected = [
        (key, qty) for key, qty in counts.items()
        if is_sheet_material(key[5]) == want_sheet
    ]
    # Group by material, then biggest part first.
    selected.sort(key=lambda item: (item[0][5].lower(), -item[0][2], -item[0][3]))
    return [
        [name, parent, qty, length, width, thickness, material]
        for (name, parent, length, width, thickness, material), qty in selected
    ]


def material_tally(counts):
    tally = {}
    for key, qty in counts.items():
        tally[key[5]] = tally.get(key[5], 0) + qty
    return tally


def gather(design):
    """Return (panels, others, tally) for the whole design."""
    counts = {}
    collect(design.rootComponent, '(root)', counts)
    counts = merge_copies(counts)
    return sorted_rows(counts, True), sorted_rows(counts, False), material_tally(counts)


def write_csv(path, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)


def export(panels, others, panel_path):
    """Write the cut list, plus a sibling file for non-sheet parts.

    Returns the path of that second file, or None when there was nothing
    to put in it.
    """
    write_csv(panel_path, panels)
    if not others:
        return None
    other_path = os.path.splitext(panel_path)[0] + '_other.csv'
    write_csv(other_path, others)
    return other_path


def summarise(tally, panels, others, panel_path, other_path):
    lines = ['Exported {} panel rows to:'.format(len(panels)), panel_path, '']

    lines.append('Materials found:')
    for material in sorted(tally, key=str.lower):
        mark = 'cut list' if is_sheet_material(material) else 'not a sheet material'
        lines.append('  {}  x{}  ({})'.format(material, tally[material], mark))

    if others:
        label = 'row' if len(others) == 1 else 'rows'
        lines += ['', '{} {} were not sheet goods and went to:'.format(len(others), label), other_path]
        lines.append('If panels are listed there, add their material to SHEET_MATERIALS.')
    return '\n'.join(lines)
