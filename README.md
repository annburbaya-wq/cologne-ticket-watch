# Cologne ticket watch

Monitors ESL's Wave 2 / 1-Day Lower announcement card and sends Telegram notifications.

Checks run every five minutes via GitHub Actions; scheduling delays are possible. Status reports follow 09:00, 15:00 and 21:00 Europe/Berlin.

Send any message to the bot from the configured private chat to receive the result of the next fresh check. Each request gets a reply, usually at the next scheduled run; this is not an instant webhook service. Other chats are ignored. Message text is not saved or logged.

Secrets are stored only in GitHub Actions Secrets. No checkout inventory checks or purchases. Public-repository standard runners are free. GitHub may disable schedules after 60 days of repository inactivity. Check the Actions tab periodically.
