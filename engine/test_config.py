# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Settings come from a file per machine, never from the code (12 Oct 2026).
Run: python3 -m unittest test_config -v   (from engine/)"""
import importlib, os, tempfile, unittest
import config


def load(path):
    os.environ["ANTECHAMBER_CONFIG"] = path
    try:
        return importlib.reload(config)
    finally:
        del os.environ["ANTECHAMBER_CONFIG"]


class Settings(unittest.TestCase):
    def tearDown(self):
        importlib.reload(config)

    def test_a_missing_file_is_reported_not_hidden(self):
        c = load("/nonexistent/antechamber.toml")
        self.assertFalse(c.LOADED)          # the service's /health turns this into ok: false
        self.assertEqual(c.PG_DB, "antechamber")

    def test_the_file_decides_every_machine_value(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[postgres]\nbin_dir = "/pg/bin"\nhost = "/sock"\nport = 6000\nuser = "u"\ndatabase = "d"\n'
                    '[embedder]\nurl = "http://e/api/embed"\nmodel = "m"\ndimensions = 8\n'
                    '[service]\nport = 9000\n')
        c = load(f.name)
        os.unlink(f.name)
        self.assertTrue(c.LOADED)
        self.assertEqual(c.psql_cmd(), ["/pg/bin/psql", "-h", "/sock", "-p", "6000", "-U", "u", "-d", "d"])
        self.assertEqual(c.psql_cmd("other")[-1], "other")
        self.assertEqual((c.EMBED_URL, c.EMBED_MODEL, c.EMBED_DIMS, c.SERVICE_PORT), ("http://e/api/embed", "m", 8, 9000))

    def test_no_adapters_or_extensions_unless_the_file_names_them(self):
        c = load("/nonexistent/antechamber.toml")
        self.assertEqual((c.SHELF_ADAPTERS, c.SHELF_IMPORT_ROOT, c.SERVICE_EXTENSIONS), ([], "..", []))
        self.assertEqual(c.get("anything", "key", 7), 7)

    def test_the_file_names_adapters_extensions_and_any_section(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[shelves]\nimport_root = "/x"\nadapters = ["pkg.a:SHELVES", "pkg.b:MORE"]\n'
                    '[service]\nextensions = ["pkg.svc:register"]\n[mine]\nflag = false\n')
        c = load(f.name)
        os.unlink(f.name)
        self.assertEqual(c.SHELF_ADAPTERS, ["pkg.a:SHELVES", "pkg.b:MORE"])
        self.assertEqual(c.SHELF_IMPORT_ROOT, "/x")
        self.assertEqual(c.SERVICE_EXTENSIONS, ["pkg.svc:register"])
        self.assertIs(c.get("mine", "flag", True), False)
        self.assertIsNone(c.get("mine", "absent"))


    def test_raw_settings_carry_the_config_folder_and_refresh_minutes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[index]\nrefresh_minutes = 5\n[[adapters.notes]]\nid = "n"\nfolder = "x"\n')
        c = load(f.name)
        os.unlink(f.name)
        self.assertEqual(c.INDEX_REFRESH_MINUTES, 5.0)
        self.assertEqual(c.raw()["_config_dir"], os.path.dirname(os.path.abspath(f.name)))
        self.assertEqual(c.raw()["adapters"]["notes"][0]["id"], "n")
        c.raw()["adapters"]["notes"].clear()
        self.assertEqual(c.raw()["adapters"]["notes"][0]["id"], "n")
        c = load("/nonexistent/antechamber.toml")
        self.assertEqual(c.INDEX_REFRESH_MINUTES, 15.0)
        self.assertEqual(set(c.raw()), {"_config_dir"})


if __name__ == "__main__":
    unittest.main()
