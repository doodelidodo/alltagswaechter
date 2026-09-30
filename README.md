<h1 align="center">Alltagswächter</h1>

<p align="center">
  <em>Only tells you what matters today: your bus is late, rain from 5 pm, cardboard goes out tomorrow.</em><br>
  Swiss public transport · MeteoSwiss forecast · waste collection · push via ntfy · one container
</p>

---

Most "smart" dashboards show you everything, all the time. Alltagswächter
("everyday watchman") stays **silent unless something affects your day** and
then sends a single push to your phone:

| | When | Example |
| --- | --- | --- |
| 🚌 **Your connection** | from 45–60 min before *your* departure, only on your days | *B 2 07:09 delayed: +6 min* · *platform change → 7* · *07:12 no longer in the timetable* |
| ☔ **Weather** | once in the morning, only if relevant | *Rain from 16 in Zürich – 16:00–19:00, up to 1.2 mm/h* · *risk of ice: −1 °C* |
| 🗑️ **Waste collection** | the evening before | *Tomorrow: paper and cardboard* |

Silence means: nothing to worry about.

## Quick start

You need Docker and an [ntfy](https://ntfy.sh) topic: ntfy.sh works out of the
box, your own server is better.

```bash
mkdir alltagswaechter && cd alltagswaechter
curl -O https://raw.githubusercontent.com/doodelidodo/alltagswaechter/main/docker-compose.yml
mkdir config
curl -o config/config.toml https://raw.githubusercontent.com/doodelidodo/alltagswaechter/main/config.example.toml
# edit config/config.toml: ntfy topic, your stops and departures, location, waste
docker compose run --rm alltagswaechter check-config
docker compose run --rm alltagswaechter test-notify
docker compose up -d
```

Subscribe to the topic in the ntfy app, and you're done.

### Useful commands

```bash
docker compose run --rm alltagswaechter stations "Zürich Enge"   # exact stop names
docker compose run --rm alltagswaechter now all --dry-run         # what would it say right now?
docker compose run --rm alltagswaechter now transit               # check your connection immediately
docker compose logs -f alltagswaechter
```

## Configuration

Everything lives in one TOML file. See [`config.example.toml`](config.example.toml).
Every section except `[ntfy]` is optional; leave one out and that check is off.
Changes apply within 30 seconds without a restart; a broken edit is logged and
the previous configuration keeps running. (Mount the **directory**, as the
compose file does, not the single file – editors and `git pull` replace files,
and a single-file mount would keep seeing the old one.)
Secrets can come from the environment (`NTFY_TOKEN`, `NTFY_USERNAME`,
`NTFY_PASSWORD`, `NTFY_URL`, `NTFY_TOPIC`), so the file itself contains nothing
sensitive.

### Public transport

```toml
[[transit]]
name = "Commute"
from = "Zürich Enge"
to = "Winterthur"
departures = ["07:12", "07:42"]
days = "weekdays"
```

You list the departures **you actually take**. The watcher polls only around
those times (every 3 minutes by default) and reports *changes*:

- a delay appears (from `min_delay` minutes, default 3),
- the delay grows or shrinks by 3+ minutes,
- the delay is recovered,
- the platform changes,
- the connection disappears from the timetable (checked twice before it says so).

If the next connection gets you there no later than your delayed one, the push
says so: *Next: 07:16 → arr. 07:28*.

Data: [transport.opendata.ch](https://transport.opendata.ch). Real-time data
depends on the operator; SBB trains and most city buses have it.

### Weather

Forecast from [Open-Meteo](https://open-meteo.com) using **MeteoSwiss' ICON-CH2**
model (falls back to Open-Meteo's automatic choice if unavailable). One check in
the morning at `at`; it pushes only for rain within your `window`, gusts above
`gust_kmh`, or frost in the `frost_window`. Set `always = true` for a daily
summary.

### Waste collection

Three sources, combinable:

| Source | For | Config |
| --- | --- | --- |
| [OpenERZ](https://openerz.metaodi.ch) | City of Zurich | `openerz_zip = 8003` |
| iCal/ICS feed | many municipalities and waste apps | `ical_url = "https://…"` |
| Typed-in dates | municipalities with only a PDF | `[[waste.schedule]]` |

```toml
[[waste.schedule]]
name = "Papier/Karton"
dates = [2026-10-15, 2026-12-10]
note = "bundled"

[[waste.schedule]]
name = "Grüngut"
weekdays = ["mon"]
skip = [2026-12-28]
```

`types` filters OpenERZ and iCal entries (`paper`, `cardboard`, `organic`, … or
any word from the iCal title). Typed-in schedules are always used.

## Design

- **Standard library only.** No dependencies beyond `python:3.12-slim`.
- **Report changes, not states.** Every notification is remembered in a small
  state file (`/data/state.json`, pruned after three days), so a restart never
  repeats a push, and a failed push is retried on the next round.
- **Robust to restarts.** A daily check missed by a restart still runs within
  three hours; later it is skipped instead of arriving at the wrong time.
- **Self-test in the build.** `python -m app selftest` runs every check against
  recorded-style API answers; the image isn't built if it fails.

## Not affiliated

Not affiliated with SBB, MeteoSwiss, Open-Meteo or any municipality. All data
sources are public and require no key. Please be considerate: the defaults poll
the transport API only around your own departures.

## License

MIT
