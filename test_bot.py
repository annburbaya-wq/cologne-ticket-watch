import datetime as dt
import unittest
import urllib.error
import ssl
from unittest.mock import patch
from zoneinfo import ZoneInfo
from bot import tick_result, telegram, answer_requests, configured_chats, notify_chats


class MessageRequests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.now = dt.datetime(2026, 11, 14, 14, 7, tzinfo=ZoneInfo('Europe/Berlin'))
        self.replies = []
        self.saved_offsets = []

    def process(self, updates, reply=None):
        return answer_requests(
            self.state, updates, 123, {'status': 'coming_soon'}, self.now,
            reply or (lambda chat_id, message_id, text: self.replies.append((message_id, text))),
            lambda: self.saved_offsets.append(self.state['message_offset']))

    def update(self, number, chat=123, **content):
        return {'update_id': number, 'message': {
            'message_id': number + 10, 'chat': {'id': chat, 'type': 'private'}, **content}}

    def test_arbitrary_text_and_photo_get_fresh_status(self):
        self.assertEqual(self.process([self.update(1, text='привет'), self.update(2, photo=[])]), 2)
        self.assertEqual([r[0] for r in self.replies], [11, 12])
        self.assertIn('Coming soon', self.replies[0][1])
        self.assertIn('14:07', self.replies[0][1])
        self.assertEqual(self.saved_offsets, [2, 3])

    def test_other_chat_cannot_request_checks(self):
        self.assertEqual(self.process([self.update(1, chat=999, text='check')]), 0)
        self.assertEqual(self.replies, [])
        self.assertEqual(self.state['message_offset'], 2)

    def test_replayed_updates_do_not_duplicate_replies(self):
        update = self.update(1, text='билеты?')
        self.process([update])
        self.process([update])
        self.assertEqual(len(self.replies), 1)

    def test_failed_reply_does_not_acknowledge_request(self):
        def failed(*_):
            raise RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            self.process([self.update(1)], failed)
        self.assertNotIn('message_offset', self.state)
        self.process([self.update(1)])
        self.assertEqual(len(self.replies), 1)


class MultipleChats(unittest.TestCase):
    def test_allowlisted_chats_receive_replies_in_their_own_chat(self):
        updates = [dict(update_id=i, message=dict(message_id=i+10,
                   chat=dict(id=chat, type='private'), text='tickets?'))
                   for i, chat in enumerate([123, 456, 999], 1)]
        replies = []
        state = {}
        count = answer_requests(state, updates, '123,456', {'status': 'coming_soon'},
                                dt.datetime.now(ZoneInfo('Europe/Berlin')),
                                lambda chat, mid, text: replies.append((chat, mid)), lambda: None)
        self.assertEqual(count, 2)
        self.assertEqual(replies, [('123', 11), ('456', 12)])

    def test_recipient_failure_does_not_repeat_successful_alert(self):
        state, sent = {}, []
        now = dt.datetime(2026, 11, 14, 14, 0, tzinfo=ZoneInfo('Europe/Berlin'))
        def send(chat, text):
            if chat == '456':
                raise RuntimeError('blocked')
            sent.append(chat)
        for _ in range(2):
            self.assertEqual(notify_chats(state, ['123', '456'], 'secret',
                             {'status': 'coming_soon'}, now, send, lambda: None), 1)
        self.assertEqual(sent, ['123'])
        self.assertNotIn('123', state['deliveries'])
        self.assertNotIn('456', state['deliveries'])

    def test_chat_secret_supports_spaces_and_deduplicates(self):
        self.assertEqual(configured_chats('123, 456\n123'), ['123', '456'])


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
