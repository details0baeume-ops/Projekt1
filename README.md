# KI-News-Tracker

Prüft alle 15 Minuten die News-Feeds von KI-Firmen und schickt dir neue Beiträge sofort aufs Handy
(ntfy, Telegram, Discord) oder per E-Mail. Läuft kostenlos über GitHub Actions, du brauchst keinen Server.
Alle Meldungen landen außerdem in [`NEWS.md`](NEWS.md).

**Beobachtete Quellen:** OpenAI, Anthropic, Claude-Blog, Google DeepMind, Google AI, Meta AI, Microsoft AI, Mistral,
xAI, NVIDIA, Hugging Face, Perplexity, Cohere und Apple ML. Die Liste kannst du in [`sources.json`](sources.json) ändern.

## Einrichtung (ca. 5 Minuten)

### 1. Benachrichtigungskanal wählen

Am einfachsten ist **ntfy**, dafür brauchst du kein Konto:

1. Installiere die App „ntfy“ ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) oder [iOS](https://apps.apple.com/app/ntfy/id1625396347)).
2. Abonniere dort ein Thema mit einem schwer erratbaren Namen, z. B. `ki-news-7f3k9q2x`.
   Die Themen sind öffentlich, wer den Namen kennt, kann mitlesen.

### 2. Secrets im GitHub-Repo anlegen

Gehe zu *Settings → Secrets and variables → Actions → New repository secret*:

| Kanal    | Secrets                                                                  |
| -------- | ------------------------------------------------------------------------ |
| ntfy     | `NTFY_TOPIC` (optional `NTFY_SERVER`, `NTFY_TOKEN`)                      |
| Telegram | `TELEGRAM_BOT_TOKEN` (von @BotFather), `TELEGRAM_CHAT_ID`                |
| Discord  | `DISCORD_WEBHOOK_URL`                                                    |
| E-Mail   | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` (optional `SMTP_PORT`, Standard 465, `EMAIL_FROM`) |

Du kannst mehrere Kanäle gleichzeitig nutzen. E-Mail kommt als Sammelmail pro Lauf.
Für Gmail brauchst du ein App-Passwort (`smtp.gmail.com`).

### 3. Workflow aktivieren

- Der Workflow muss auf dem **Standard-Branch** (meist `main`) liegen, weil GitHub zeitgesteuerte Workflows nur dort startet.
- Unter *Actions → KI-News-Tracker → Run workflow* mit Häkchen bei „Nur Testnachricht senden“ prüfst du, ob die Benachrichtigung ankommt.
- Beim ersten echten Lauf werden alle vorhandenen Beiträge nur gespeichert, damit du nicht mit alten News überflutet wirst.
  Ab dann bekommst du nur noch neue Beiträge.

## Quellen anpassen

In `sources.json`:

```json
{"name": "OpenAI", "url": "https://openai.com/news/rss.xml"},
{"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml", "keywords": ["release", "model"]},
{"name": "Google News: DeepSeek", "url": "https://news.google.com/rss/search?q=DeepSeek&hl=de&gl=DE&ceid=DE:de", "enabled": false}
```

- `keywords` (optional): Es werden nur Beiträge gemeldet, deren Titel oder Text eines der Wörter enthält.
- `enabled: false` schaltet eine Quelle ab.
- Funktioniert mit jedem RSS- oder Atom-Feed. Hat eine Firma keinen Feed, kannst du eine Google-News-Suche nutzen. Die meldet aber auch Presseartikel *über* die Firma und ist daher deutlich lauter.
- Einstellungen: `max_age_days` (ältere Beiträge werden ignoriert), `max_notifications_per_run` (Schutz vor Spam) und `news_md_items`.

Anthropic, Meta, Mistral, xAI, Perplexity und Cohere haben keinen offiziellen RSS-Feed. Für sie nutzt der Tracker die
Community-Feeds von [Olshansk/rss-feeds](https://github.com/Olshansk/rss-feeds), die stündlich aktualisiert werden.

## Lokal ausführen

```bash
python3 ki_news_tracker.py --dry-run          # nur anzeigen, nichts senden oder speichern
python3 ki_news_tracker.py --only anthropic   # nur bestimmte Quellen
NTFY_TOPIC=mein-thema python3 ki_news_tracker.py --test
python3 -m unittest discover -s tests         # Tests
```

Du brauchst nur Python 3.9 oder neuer, zusätzliche Pakete sind nicht nötig.

## Hinweise

- GitHub-Cronjobs starten manchmal ein paar Minuten zu spät.
- In öffentlichen Repos pausiert GitHub zeitgesteuerte Workflows nach 60 Tagen ohne Aktivität. Dann aktivierst du sie unter *Actions* einfach wieder.
