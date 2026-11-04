import unittest
from unittest import mock
from openhands.runtime.plugins.agent_skills.file_ops.file_ops import append_file

class TestAppendFile(unittest.TestCase):
    def setUp(self):
        self.mock_open = mock.mock_open()
        self.open = self.mock_open

    def test_append(self):
        append_file('test.txt', 'new content')
        self.mock_open().write.assert_called_once_with('new content')

    def test_append_exception(self):
        self.open().write.side_effect = Exception('Error')
        with self.assertRaises(Exception):
            append_file('test.txt', 'content')

if __name__ == '__main__':
    unittest.main()