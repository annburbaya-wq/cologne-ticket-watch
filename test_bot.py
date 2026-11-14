import datetime as dt
import unittest
import urllib.error
import ssl
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from bot import tick_result, telegram, answer_requests, configured_chats, notify_chats, cycle, recipient_key, message_for, TelegramError, save


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
        self.assertEqual(self.saved_offsets[:2], [2, 3])

    def test_other_chat_cannot_request_checks(self):
        self.assertEqual(self.process([self.update(1, chat=999, text='check')]), 0)
        self.assertEqual(self.replies, [])
        self.assertEqual(self.state['message_offset'], 2)

    def test_replayed_updates_do_not_duplicate_replies(self):
        update = self.update(1, text='билеты?')
        self.process([update])
        self.process([update])
        self.assertEqual(len(self.replies), 1)

    def test_failed_reply_is_persisted_and_retried_without_new_update(self):
        def failed(*_):
            raise RuntimeError('offline')
        self.assertEqual(self.process([self.update(1)], failed), 0)
        self.assertEqual(len(self.state['pending_requests']), 1)
        self.process([])
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(self.state['pending_requests'], [])

    def test_failed_first_chat_does_not_block_second_or_repeat_success(self):
        delivered = []
        def send(chat, mid, text):
            if chat == '123':
                raise RuntimeError('offline')
            delivered.append(chat)
        updates = [self.update(1), self.update(2, chat=456)]
        for batch in [updates, []]:
            answer_requests(self.state, batch, '123,456', {'status': 'coming_soon'},
                            self.now, send, lambda: None)
        self.assertEqual(delivered, ['456'])
        self.assertEqual(len(self.state['pending_requests']), 1)
        self.assertNotIn('chat_id', self.state['pending_requests'][0])
        self.assertNotIn('text', self.state['pending_requests'][0])

    def test_blocked_chat_does_not_remain_queued(self):
        def blocked(*_):
            raise TelegramError('blocked', 403)
        self.process([self.update(1)], blocked)
        self.assertEqual(self.state['pending_requests'], [])

    def test_group_using_same_id_cannot_request(self):
        update = self.update(1)
        update['message']['chat']['type'] = 'group'
        self.assertEqual(self.process([update]), 0)

    def test_unauthorized_updates_never_enter_pending_queue(self):
        self.process([self.update(1, chat=999, text='secret text')])
        self.assertEqual(self.state['pending_requests'], [])



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


class AuditCycles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.now = dt.datetime(2026, 11, 14, 14, 7, 30, tzinfo=ZoneInfo('Europe/Berlin'))
        self.sent = []
        self.updates = []
        self.blocked = None
        self.poll_failed = False

    def telegram(self, token, method, **payload):
        if method == 'getUpdates':
            self.last_poll_offset = payload['offset']
            if self.poll_failed:
                raise RuntimeError('network')
            return self.updates
        if payload['chat_id'] == self.blocked:
            raise TelegramError('blocked', 403)
        self.sent.append(payload)
        return {'message_id': 50}

    def run_cycle(self, observe=None):
        with patch('bot.telegram', side_effect=self.telegram):
            cycle('123:secret', ['123', '456'], self.path,
                  observe or (lambda: {'status': 'coming_soon'}), lambda: self.now)

    def state(self):
        return json.loads(self.path.read_text())

    def test_both_recipients_notified_and_unchanged_cycle_quiet(self):
        self.run_cycle()
        self.run_cycle()
        self.assertEqual([m['chat_id'] for m in self.sent], ['123', '456'])

    def test_site_down_answers_with_dated_previous_observation(self):
        self.run_cycle()
        self.sent.clear()
        self.updates = [{'update_id':1,'message':{'message_id':12,'chat':{'id':456,'type':'private'}}}]
        def down():
            raise RuntimeError('ESL unavailable')
        with self.assertRaises(RuntimeError):
            self.run_cycle(down)
        reply = [m for m in self.sent if 'reply_parameters' in m][0]
        self.assertEqual(reply['chat_id'], '456')
        self.assertIn('Последний успешный результат', reply['text'])
        self.assertIn(self.now.isoformat(), reply['text'])
        self.assertEqual(self.state()['last_result']['status'], 'coming_soon')

    def test_first_site_failure_does_not_invent_availability(self):
        self.updates = [{'update_id':1,'message':{'message_id':12,'chat':{'id':123,'type':'private'}}}]
        def down():raise RuntimeError('down')
        with self.assertRaises(RuntimeError):self.run_cycle(down)
        reply = [m for m in self.sent if 'reply_parameters' in m][0]
        self.assertIn('неизвестно', reply['text'])
        self.assertNotIn('Coming soon', reply['text'])
        self.assertNotIn('Sold out', reply['text'])

    def test_failures_reset_and_restoration_notices_only_once(self):
        self.run_cycle()
        def down():raise RuntimeError('down')
        for _ in range(3):
            with self.assertRaises(RuntimeError):self.run_cycle(down)
        self.sent.clear()
        self.run_cycle()
        self.run_cycle()
        self.assertEqual(self.state()['failures'], 0)
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all('восстановились' in m['text'] for m in self.sent))

    def test_one_blocked_recipient_does_not_spam_other_or_claim_site_failure(self):
        self.blocked = '123'
        for _ in range(2):
            with self.assertRaises(RuntimeError):self.run_cycle()
        self.assertEqual([m['chat_id'] for m in self.sent], ['456'])
        self.assertNotIn('сайта не удалась', self.sent[0]['text'])

    def test_poll_failure_still_retries_saved_queue(self):
        key = recipient_key('123:secret', '456')
        save(self.path, {'pending_requests':[{'recipient':key,'message_id':10,'update_id':1}]})
        self.poll_failed = True
        with self.assertRaises(RuntimeError):self.run_cycle()
        self.assertEqual(len([m for m in self.sent if 'reply_parameters' in m]), 1)
        self.assertEqual(self.state()['pending_requests'], [])

    def test_unknown_status_does_not_overwrite_previous_observation(self):
        self.run_cycle()
        with self.assertRaises(RuntimeError):self.run_cycle(lambda: {'status':'unknown'})
        self.assertEqual(self.state()['last_result']['status'], 'coming_soon')

    def test_corrupt_cache_recovers_and_saves_atomic_file(self):
        self.path.write_text('{invalid')
        self.run_cycle()
        self.assertEqual(self.state()['last_result']['status'], 'coming_soon')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.path.with_suffix('.tmp').exists())

    def test_sale_url_change_generates_new_alert_and_next_day_has_report(self):
        self.run_cycle(lambda: {'status':'sale_link','url':'https://example.com/a'})
        self.sent.clear()
        self.run_cycle(lambda: {'status':'sale_link','url':'https://example.com/b'})
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all('🚨🚨🚨' in m['text'] and '!!!' in m['text'] for m in self.sent))
        self.sent.clear()
        self.now += dt.timedelta(days=1)
        self.run_cycle(lambda: {'status':'sale_link','url':'https://example.com/b'})
        self.assertEqual(len(self.sent), 2)

    def test_failed_reply_survives_process_restart(self):
        self.run_cycle()
        key = recipient_key('123:secret', '456')
        state = self.state()
        state['pending_requests'] = [{'recipient':key,'message_id':12,'update_id':1}]
        save(self.path, state)
        self.run_cycle()
        self.assertEqual(len([m for m in self.sent if 'reply_parameters' in m]), 1)
        self.assertEqual(self.state()['pending_requests'], [])

    def test_lower_update_id_after_week_inactivity_is_not_lost(self):
        save(self.path, {'message_offset':9000,
                        'updates_received_at':(self.now-dt.timedelta(days=8)).isoformat()})
        self.updates = [{'update_id':100,'message':{'message_id':12,'chat':{'id':456,'type':'private'}}}]
        self.run_cycle()
        self.assertEqual(self.last_poll_offset, 0)
        self.assertEqual(len([m for m in self.sent if 'reply_parameters' in m]), 1)
        self.assertEqual(self.state()['message_offset'], 101)

    def test_custom_status_hours_are_respected(self):
        self.run_cycle()
        self.sent.clear()
        self.now = self.now.replace(hour=16)
        with patch.dict('os.environ', {'STATUS_HOURS':'9,16,21'}):
            self.run_cycle()
        self.assertEqual(len(self.sent), 2)

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):message_for({'status':'unknown'})


if __name__ == '__main__':
    unittest.main()
