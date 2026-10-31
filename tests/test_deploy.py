"""Exercise rollout control flow without touching Docker, Nginx or a live host."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.deploy import Deployment, require_compatible


def ready(release='old', contract='schema'):
    return dict(status='ready', schema_mode='verify', protocol='v1', schema_contract=contract, release=release)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = self.temp.name
        self.d = Deployment(dict(state_dir=root, upstream_file=root+'/upstream', env_file=root+'/.env',
            assets_dir=root+'/assets', nginx_pid_file=root+'/pid', project='fixture', network='fixture',
            data_volume='fixture', edge_ready_url='https://fixture/ready', blue_port=8101, green_port=8102,
            ready_timeout=1, drain_timeout=-1))
        self.d.save({'slot':'blue', 'image':'old'})
        self.d.upstream.write_text(self.d.upstream_text('blue'))
        self.calls = []

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ['docker','run']:
            return json.dumps(ready('candidate'))
        return ''

    def test_rejects_legacy_and_incompatible_baseline(self):
        for report in ({}, ready(contract='different'), {**ready(), 'schema_mode':'auto'}):
            with self.assertRaises(RuntimeError):
                require_compatible(ready(), report)

    def test_success_checks_edge_before_retiring_old_slot(self):
        events = []
        def checked(slot, image, edge=False):
            events.append(('ready',slot,edge))
            return ready(image)
        def retire():
            state = self.d.state()
            if state.get('retiring'):
                events.append(('retire',state['retiring']['slot']))
        with patch('scripts.deploy.run',side_effect=self.fake_run), patch('scripts.deploy.nginx_workers',return_value={'12':'start'}), \
             patch.object(self.d,'wait_ready',side_effect=checked), patch.object(self.d,'prepare',return_value=ready('new')), \
             patch.object(self.d,'retire',side_effect=retire):
            self.d.deploy('new')
        self.assertEqual(self.d.state()['slot'],'green')
        self.assertEqual(self.d.state()['previous_image'],'old')
        self.assertLess(events.index(('ready','green',True)),events.index(('retire','blue')))
        self.assertFalse(self.d.journal.exists())
        self.assertEqual(self.calls[-2:], [['nginx','-t'],['systemctl','reload','nginx']])

    def test_candidate_failure_restores_old_route_and_retains_candidate_for_drain(self):
        with patch('scripts.deploy.run',side_effect=self.fake_run), patch('scripts.deploy.nginx_workers',return_value={'12':'start'}), \
             patch.object(self.d,'wait_ready',return_value=ready()), patch.object(self.d,'prepare',side_effect=RuntimeError('candidate unhealthy')):
            with self.assertRaisesRegex(RuntimeError,'candidate unhealthy'):
                self.d.deploy('new')
        self.assertEqual(self.d.state()['slot'],'blue')
        self.assertEqual(self.d.state()['retiring']['slot'],'green')
        self.assertEqual(self.d.upstream.read_text(), self.d.upstream_text('blue'))
        self.assertFalse(any(call[:2] == ['docker','stop'] for call in self.calls))

    def test_failed_edge_check_rolls_back(self):
        def checked(slot, image, edge=False):
            if slot == 'green' and edge:
                raise RuntimeError('edge failure')
            return ready(image)
        with patch('scripts.deploy.run',side_effect=self.fake_run), patch('scripts.deploy.nginx_workers',return_value={'12':'start'}), \
             patch.object(self.d,'wait_ready',side_effect=checked), patch.object(self.d,'prepare',return_value=ready('new')):
            with self.assertRaisesRegex(RuntimeError,'edge failure'):
                self.d.deploy('new')
        self.assertEqual(self.d.state()['slot'],'blue')
        self.assertEqual(sum(c == ['systemctl','reload','nginx'] for c in self.calls),2)

    def test_failed_recovery_keeps_journal_and_blocks_next_deploy(self):
        self.d.journal.write_text(json.dumps({'old':self.d.state(),'target':'green','upstream':'old'}))
        with patch.object(self.d,'wait_ready',side_effect=RuntimeError('old unavailable')):
            with self.assertRaises(RuntimeError):
                self.d.recover()
        self.assertTrue(self.d.journal.exists())
        with self.assertRaisesRegex(RuntimeError,'Interrupted rollout'):
            self.d.deploy('new')

    def test_retire_never_stops_services_with_old_requests_in_flight(self):
        self.d.save({'slot':'green','image':'new','retiring':{'slot':'blue','workers':{'12':'start'}}})
        with patch.object(self.d,'wait_ready',return_value=ready('new')), patch('scripts.deploy.process_identity',return_value='start'), \
             patch('scripts.deploy.run',side_effect=self.fake_run):
            with self.assertRaisesRegex(RuntimeError,'still draining'):
                self.d.retire()
        self.assertEqual(self.calls, [])
        self.assertIn('retiring', self.d.state())

    def test_drained_services_stop_without_force_kill(self):
        self.d.save({'slot':'green','image':'new','retiring':{'slot':'blue','workers':{'12':'start'}}})
        with patch.object(self.d,'wait_ready',return_value=ready('new')), patch('scripts.deploy.process_identity',return_value=None), \
             patch.object(self.d,'container',side_effect=lambda service:service), patch('scripts.deploy.run',side_effect=self.fake_run):
            self.d.retire()
        self.assertEqual(self.calls,[['docker','stop','--timeout','-1','api-blue'],['docker','stop','--timeout','-1','worker-blue']])
        self.assertNotIn('retiring',self.d.state())

    def test_upstream_drift_does_not_start_candidate(self):
        self.d.upstream.write_text('server unexpected:80;')
        with patch.object(self.d,'wait_ready',return_value=ready()), patch.object(self.d,'prepare') as prepare:
            with self.assertRaisesRegex(RuntimeError,'drift'):
                self.d.deploy('new')
            prepare.assert_not_called()

    def test_assets_append_without_overwriting_previous_release(self):
        assets = Path(self.d.c['assets_dir'])
        assets.mkdir()
        (assets/'old.js').write_bytes(b'old chunk')
        def copy(args, **kwargs):
            if args[:2] == ['docker','cp']:
                (Path(args[-1])/'new.js').write_bytes(b'new chunk')
            return ''
        with patch('scripts.deploy.run',side_effect=copy):
            self.d.copy_assets('image')
        self.assertEqual((assets/'old.js').read_bytes(),b'old chunk')
        self.assertEqual((assets/'new.js').read_bytes(),b'new chunk')
        (assets/'new.js').write_bytes(b'conflicting chunk')
        with patch('scripts.deploy.run',side_effect=copy):
            with self.assertRaisesRegex(RuntimeError,'collision'):
                self.d.copy_assets('image')
        self.assertEqual((assets/'new.js').read_bytes(),b'conflicting chunk')
