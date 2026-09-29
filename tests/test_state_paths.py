import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dossier_store


class DossierPathTests(unittest.TestCase):
    def test_identifiers_cannot_escape_state(self):
        for value in ("../outside", "/absolute", "..\\outside", "x:y", "", "123/456"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    dossier_store._json_path(value)
                with self.assertRaises(ValueError):
                    dossier_store._md_path(value)

    def test_numeric_compatibility(self):
        self.assertEqual(dossier_store._json_path("91001").name, "91001.json")
        self.assertEqual(dossier_store._md_path(1).name, "1.md")


if __name__ == "__main__":
    unittest.main()
