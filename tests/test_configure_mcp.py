import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure_mcp', ROOT / 'scripts/configure_mcp.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConfigurationTests(unittest.TestCase):
    def test_separate_servers_absolute_existing_paths_no_debug(self):
        config = module.configurations(ROOT)['mcpServers']
        self.assertEqual(set(config), {'wayland', 'wayland_observer'})
        for server in config.values():
            self.assertTrue(Path(server['args'][0]).is_absolute())
            self.assertTrue(Path(server['args'][0]).is_file())
            self.assertNotIn('WAYLAND_CU_DEBUG_DIR', server['env'])

    def test_missing_checkout_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                module.configurations(directory)
