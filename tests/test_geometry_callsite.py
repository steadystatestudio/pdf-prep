"""Exercise the workflow's mode validation without a GUI or desktop preferences."""
from types import SimpleNamespace

import pytest

from pdf_prep.smoke import _validate_minimum_geometry


def window(size, minimum, maximized=False):
    return SimpleNamespace(size=lambda: size, minimumSize=lambda: minimum,
                           isMaximized=lambda: maximized, isMinimized=lambda: False, isFullScreen=lambda: False,
                           width=lambda: size[0], height=lambda: size[1],
                           minimumWidth=lambda: minimum[0], minimumHeight=lambda: minimum[1])


def facts():
    return dict(geometry_mode='validated-constrained-maximized', constrained_native_maximum=True,
                geometry_constraint=dict(source='x11-ewmh', reason='preferred-minimum-exceeds-validated-workarea',
                                         preferred_minimum=[780, 744], client_capacity=[1200, 692]))


def test_workflow_accepts_only_measured_max_mode_without_claiming_normal_minimum():
    actual = window((1222, 714), (780, 714), maximized=True)
    _validate_minimum_geometry(actual, facts())
    for change in ('unverified', 'not-constrained', 'not-maximized'):
        report = facts()
        candidate = actual
        if change == 'unverified':
            report['geometry_constraint']['source'] = 'captured-qt'
        elif change == 'not-constrained':
            report['geometry_constraint']['preferred_minimum'] = [780, 600]
        else:
            candidate = window((1222, 714), (780, 714), maximized=False)
        with pytest.raises(AssertionError):
            _validate_minimum_geometry(candidate, report)


def test_workflow_normal_mode_keeps_exact_minimum_assertion():
    report = {'geometry_mode': 'exact-normal-minimum'}
    _validate_minimum_geometry(window((780, 627), (780, 627)), report)
    with pytest.raises(AssertionError):
        _validate_minimum_geometry(window((800, 627), (780, 627)), report)
    with pytest.raises(AssertionError):
        _validate_minimum_geometry(window((780, 627), (780, 627)), {'geometry_mode': 'unknown'})
