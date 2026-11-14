"""IEM Cologne Wave 2 / 1-day Lower monitor. Python 3.9+."""
import argparse
import datetime as dt
import getpass
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
        raise RuntimeError(messages.get(error.code, 'Telegram вернул HTTP ' + str(error.code) + '. Повторите позже.')) from None
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
        return ('🎟 На странице ESL появилась ссылка покупки!\n' + LABEL
                + '\nПроверьте наличие на нужный день в магазине:\n' + result['url'])
    if status == 'coming_soon':
        return '⏳ ' + LABEL + '\nПродажа пока не открыта: Coming soon.\n' + URL
    return '⏳ ' + LABEL + '\nНа странице указано Sold out.\n' + URL


def tick_result(state, result, now, send):
    """One successful scheduled observation; update state after delivery."""
    hours = [9, 15, 21]
    due = [h for h in hours if h <= now.hour]
    slot = now.strftime('%Y-%m-%d') + ('T%02d' % due[-1]) if due else None
    changed = state.get('status') != result['status']
    recovered = state.get('failures', 0) >= 3
    if changed or recovered or (slot and state.get('last_slot', '') < slot):
        prefix = '✅ Проверки восстановились.\n' if recovered else ''
        send(prefix + message_for(result) + '\nПроверено: ' + now.strftime('%d.%m %H:%M %Z'))
        if slot:
            state['last_slot'] = slot
    state.update(status=result['status'], failures=0, last_success=now.isoformat())


def answer_requests(state, updates, chat_id, result, now, reply, persist):
    """Reply to each owner message using this cycle's fresh observation."""
    answered = 0
    for update in sorted(updates, key=lambda item: item['update_id']):
        if update['update_id'] < state.get('message_offset', 0):
            continue
        message = update.get('message', {})
        if (message.get('chat', {}).get('type') == 'private'
                and str(message['chat']['id']) == str(chat_id)):
            reply(message['message_id'],
                  '🔎 Проверка по вашему сообщению:\n' + message_for(result)
                  + '\nПроверено: ' + now.strftime('%d.%m %H:%M %Z'))
            answered += 1
        # Save after successful delivery, so failed replies are retried.
        state['message_offset'] = update['update_id'] + 1
        persist()
    return answered


def scheduled():
    from playwright.sync_api import sync_playwright
    token = os.environ['TELEGRAM_BOT_TOKEN']
    chat_id = os.environ['TELEGRAM_CHAT_ID']
    state_path = DATA / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    now = dt.datetime.now(ZoneInfo('Europe/Berlin'))

    def send(text):
        telegram(token, 'sendMessage', chat_id=chat_id, text=text)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                result = check(browser.new_page())
            finally:
                browser.close()
        tick_result(state, result, now, send)
        save(state_path, state)
        updates = telegram(token, 'getUpdates', offset=state.get('message_offset', 0),
                           limit=100, timeout=0, allowed_updates=['message'])

        def reply(message_id, text):
            telegram(token, 'sendMessage', chat_id=chat_id, text=text,
                     reply_parameters={'message_id': message_id,
                                       'allow_sending_without_reply': True})

        answered = answer_requests(state, updates, chat_id, result, now, reply,
                                   lambda: save(state_path, state))
        print('Message requests answered:', answered, flush=True)
        print(now.isoformat(), result['status'], flush=True)
    except Exception as error:
        state['failures'] = state.get('failures', 0) + 1
        # Alert on the first failure as well, so failed initial setup is visible.
        if state['failures'] in (1, 3) or state['failures'] % 12 == 0:
            try:
                send('⚠️ Проверка билетов или доставка не удалась. '
                     'Наличие неизвестно. Проверьте вкладку Actions на GitHub.\n' + URL)
            except RuntimeError:
                print('Telegram delivery failed', flush=True)
        save(state_path, state)
        raise RuntimeError('Scheduled check failed: ' + type(error).__name__) from None


def run(once=False):
    from playwright.sync_api import sync_playwright
    config = {} if once else json.loads((DATA / 'config.json').read_text())
    state_path = DATA / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    interval = max(60, int(os.environ.get('CHECK_SECONDS', '60')))
    hours = sorted(set(int(h) for h in os.environ.get('STATUS_HOURS', '9,15,21').split(',')))
    if not hours or any(h < 0 or h > 23 for h in hours):
        raise ValueError('STATUS_HOURS must contain hours from 0 to 23')
    zone = ZoneInfo(os.environ.get('TIMEZONE', 'Europe/Berlin'))
    failures = 0

    def send(text):
        telegram(config['token'], 'sendMessage', chat_id=config['chat_id'], text=text)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        while True:
            started = time.monotonic()
            now = dt.datetime.now(zone)
            timestamp = now.isoformat(timespec='seconds')
            try:
                result = check(page)
                print(timestamp, result['status'], flush=True)
                if once:
                    print(json.dumps(result, ensure_ascii=False))
                    return
                # Alert only once per transition, and persist only AFTER successful delivery.
                changed = state.get('status') != result['status']
                if changed:
                    send(message_for(result))
                    state['status'] = result['status']
                    state['last_slot'] = timestamp[:13]
                    save(state_path, state)
                if failures >= 3:
                    send('✅ Проверки восстановились.\n' + message_for(result))
                failures = 0
                due = [h for h in hours if h <= now.hour]
                slot = now.strftime('%Y-%m-%d') + ('T%02d' % due[-1]) if due else None
                if slot and state.get('last_slot', '') < slot:
                    send(message_for(result) + '\nПроверено: ' + now.strftime('%d.%m %H:%M %Z'))
                    state['last_slot'] = slot
                state['last_success'] = timestamp
                save(state_path, state)
            except Exception as error:
                if once:
                    raise RuntimeError('Проверка не удалась: ' + type(error).__name__) from None
                failures += 1
                print(timestamp, 'check/delivery failed:', type(error).__name__, flush=True)
                if failures == 3 or failures % 60 == 0:
                    try:
                        send('⚠️ Не удаётся проверить ESL или доставить сообщение. '
                             'Текущее наличие неизвестно. Нужна проверка сервиса.\n' + URL)
                    except RuntimeError:
                        print('Telegram unavailable too', flush=True)
                if page.is_closed():
                    raise RuntimeError('Browser closed; restart required') from None
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
