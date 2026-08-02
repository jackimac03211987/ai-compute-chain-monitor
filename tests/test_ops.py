import tempfile
import unittest
from pathlib import Path

from ops.rotate_logs import rotate


class OpsTests(unittest.TestCase):
    def test_log_rotation_copy_truncates_and_bounds_history(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"serve.log"; path.write_bytes(b"abcdef")
            self.assertEqual(rotate(root,max_bytes=5,keep=2),["serve.log"])
            self.assertEqual(path.read_bytes(),b""); self.assertEqual((Path(root)/"serve.log.1").read_bytes(),b"abcdef")
            path.write_bytes(b"ghijkl"); rotate(root,max_bytes=5,keep=2)
            self.assertEqual((Path(root)/"serve.log.2").read_bytes(),b"abcdef")


if __name__ == "__main__": unittest.main()
