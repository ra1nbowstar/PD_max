import unittest

import numpy as np

from app.ai_detection.core.detectors import PixelLevelDetector


class PixelOverlapDetectorTests(unittest.TestCase):
    def test_detect_overlap_on_edge_band_difference(self):
        detector = PixelLevelDetector()
        uniform = np.full((120, 160, 3), 200, dtype=np.uint8)
        edged = uniform.copy()
        edged[:, :18] = 40
        edged[:, -18:] = 40

        uniform_score = detector.detect_overlap(uniform)
        edged_score = detector.detect_overlap(edged)

        self.assertGreater(edged_score, uniform_score)
        self.assertGreaterEqual(uniform_score, 0.0)
        self.assertLessEqual(edged_score, 1.0)


if __name__ == "__main__":
    unittest.main()
