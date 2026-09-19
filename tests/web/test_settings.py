import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from wildfire_data.web.app import create_app
from wildfire_data.web.settings import Settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'app'
        (self.root / 'config').mkdir(parents=True)
        self.file = self.root / 'config/.env'
        self.enterContext(patch('wildfire_data.web.settings.REPOSITORY_ROOT', self.root))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def test_local_quoted_credential_loads_without_changing_environment(self):
        self.file.write_text('export MAP_KEY="local-test-key" # FIRMS\nWILDFIRE_ALLOWED_HOSTS=unexpected\n')
        settings = Settings.from_environment()
        self.assertEqual(settings.firms_key, 'local-test-key')
        self.assertNotIn('MAP_KEY', os.environ)
        self.assertEqual(settings.allowed_hosts, ('localhost', '127.0.0.1'))
        self.assertNotIn('local-test-key', repr(settings))

    def test_process_environment_takes_precedence_over_local_aliases(self):
        self.file.write_text('NASA_FIRMS_API_KEY=local-primary\nMAP_KEY=local-alias\n')
        for environment, expected in [
            ({'NASA_FIRMS_API_KEY': 'environment-primary', 'MAP_KEY': 'environment-alias'}, 'environment-primary'),
            ({'MAP_KEY': 'environment-alias'}, 'environment-alias'),
            ({'NASA_FIRMS_API_KEY': '', 'MAP_KEY': 'environment-alias'}, 'environment-alias'),
            ({}, 'local-primary'),
        ]:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                self.assertEqual(Settings.from_environment().firms_key, expected)

    def test_missing_or_empty_credentials_leave_firms_unconfigured(self):
        self.assertEqual(Settings.from_environment().firms_key, '')
        self.file.write_text('NASA_FIRMS_API_KEY=""\nMAP_KEY\n')
        self.assertEqual(Settings.from_environment().firms_key, '')

    def test_parent_environment_files_and_variable_expansion_are_not_loaded(self):
        (self.root.parent / '.env').write_text('MAP_KEY=parent-test-key\n')
        (self.root / '.env').write_text('MAP_KEY=unconfigured-root-file\n')
        self.assertEqual(Settings.from_environment().firms_key, '')
        self.file.write_text('MAP_KEY="${OTHER_KEY}"\n')
        with patch.dict(os.environ, {'OTHER_KEY': 'unrelated-secret'}):
            self.assertEqual(Settings.from_environment().firms_key, '${OTHER_KEY}')

    def test_configuration_reports_availability_without_exposing_local_key(self):
        self.file.write_text('MAP_KEY=local-test-key\n')
        settings = replace(Settings.from_environment(), prepare_data=False)
        with TestClient(create_app(settings=settings, vegetation_sampler=object()), base_url='http://localhost') as client:
            response = client.get('/api/config')
            self.assertTrue(response.json()['firms_configured'])
            self.assertNotIn('local-test-key', response.text)
            self.assertEqual(client.get('/config/.env').status_code, 404)
