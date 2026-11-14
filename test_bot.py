import datetime as dt
import unittest
import urllib.error
import ssl
from unittest.mock import patch
from zoneinfo import ZoneInfo
from bot import tick_result, telegram


class TelegramErrors(unittest.TestCase):
    def test_auth_failure_without_secret(self):
        token = '123:SECRET'
        error = urllib.error.HTTPError('https://api.telegram.org/bot' + token, 401, 'SECRET', {}, None)
        with patch('bot.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                telegram(token, 'getMe')
        self.assertIn('401', str(caught.exception))
        self.assertNotIn('SECRET', str(caught.exception))

    def test_certificate_failure_is_distinct(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError('SECRET'))
        with patch('bot.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                telegram('123:SECRET', 'getMe')
        self.assertIn('SSL', str(caught.exception))
        self.assertNotIn('SECRET', str(caught.exception))

    def test_empty_token_does_not_make_request(self):
        with patch('bot.urllib.request.urlopen') as request:
            with self.assertRaises(RuntimeError):
                telegram('', 'getMe')
            request.assert_not_called()


class ScheduledNotifications(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 14, 10, 2, tzinfo=ZoneInfo('Europe/Berlin'))
        self.state = {}
        self.messages = []

    def tick(self, result):
        tick_result(self.state, result, self.now, self.messages.append)

    def test_unchanged_status_is_quiet_between_reports(self):
        self.tick({'status': 'coming_soon'})
        self.tick({'status': 'coming_soon'})
        self.assertEqual(len(self.messages), 1)

    def test_sale_alert_is_immediate_once(self):
        self.tick({'status': 'coming_soon'})
        self.tick({'status': 'sale_link', 'url': 'https://example.com'})
        self.tick({'status': 'sale_link', 'url': 'https://example.com'})
        self.assertEqual(len(self.messages), 2)
        self.assertIn('ссылка покупки', self.messages[-1])

    def test_daily_report_slot(self):
        self.tick({'status': 'coming_soon'})
        self.now = self.now.replace(hour=15)
        self.tick({'status': 'coming_soon'})
        self.tick({'status': 'coming_soon'})
        self.assertEqual(len(self.messages), 2)

    def test_failed_delivery_does_not_acknowledge_new_status(self):
        self.tick({'status': 'coming_soon'})
        def failed(_):
            raise RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            tick_result(self.state, {'status': 'sale_link', 'url': 'https://example.com'},
                        self.now, failed)
        self.assertEqual(self.state['status'], 'coming_soon')
        self.tick({'status': 'sale_link', 'url': 'https://example.com'})
        self.assertEqual(self.state['status'], 'sale_link')


if __name__ == '__main__':
    unittest.main()
