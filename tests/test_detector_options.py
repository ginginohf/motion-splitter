import unittest
from pathlib import Path

from video_analysis import TimeSmoother, MotionSegments, parse_resolution, analyze_video
from settings_store import normalize, SCHEMA_VERSION, SettingsError


class DetectorOptionsTests(unittest.TestCase):
    def test_resolution_fits_portrait_landscape_and_square_without_upscale(self):
        for original, expected in (((1920, 1080), (640, 360)),
                                   ((1080, 1920), (202, 360)),
                                   ((1000, 1000), (360, 360)),
                                   ((320, 180), (320, 180))):
            self.assertEqual(parse_resolution("640x360", *original), expected)
        self.assertEqual(parse_resolution("Оригинал", 1080, 1920), (1080, 1920))

    def test_smoothing_window_has_same_duration_across_sampling_rates(self):
        for fps in (10, 20, 50):
            smoother = TimeSmoother(.2)
            for i in range(fps):
                smoother.update(i / fps, (i + 1) / fps, 4)
            for i in range(fps // 10):
                value = smoother.update(1 + i / fps, 1 + (i + 1) / fps, 0)
            self.assertAlmostEqual(value, 2)

    def test_irregular_sampling_is_weighted_by_duration(self):
        smoother = TimeSmoother(.2)
        smoother.update(0, .05, 8)
        self.assertAlmostEqual(smoother.update(.05, .2, 0), 2)
        self.assertAlmostEqual(smoother.update(.2, .5, 3), 3)
        self.assertEqual(len(smoother.samples), 1)

    def test_zero_window_disables_smoothing(self):
        smoother = TimeSmoother(0)
        self.assertEqual(smoother.update(0, 1, 10), 10)
        self.assertEqual(smoother.update(1, 2, 0), 0)
        self.assertEqual(len(smoother.samples), 0)

    def test_padding_clamps_to_video_bounds(self):
        events = MotionSegments(0, .5)
        events.sample(.2, 1, True)
        self.assertEqual(events.finish(1, 0, 2, 2), [(0, 1)])

    def test_padding_cannot_rescue_a_short_event(self):
        events = MotionSegments(0, 1)
        events.sample(.2, .3, True)
        events.sample(.3, .4, False)
        self.assertEqual(events.finish(5, 0, 2, 2), [])

    def test_overlapping_padded_intervals_are_merged(self):
        events = MotionSegments(0, .5)
        events.sample(1, 2, True)
        events.sample(2, 2.1, False)
        events.sample(3, 4, True)
        events.sample(4, 4.1, False)
        self.assertEqual(events.finish(6, 0, .5, .5), [(.5, 4.5)])

    def test_version_one_settings_and_presets_gain_new_defaults(self):
        migrated = normalize({"schema_version": 1, "presets": {"old": {"sensitivity": 2}}})
        self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
        for settings in (migrated, migrated["presets"]["old"]):
            self.assertEqual(settings["smoothing_sec"], .2)
            self.assertEqual(settings["pre_motion_sec"], 0)
            self.assertEqual(settings["post_motion_sec"], 0)

    def test_new_options_reject_invalid_numbers(self):
        for key in ("smoothing_sec", "pre_motion_sec", "post_motion_sec"):
            for value in (-1, float("nan"), float("inf")):
                with self.subTest(key=key, value=value), self.assertRaises(SettingsError):
                    normalize({key: value})

    def test_real_video_padding_and_proportional_preview(self):
        fixture = Path(__file__).parent / "fixtures/generated/motion_middle.mp4"
        previews = []
        plain = analyze_video(fixture, 1, .5, 1, "100x100", 0,
                              preview_cb=lambda frame, *args: previews.append(frame.shape[:2]))
        padded = analyze_video(fixture, 1, .5, 1, "100x100", 0,
                               pre_motion_sec=.5, post_motion_sec=.5)
        self.assertEqual(set(previews), {(56, 100)})
        self.assertEqual(len(plain), 1)
        self.assertAlmostEqual(padded[0][0], plain[0][0] - .5)
        self.assertAlmostEqual(padded[0][1], plain[0][1] + .5)


if __name__ == "__main__":
    unittest.main()
