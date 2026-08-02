import unittest


class DependencyTests(unittest.TestCase):
    def test_openpyxl_315_is_available(self):
        import openpyxl

        self.assertEqual(openpyxl.__version__, "3.1.5")

    def test_argon2_2310_is_available(self):
        from importlib.metadata import version

        self.assertEqual(version("argon2-cffi"), "23.1.0")
