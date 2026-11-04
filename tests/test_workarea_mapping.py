import unittest

from pdf_prep.workarea_mapping import normalize_workarea


def payload(root=(0, 0, 1280, 800), area=(58, 41, 1222, 759), monitors=None):
    return {
        "schema": 1,
        "source": "x11-ewmh",
        "coordinate_space": "x11-root",
        "root": root,
        "workarea": area,
        "monitors": monitors,
    }


class Mapping(unittest.TestCase):
    def test_native_root_units_not_assumed_physical(self):
        for dpr in (1, 2):
            result, details = normalize_workarea(
                payload(), [{"geometry": (0, 0, 1280, 800), "dpr": dpr}], 0
            )
            self.assertEqual(result, (58, 41, 1222, 759))
            self.assertEqual(details["coordinate_scale"], 1)

    def test_explicit_fractional_qt_scaling_applied_once_inward(self):
        result, details = normalize_workarea(
            payload(), [{"geometry": (0, 0, 853, 533), "dpr": 3}], 0, 1.5
        )
        self.assertEqual(result, (39, 28, 814, 505))
        self.assertEqual(details["coordinate_scale"], 1.5)

    def test_uniform_multiple_monitors_with_negative_qt_origin(self):
        data = payload(
            (0, 0, 2560, 800),
            (58, 41, 2502, 759),
            [(0, 0, 1280, 800), (1280, 0, 1280, 800)],
        )
        screens = [
            {"geometry": (-1280, 0, 1280, 800), "dpr": 1},
            {"geometry": (0, 0, 1280, 800), "dpr": 1},
        ]
        self.assertEqual(
            normalize_workarea(data, screens, 0)[0], (-1222, 41, 1222, 759)
        )
        self.assertEqual(normalize_workarea(data, screens, 1)[0], (0, 41, 1280, 759))

    def test_multi_monitor_without_native_layout_falls_back(self):
        screens = [
            {"geometry": (0, 0, 640, 800), "dpr": 1},
            {"geometry": (640, 0, 640, 800), "dpr": 1},
        ]
        self.assertIsNone(normalize_workarea(payload(), screens, 0)[0])

    def test_mixed_dpi_is_not_guessed(self):
        screens = [
            {"geometry": (0, 0, 640, 800), "dpr": 1},
            {"geometry": (640, 0, 640, 800), "dpr": 2},
        ]
        self.assertEqual(
            normalize_workarea(payload(), screens, 0)[1]["reason"],
            "mixed-device-scales-unverified",
        )

    def test_invalid_or_unrelated_root_does_not_replace_qt(self):
        screen = [{"geometry": (0, 0, 1280, 800), "dpr": 1}]
        for data in (
            payload(area=(58, 41, 1280, 800)),
            payload(root=(0, 0, 1600, 900)),
            payload(area=(True, 0, 100, 100)),
            {"schema": 99},
        ):
            with self.subTest(data=data):
                self.assertIsNone(normalize_workarea(data, screen, 0)[0])

    def test_monitor_islands_fail_root_and_monitor_validation(self):
        screens = [
            {"geometry": (0, 0, 640, 400), "dpr": 2},
            {"geometry": (1280, 0, 640, 400), "dpr": 2},
        ]
        data = payload(
            (0, 0, 2560, 800),
            (0, 41, 2560, 759),
            [(0, 0, 1280, 800), (1280, 0, 1280, 800)],
        )
        self.assertIsNone(normalize_workarea(data, screens, 0)[0])


if __name__ == "__main__":
    unittest.main()
