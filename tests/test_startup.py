"""Startup sequencing checks without sockets, model loads, threads or browsers."""
import sys
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import run as startup


URL = 'http://127.0.0.1:8765'
SCHEMA = {'info': {'title': '知校 · 校园知识问答'},
          'paths': {'/api/ask': {}, '/api/status': {}}}


class StartupTests(unittest.TestCase):
    def test_json_probe_bypasses_system_proxy_and_handles_bad_or_missing_response(self):
        response = Mock()
        response.read.return_value = b'{"status":"ok"}'
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = context
        handler = object()
        with patch.object(startup, 'ProxyHandler', return_value=handler) as proxy_handler, \
                patch.object(startup, 'build_opener', return_value=opener) as build:
            self.assertEqual(startup.read_json(URL+'/api/health'), {'status': 'ok'})
            proxy_handler.assert_called_once_with({})
            build.assert_called_once_with(handler)
            opener.open.assert_called_once_with(URL+'/api/health', timeout=1)
            self.assertLessEqual(response.read.call_args.args[0], 1_000_000)
            response.read.return_value = b'not json'
            self.assertIsNone(startup.read_json(URL+'/api/health'))
            opener.open.side_effect = OSError('fixture: unavailable')
            self.assertIsNone(startup.read_json(URL+'/api/health'))

    def test_service_identity_requires_health_title_and_both_expected_api_paths(self):
        with patch.object(startup, 'read_json', side_effect=[{'status': 'ok'}, SCHEMA]) as read:
            self.assertTrue(startup.is_campus_service(URL))
            self.assertEqual(read.call_args_list, [call(URL+'/api/health'), call(URL+'/openapi.json')])
        for schema in [None, {}, {'info': 'not a mapping'},
                       {'info': {'title': 'Other app'}, 'paths': SCHEMA['paths']},
                       {'info': SCHEMA['info'], 'paths': {'/api/status': {}}},
                       {'info': SCHEMA['info'], 'paths': {'/api/ask': {}}}]:
            with self.subTest(schema=schema), \
                    patch.object(startup, 'read_json', side_effect=[{'status': 'ok'}, schema]):
                self.assertFalse(startup.is_campus_service(URL))
        for health in [None, {'status': 'initializing'}, 'ok']:
            with self.subTest(health=health), patch.object(startup, 'read_json', return_value=health) as read:
                self.assertFalse(startup.is_campus_service(URL))
                read.assert_called_once_with(URL+'/api/health')

    def test_browser_waits_for_server_start_and_a_successful_identity_probe(self):
        server = SimpleNamespace(started=False)
        stop = Mock()
        stop.is_set.return_value = False
        with patch.object(startup, 'is_campus_service', side_effect=[False, True]) as probe, \
                patch.object(startup, 'open_browser_url') as browser, \
                patch.object(startup.time, 'monotonic', return_value=0), \
                patch('builtins.print') as printed:
            waited = []

            def wait(interval):
                waited.append(interval)
                browser.assert_not_called()
                if len(waited) == 1:
                    probe.assert_not_called()
                    server.started = True
                elif len(waited) == 2:
                    self.assertEqual(probe.call_count, 1)
                else:
                    self.fail('Watcher did not exit after the successful identity probe')

            stop.wait.side_effect = wait
            startup.watch_readiness(server, URL, stop)
            self.assertEqual(waited, [0.25, 0.25])
            self.assertEqual(probe.call_args_list, [call(URL), call(URL)])
            browser.assert_called_once_with(URL)
            notice = '\n'.join(str(args) for args, _ in printed.call_args_list)
            self.assertIn('就绪', notice)
            self.assertIn(URL, notice)

    def test_no_browser_option_still_confirms_readiness_without_opening_a_window(self):
        server = SimpleNamespace(started=True)
        stop = Mock()
        stop.is_set.return_value = False
        with patch.object(startup, 'is_campus_service', return_value=True) as probe, \
                patch.object(startup, 'open_browser_url') as browser, \
                patch.object(startup.time, 'monotonic', return_value=0), \
                patch('builtins.print'):
            startup.watch_readiness(server, URL, stop, open_browser=False)
            probe.assert_called_once_with(URL)
            browser.assert_not_called()
            stop.wait.assert_not_called()

    def test_slow_initialization_reports_progress_and_stops_only_on_stop_event(self):
        server = SimpleNamespace(started=False, should_exit=False)
        stop = Mock()
        stop.is_set.side_effect = [False, False, False, True]
        with patch.object(startup, 'is_campus_service') as probe, \
                patch.object(startup, 'open_browser_url') as browser, \
                patch.object(startup.time, 'monotonic', side_effect=[0, 10.1, 21.0, 31.1]), \
                patch('builtins.print') as printed:
            startup.watch_readiness(server, URL, stop)
            self.assertEqual(stop.wait.call_args_list, [call(0.25)]*3)
            probe.assert_not_called()
            browser.assert_not_called()
            self.assertFalse(server.should_exit)
            notice = '\n'.join(str(args) for args, _ in printed.call_args_list)
            self.assertIn('加载', notice)
            self.assertGreaterEqual(printed.call_count, 2)

    def test_failed_browser_launch_gives_manual_url_and_success_needs_no_fallback(self):
        for outcome in [False, OSError('fixture: missing browser'), startup.webbrowser.Error('fixture: failed')]:
            with self.subTest(outcome=type(outcome).__name__), \
                    patch.object(startup.webbrowser, 'open') as browser, patch('builtins.print') as printed:
                if isinstance(outcome, Exception):
                    browser.side_effect = outcome
                else:
                    browser.return_value = outcome
                startup.open_browser_url(URL)
                browser.assert_called_once_with(URL)
                notice = '\n'.join(str(args) for args, _ in printed.call_args_list)
                self.assertIn('手动', notice)
                self.assertIn(URL, notice)
        with patch.object(startup.webbrowser, 'open', return_value=True), patch('builtins.print') as printed:
            startup.open_browser_url(URL)
            printed.assert_not_called()

    def test_main_reuses_an_identified_service_without_starting_uvicorn(self):
        for args, should_open in [([], True), (['--no-browser'], False)]:
            fake_uvicorn = Mock()
            with self.subTest(args=args), \
                    patch.object(startup, 'startup_lock', return_value=nullcontext(True)), \
                    patch.object(startup, 'is_campus_service', return_value=True) as probe, \
                    patch.object(startup, 'port_is_listening') as listening, \
                    patch.object(startup, 'open_browser_url') as browser, \
                    patch.dict(sys.modules, {'uvicorn': fake_uvicorn}), patch('builtins.print'):
                self.assertEqual(startup.main(args), 0)
                probe.assert_called_once_with(URL)
                listening.assert_not_called()
                fake_uvicorn.Server.assert_not_called()
                fake_uvicorn.Config.assert_not_called()
                if should_open:
                    browser.assert_called_once_with(URL)
                else:
                    browser.assert_not_called()

    def test_main_does_not_treat_an_unrelated_listener_as_the_project(self):
        fake_uvicorn = Mock()
        with patch.object(startup, 'startup_lock', return_value=nullcontext(True)), \
                patch.object(startup, 'is_campus_service', return_value=False), \
                patch.object(startup, 'port_is_listening', return_value=True), \
                patch.object(startup, 'open_browser_url') as browser, \
                patch.dict(sys.modules, {'uvicorn': fake_uvicorn}), patch('builtins.print') as printed:
            self.assertEqual(startup.main([]), 1)
            browser.assert_not_called()
            fake_uvicorn.Server.assert_not_called()
            notice = '\n'.join(str(args) for args, _ in printed.call_args_list)
            self.assertIn('8765', notice)

    def test_main_does_not_start_a_second_process_while_startup_lock_is_busy(self):
        fake_uvicorn = Mock()
        with patch.object(startup, 'startup_lock', return_value=nullcontext(False)), \
                patch.object(startup, 'is_campus_service', return_value=False), \
                patch.object(startup, 'port_is_listening') as listening, \
                patch.object(startup, 'open_browser_url') as browser, \
                patch.dict(sys.modules, {'uvicorn': fake_uvicorn}), patch('builtins.print') as printed:
            self.assertEqual(startup.main([]), 0)
            listening.assert_not_called()
            browser.assert_not_called()
            fake_uvicorn.Server.assert_not_called()
            fake_uvicorn.Config.assert_not_called()
            notice = '\n'.join(str(args) for args, _ in printed.call_args_list)
            self.assertIn('启动', notice)
            self.assertIn(URL, notice)


if __name__ == '__main__':
    unittest.main()
