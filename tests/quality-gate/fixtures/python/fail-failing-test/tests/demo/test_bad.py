import unittest


class Bad(unittest.TestCase):
    def test_fails(self):
        self.fail("seeded failure")
