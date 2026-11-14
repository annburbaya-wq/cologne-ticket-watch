"""IEM Cologne Wave 2 / 1-day Lower monitor. Python 3.9+."""
import argparse
import datetime as dt
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', BASE / 'data'))
URL = 'https://pro.eslgaming.com/tour/cs/cologne/'
LABEL = 'IEM Cologne 2027 · Wave 2 · 1-Day Lower'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)


class TelegramError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def telegram(token, method, **payload):
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise RuntimeError('Неверный формат токена. Скопируйте только токен из BotFather, без кавычек и пояснений.')
    request = urllib.request.Request(
        'https://api.telegram.org/bot' + token + '/' + method,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.load(response)
        if not data.get('ok'):
            raise RuntimeError('Telegram отклонил запрос. Повторите подключение.')
        return data['result']
    except urllib.error.HTTPError as error:
        messages = {
            401: 'Telegram не принял токен (401). Скопируйте актуальный токен из BotFather целиком.',
            404: 'Telegram не нашёл бота (404). Проверьте, что скопирован весь токен из BotFather.',
            403: 'Telegram запретил запрос (403). Откройте своего бота, нажмите Start и проверьте, что он не заблокирован.',
            409: 'Другой процесс уже читает сообщения этого бота (409). Остановите его и повторите подключение.',
            429: 'Слишком много запросов к Telegram (429). Подождите минуту и повторите.',
        }
        raise TelegramError(messages.get(error.code, 'Telegram вернул HTTP ' + str(error.code) + '. Повторите позже.'), error.code) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            message = 'Python не смог проверить сертификат Telegram (SSL). Нужна настройка сертификатов Python.'
        elif isinstance(error.reason, socket.gaierror):
            message = 'Не удалось найти api.telegram.org (DNS). Проверьте интернет или VPN.'
        elif isinstance(error.reason, (TimeoutError, socket.timeout)):
            message = 'Telegram не ответил вовремя. Проверьте интернет или VPN и повторите.'
        else:
            message = 'Не удалось соединиться с Telegram. Проверьте интернет или VPN и повторите.'
        raise RuntimeError(message) from None
    except (TimeoutError, socket.timeout):
        raise RuntimeError('Telegram не ответил вовремя. Проверьте интернет или VPN и повторите.') from None
    except RuntimeError:
        raise
    except Exception:
        # Do not leak token-bearing URLs through exception messages.
        raise RuntimeError('Telegram request failed: ' + method) from None


def setup():
    token = getpass.getpass('Токен от @BotFather (ввод скрыт): ').strip()
    me = telegram(token, 'getMe')
    code = '/link_' + secrets.token_hex(6)
    print('Откройте https://t.me/' + me['username'])
    print('Нажмите Start и отправьте боту: ' + code, flush=True)
    offset = 0
    deadline = time.time() + 600
    while time.time() < deadline:
        for update in telegram(token, 'getUpdates', offset=offset, timeout=20,
                               allowed_updates=['message']):
            offset = update['update_id'] + 1
            message = update.get('message', {})
            if message.get('text') == code and message.get('chat', {}).get('type') == 'private':
                chat_id = message['chat']['id']
                save(DATA / 'config.json', {'token': token, 'chat_id': chat_id})
                telegram(token, 'sendMessage', chat_id=chat_id,
                         text='Telegram подключён. Мониторинг начнётся после запуска сервиса.')
                print('Telegram подключён. Теперь запустите сервис.')
                return
    raise RuntimeError('Время подключения истекло. Повторите setup.')


def check(page):
    response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
    if not response or response.status >= 400:
        raise RuntimeError('ESL HTTP error')
    page.locator('[role="tab"]').filter(has_text='Wave 2').wait_for(state='attached', timeout=20000)
    return page.evaluate((BASE / 'detect.js').read_text())


def message_for(result):
    status = result['status']
    if status == 'sale_link':
        return ('🚨🚨🚨 WAVE 2: ПОЯВИЛАСЬ ССЫЛКА ПОКУПКИ!!!\n' + LABEL
                + '\nНа странице ESL появилась ссылка покупки!!!'
                + '\nСкорее проверьте наличие на нужный день в магазине:\n' + result['url'])
    if status == 'coming_soon':
        return '⏳ ' + LABEL + '\nПродажа пока не открыта: Coming soon.\n' + URL
    if status == 'sold_out':
        return '⏳ ' + LABEL + '\nНа странице указано Sold out.\n' + URL
    raise ValueError('Unknown availability status')


def tick_result(state, result, now, send):
    """One successful scheduled observation; update state after delivery."""
    hours = [9, 15, 21]
    due = [h for h in hours if h <= now.hour]
    slot = now.strftime('%Y-%m-%d') + ('T%02d' % due[-1]) if due else None
    changed = (state.get('status') != result['status']
               or (result['status'] == 'sale_link' and state.get('url') != result.get('url')))
    recovered = state.get('failures', 0) >= 3
    if changed or recovered or (slot and state.get('last_slot', '') < slot):
        prefix = '✅ Проверки восстановились.\n' if recovered else ''
        send(prefix + message_for(result) + '\nПроверено: ' + now.strftime('%d.%m %H:%M %Z'))
        if slot:
            state['last_slot'] = slot
    state.update(status=result['status'], url=result.get('url'), failures=0, last_success=now.isoformat())


def configured_chats(value):
    """The private secret can contain one or several numeric chat IDs."""
    chats = list(dict.fromkeys(re.split(r'[,\s]+', str(value).strip())))
    if not chats or any(not re.fullmatch(r'[0-9]+', chat) for chat in chats):
        raise ValueError('TELEGRAM_CHAT_ID must contain private chat IDs')
    return chats


def recipient_key(token, chat):
    return hmac.new(token.encode(), str(chat).encode(), hashlib.sha256).hexdigest()


def notify_chats(state, chat_ids, token, result, now, send, persist):
    """Keep delivery state per recipient without storing their IDs in the cache."""
    deliveries = state.setdefault('deliveries', {})
    failures = 0
    for index, chat in enumerate(chat_ids):
        key = recipient_key(token, chat)
        if key not in deliveries:
            # Preserve the original owner's delivery history during migration.
            deliveries[key] = ({k: state[k] for k in
                               ('status', 'url', 'last_slot', 'failures', 'last_success') if k in state}
                              if index == 0 else {})
        try:
            tick_result(deliveries[key], result, now, lambda text: send(chat, text))
        except RuntimeError:
            failures += 1
            deliveries[key]['failures'] = deliveries[key].get('failures', 0) + 1
        persist()
    return failures


def answer_requests(state, updates, chat_id, result, now, reply, persist,
                    token='', checked_at=None, check_failed=False):
    """Persist requests before acknowledging, then isolate each chat's delivery."""
    chats = configured_chats(chat_id)
    recipients = {recipient_key(token, chat): chat for chat in chats}
    pending = state.setdefault('pending_requests', [])
    for update in sorted(updates, key=lambda item: item['update_id']):
        if update['update_id'] < state.get('message_offset', 0):
            continue
        message = update.get('message', {})
        key = recipient_key(token, message.get('chat', {}).get('id', ''))
        if message.get('chat', {}).get('type') == 'private' and key in recipients:
            pending.append({'update_id': update['update_id'],
                            'message_id': message['message_id'], 'recipient': key})
        # A failed reply remains in the durable queue; later messages can proceed.
        state['message_offset'] = update['update_id'] + 1
        persist()
    if result is None:
        text = '⚠️ Проверить сайт не удалось. Текущее наличие неизвестно.\n' + URL
    elif check_failed:
        text = ('⚠️ Свежая проверка не удалась. Последний успешный результат:\n'
                + message_for(result) + '\nПроверено: ' + checked_at)
    else:
        text = ('🔎 Проверка по вашему сообщению:\n' + message_for(result)
                + '\nПроверено: ' + now.strftime('%d.%m %H:%M:%S %Z'))
    answered = 0
    for request in list(pending):
        chat = recipients.get(request['recipient'])
        if chat is None:
            pending.remove(request)
            persist()
            continue
        try:
            reply(chat, request['message_id'], text)
        except RuntimeError as error:
            if getattr(error, 'code', None) != 403:
                continue
            # A blocked bot cannot deliver; don't keep this request indefinitely.
        else:
            answered += 1
        pending.remove(request)
        persist()
    return answered


def load_state(path):
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text())
        if (not isinstance(state, dict)
                or not isinstance(state.get('deliveries', {}), dict)
                or not isinstance(state.get('pending_requests', []), list)
                or any(not isinstance(r, dict) or not {'recipient', 'message_id', 'update_id'} <= r.keys()
                       for r in state.get('pending_requests', []))):
            raise ValueError('Invalid cached state schema')
        return state
    except (ValueError, OSError):
        # Loss of cached history can repeat a status, but must not stop monitoring.
        print('Cached state unreadable; starting with fresh state', flush=True)
        return {}


def cycle(token, chat_ids, state_path, observe, clock=None):
    state = load_state(state_path)
    clock = clock or (lambda: dt.datetime.now(ZoneInfo('Europe/Berlin')))
    persist = lambda: save(state_path, state)
    def send(chat, text):
        telegram(token, 'sendMessage', chat_id=chat, text=text,
                 link_preview_options={'is_disabled': True})
    check_failed = False
    delivery_failures = 0
    try:
        result = observe()
        message_for(result)  # Reject unknown states before changing saved availability.
    except Exception as error:
        print('Site check failed:', type(error).__name__, flush=True)
        check_failed = True
        state['failures'] = state.get('failures', 0) + 1
        result = state.get('last_result')
        if state['failures'] in (1, 3) or state['failures'] % 12 == 0:
            for chat in chat_ids:
                try:
                    send(chat, '⚠️ Проверка сайта не удалась. Текущее наличие неизвестно.\n' + URL)
                except RuntimeError:
                    delivery_failures += 1
    now = clock()  # Timestamp the completed observation, not the browser startup.
    if not check_failed:
        if state.get('failures', 0) >= 3:
            for delivery in state.get('deliveries', {}).values():
                delivery['failures'] = max(3, delivery.get('failures', 0))
        state.update(failures=0, last_result={k: result[k] for k in ('status', 'url') if k in result},
                     last_checked=now.isoformat())
        delivery_failures = notify_chats(state, chat_ids, token, result, now, send, persist)
    persist()
    try:
        updates = telegram(token, 'getUpdates', offset=state.get('message_offset', 0),
                           limit=100, timeout=0, allowed_updates=['message'])
    except RuntimeError:
        updates = []
        delivery_failures += 1
        print('Telegram polling failed', flush=True)
    def reply(chat, message_id, text):
        telegram(token, 'sendMessage', chat_id=chat, text=text,
                 link_preview_options={'is_disabled': True},
                 reply_parameters={'message_id': message_id, 'allow_sending_without_reply': True})
    answered = answer_requests(state, updates, ','.join(chat_ids), result, now, reply, persist,
                               token=token, checked_at=state.get('last_checked'),
                               check_failed=check_failed)
    print('Message requests answered:', answered, '; pending:', len(state['pending_requests']), flush=True)
    persist()
    if check_failed or delivery_failures or state['pending_requests']:
        raise RuntimeError('Check or delivery incomplete; see safe diagnostic above')
    print(now.isoformat(), result['status'], '; recipients:', len(chat_ids), flush=True)


def browser_check():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            return check(browser.new_page())
        finally:
            browser.close()


def scheduled():
    cycle(os.environ['TELEGRAM_BOT_TOKEN'], configured_chats(os.environ['TELEGRAM_CHAT_ID']),
          DATA / 'state.json', browser_check)


def run(once=False):
    if once:
        print(json.dumps(browser_check(), ensure_ascii=False))
        return
    config = json.loads((DATA / 'config.json').read_text())
    chats = configured_chats(','.join([str(config['chat_id'])] + config.get('extra_chat_ids', [])))
    interval = max(60, int(os.environ.get('CHECK_SECONDS', '60')))
    while True:
        started = time.monotonic()
        try:
            cycle(config['token'], chats, DATA / 'state.json', browser_check)
        except RuntimeError as error:
            print(type(error).__name__ + ': cycle incomplete', flush=True)
        time.sleep(max(1, interval - (time.monotonic() - started)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['setup', 'check', 'run', 'scheduled'], nargs='?', default='run')
    args = parser.parse_args()
    if args.command == 'setup':
        try:
            setup()
        except RuntimeError as error:
            print('\nПодключение не завершено: ' + str(error), file=sys.stderr)
            sys.exit(1)
    elif args.command == 'scheduled':
        scheduled()
    else:
        run(once=args.command == 'check')
