"""1.0.1 portability fixes found by the first CI run (Linux / macOS, Python 3.14). Synthetic data only; no network."""
import sys
import unittest

from tests.helpers import ROOT  # noqa: F401  (puts src/ on the path)
from scivetra_kb.metadata import strict_json


class JsonDepthIsDecoderIndependent(unittest.TestCase):
    def test_deep_nesting_refused_before_the_decoder(self):
        """IDs: REL-JSON-DEPTH-PORTABLE. Python 3.14 on Linux / macOS decodes 50 000 nested arrays without RecursionError; the
        refusal must not depend on that: with a huge recursion limit (simulating such a decoder) a 50 000-deep body, closed or
        not, is still NESTING_TOO_DEEP without a path, brackets inside strings do not count, and nesting between MAX_DEPTH
        and DECODER_DEPTH is still refused with its exact JSON-pointer path."""
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 200000))
        try:
            for body in (b'{"data":' + b"[" * 50000 + b"]" * 50000 + b"}", b"[" * 50000):
                with self.assertRaises(strict_json.UnsafeJson) as ctx:
                    strict_json.decode(body)
                self.assertEqual((ctx.exception.reason, ctx.exception.field_path), ("NESTING_TOO_DEEP", None))
        finally:
            sys.setrecursionlimit(old)
        self.assertEqual(strict_json.decode(b'{"t": "' + b"[" * 5000 + b'"}'), {"t": "[" * 5000})
        depth = strict_json.MAX_DEPTH + 5
        with self.assertRaises(strict_json.UnsafeJson) as ctx:
            strict_json.decode(b"[" * depth + b"]" * depth)
        self.assertEqual(ctx.exception.reason, "NESTING_TOO_DEEP")
        self.assertTrue(ctx.exception.field_path.startswith("/0/0"))


if __name__ == "__main__":
    unittest.main()
