# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The plugin's default settings file: plugin.toml, with a legacy plugin-only config.toml from 1.2.0 still honoured."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from antechamber_launch import default_config


class DefaultConfig(unittest.TestCase):
    PLUGIN = '[[adapters.notes]]\nid = "n"\nfolder = "/x"\n'
    WRITTEN = "# Written by Antechamber setup_stores. Edit by hand or call setup_stores again.\n\n"

    def home(self, **files):
        h = tempfile.mkdtemp()
        os.makedirs(os.path.join(h, ".config", "antechamber"))
        for name, text in files.items():
            with open(os.path.join(h, ".config", "antechamber", name + ".toml"), "w") as f:
                f.write(text)
        return h

    def name(self, h):
        return os.path.basename(default_config(h))

    def test_nothing_yet_uses_plugin_toml(self):
        self.assertEqual(self.name(self.home()), "plugin.toml")

    def test_legacy_plugin_only_config_is_kept(self):
        self.assertEqual(self.name(self.home(config=self.WRITTEN + self.PLUGIN)), "config.toml")
        self.assertEqual(self.name(self.home(config=self.WRITTEN + '[embedder]\nbackend = "ollama"\n' + self.PLUGIN)), "config.toml")
        # hand-added sections after setup (1.2.0 said the file could be edited) do not make it a service file
        self.assertEqual(self.name(self.home(config=self.WRITTEN + self.PLUGIN + '\n[recall]\nextra_seats = 5\n[index]\nrefresh_minutes = 5\n')), "config.toml")

    def test_service_config_is_not_used(self):
        self.assertEqual(self.name(self.home(config='[shelves]\nadapters = ["a.b:C"]\n')), "plugin.toml")
        self.assertEqual(self.name(self.home(config='[service]\nextensions = ["a.b:c"]\n')), "plugin.toml")

    def test_other_service_style_configs_are_not_used(self):
        for text in ('[service]\nport = 8790\n', '[postgres]\nuser = "u"\n', '[shelves]\nadapters = []\n',
                     '[service]\nextensions = []\n', '[embedder]\nbackend = "ollama"\n', "",
                     '[postgres]\nuser = "u"\n' + self.PLUGIN, '[index]\nrefresh_minutes = 5\n', self.PLUGIN):
            self.assertEqual(self.name(self.home(config=text)), "plugin.toml", text)

    def test_plugin_toml_wins(self):
        self.assertEqual(self.name(self.home(config="", plugin="")), "plugin.toml")

    def test_unreadable_config_is_left_alone(self):
        self.assertEqual(self.name(self.home(config="= not toml")), "plugin.toml")


if __name__ == "__main__":
    unittest.main()
