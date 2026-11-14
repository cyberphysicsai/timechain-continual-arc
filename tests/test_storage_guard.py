"""Low storage refuses new inference without touching trial or other state."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from go_bridge import CANONICAL, GoBridge


class StorageGuardTests(unittest.TestCase):
    def test_no_upstream_request_when_controller_storage_is_below_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / 'provider.jsonl'
            bridge = GoBridge('synthetic-key', journal)
            try:
                url = bridge.start() + '/chat/completions'
                with patch('go_bridge.shutil.disk_usage', return_value=SimpleNamespace(free=0)), \
                        patch('go_bridge.urllib.request.urlopen') as upstream:
                    response = httpx.post(url, json={'model': CANONICAL,
                        'messages': [{'role': 'system', 'content': '[synthetic-salt]\nfixture'}]})
                    self.assertEqual(403, response.status_code)
                    upstream.assert_not_called()
                self.assertTrue(bridge.stopped)
                self.assertFalse(journal.exists())
                events = [json.loads(s) for s in journal.with_suffix('.storage-events.jsonl').read_text().splitlines()]
                self.assertEqual(1, len(events))
                self.assertEqual('storage_reserve_reached', events[0]['event'])
                self.assertFalse(events[0]['upstream_request_issued'])
            finally:
                bridge.close()


if __name__ == '__main__':
    unittest.main()
