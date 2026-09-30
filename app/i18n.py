"""Notification texts. Keys are English, wording lives here only."""
from __future__ import annotations

TEXTS = {
    "de": {
        "delay_title": "{line} {dep} verspätet: +{delay} Min",
        "delay_body": "{route}: ab {origin} {dep} → voraussichtlich {actual}.",
        "delay_update_title": "{line} {dep}: jetzt +{delay} Min",
        "on_time_title": "{line} {dep} wieder pünktlich",
        "on_time_body": "{route}: Verspätung aufgeholt.",
        "platform_title": "{line} {dep}: Gleisänderung → Gleis {platform}",
        "platform_body": "{route}: fährt ab Gleis {platform} statt {old}.",
        "platform_body_new": "{route}: fährt ab Gleis/Kante {platform} (geändert).",
        "missing_title": "{dep} nicht mehr im Fahrplan",
        "missing_body": "{route}: die Verbindung um {dep} ist verschwunden – möglicherweise ausgefallen. Bitte in der SBB-App prüfen.",
        "alternative": "Nächste: {dep}{delay} → an {arr}",
        "rain_title": "Regen ab {start} Uhr{where}",
        "rain_body": "Regen {start}–{end} Uhr, bis {mm} mm/h.",
        "gust_body": "Böen bis {kmh} km/h.",
        "frost_body": "Glättegefahr: morgens bis {temp} °C.",
        "weather_title_other": "Wetter heute{where}",
        "weather_ok": "Nichts Besonderes: {tmin}–{tmax} °C, trocken.",
        "weather_range": "Temperatur {tmin}–{tmax} °C.",
        "waste_title": "Morgen: {types}",
        "waste_body": "Abfuhr am {date} – heute Abend bereitstellen.",
        "waste_body_station": "{type}: {station}",
        "test_title": "Alltagswächter: Test",
        "test_body": "Die Verbindung zu ntfy funktioniert.",
        "and": "und",
    },
    "en": {
        "delay_title": "{line} {dep} delayed: +{delay} min",
        "delay_body": "{route}: from {origin} {dep} → expected {actual}.",
        "delay_update_title": "{line} {dep}: now +{delay} min",
        "on_time_title": "{line} {dep} back on time",
        "on_time_body": "{route}: delay recovered.",
        "platform_title": "{line} {dep}: platform change → {platform}",
        "platform_body": "{route}: departs from platform {platform} instead of {old}.",
        "platform_body_new": "{route}: departs from platform {platform} (changed).",
        "missing_title": "{dep} no longer in the timetable",
        "missing_body": "{route}: the {dep} connection has disappeared – possibly cancelled. Please check the SBB app.",
        "alternative": "Next: {dep}{delay} → arr. {arr}",
        "rain_title": "Rain from {start}{where}",
        "rain_body": "Rain {start}–{end}, up to {mm} mm/h.",
        "gust_body": "Gusts up to {kmh} km/h.",
        "frost_body": "Risk of ice: down to {temp} °C in the morning.",
        "weather_title_other": "Weather today{where}",
        "weather_ok": "Nothing special: {tmin}–{tmax} °C, dry.",
        "weather_range": "Temperature {tmin}–{tmax} °C.",
        "waste_title": "Tomorrow: {types}",
        "waste_body": "Collection on {date} – put it out tonight.",
        "waste_body_station": "{type}: {station}",
        "test_title": "Alltagswächter: test",
        "test_body": "The connection to ntfy works.",
        "and": "and",
    },
}

# OpenERZ waste_type keys plus common words found in municipal iCal feeds.
WASTE_TYPES = {
    "de": {
        "waste": "Kehricht", "organic": "Grüngut", "paper": "Papier", "cardboard": "Karton",
        "cargotram": "Cargo-Tram", "etram": "E-Tram", "mobile": "Recyclingmobil",
        "special": "Sonderabfall", "textile": "Textilien", "chipping": "Häckseldienst",
        "metal": "Metall", "bulky_goods": "Sperrgut", "incombustibles": "Unbrennbares",
    },
    "en": {
        "waste": "general waste", "organic": "green waste", "paper": "paper", "cardboard": "cardboard",
        "cargotram": "Cargo tram", "etram": "E-tram", "mobile": "recycling van",
        "special": "hazardous waste", "textile": "textiles", "chipping": "shredding service",
        "metal": "metal", "bulky_goods": "bulky waste", "incombustibles": "incombustibles",
    },
}


def t(lang: str, key: str, **kw) -> str:
    return TEXTS.get(lang, TEXTS["en"])[key].format(**kw)


def waste_name(lang: str, key: str) -> str:
    return WASTE_TYPES.get(lang, WASTE_TYPES["en"]).get(key, key)


def join(lang: str, items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {t(lang, 'and')} " + items[-1]
