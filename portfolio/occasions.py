"""Festival themes: a greeting that slides down, a festive effect and an optional background.

Where the dates come from
  Once a day (the same cron that syncs GitHub) the site downloads Google Calendar's public
  holiday calendars for the countries below and stores up to YEARS_AHEAD years of dates in
  the JSON. "Refresh dates" in /admin does the same on demand. If Google cannot be reached,
  the last good copy is kept; with no copy at all, built-in dates are used (fixed-date
  festivals, Easter maths, and verified dates for India's lunar festivals).

Whose festivals a visitor sees
  The visitor's *site language* picks the country (REGIONS): English, Hindi and Marathi ->
  India; Spanish -> Spain + Mexico; French -> France; German -> Germany; Japanese -> Japan;
  Russian -> Russia. If that country has nothing on today - or its calendar could not be
  loaded - India's festival is shown instead (India is the default).

What you control in /admin -> Festivals
  * mode: automatic / always show one festival (until a date) / off
  * per festival: on/off, your own greeting, your own background image, the effect
  * your own occasions (birthday, launch day, ...) with dates, emoji, greeting, image, effect
  * preview any festival on the live site (only you see the preview)
"""
from __future__ import annotations

import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from . import httpclient
from .store import now_iso

HOME = "in"
YEARS_AHEAD = 5
REGIONS: dict[str, tuple[str, ...]] = {
    "en": ("in",), "hi": ("in",), "mr": ("in",), "es": ("es", "mx"), "fr": ("fr",),
    "de": ("de",), "ja": ("jp",), "ru": ("ru",),
}
COUNTRY_NAMES = {"in": "India", "es": "Spain", "mx": "Mexico", "fr": "France", "de": "Germany",
                 "jp": "Japan", "ru": "Russia"}
REGION_LANG = {"in": "en", "es": "es", "mx": "es", "fr": "fr", "de": "de", "jp": "ja", "ru": "ru"}
# Google Calendar's public holiday calendar ids.
FEEDS = {"in": "en.indian", "es": "en.spain", "mx": "en.mexican", "fr": "en.french", "de": "en.german",
         "jp": "en.japanese", "ru": "en.russian"}
GOOGLE_ICS = "https://calendar.google.com/calendar/ical/{id}/public/basic.ics"
# (IANA zone, fallback UTC offset in hours) - "today" is the visitor country's date.
ZONES = {"in": ("Asia/Kolkata", 5.5), "es": ("Europe/Madrid", 1), "mx": ("America/Mexico_City", -6),
         "fr": ("Europe/Paris", 1), "de": ("Europe/Berlin", 1), "jp": ("Asia/Tokyo", 9),
         "ru": ("Europe/Moscow", 3)}

EFFECTS = ("snow", "fireworks", "confetti", "colors", "diyas", "petals", "lanterns", "stars", "kites",
           "bats", "none")
EFFECT_LABELS = {"snow": "Falling snow", "fireworks": "Fireworks", "confetti": "Confetti",
                 "colors": "Colour bursts (Holi)", "diyas": "Glowing diyas & sparks", "petals": "Falling petals",
                 "lanterns": "Rising lanterns", "stars": "Twinkling stars & moon", "kites": "Flying kites",
                 "bats": "Bats & moon", "none": "No effect (greeting only)"}

TRICOLOR_IN = ["#FF9933", "#FFFFFF", "#138808", "#000080"]
WARM = ["#ffb703", "#fb8500", "#ffd166", "#ff7b00", "#fff1c1"]
RAINBOW = ["#ff3d7f", "#ffb300", "#00c853", "#2979ff", "#d500f9", "#ff6d00", "#00e5ff"]


def _g(**kw: str) -> dict[str, str]:
    return kw


# key -> definition. "match" is a regex on Google's event name (fullmatch, case-insensitive,
# after removing "(tentative)" / "(regional holiday)" / "(half-day)"), "countries" the
# calendars it is taken from, "window" the days shown before/after the day itself.
FESTIVALS: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------ India
    "new-year": dict(name="New Year", emoji="🎆", effect="fireworks", palette=["#ffd700", "#ff4d6d", "#4cc9f0", "#b5179e", "#ffffff"],
                     match=r"new year's day", countries=tuple(FEEDS), window=(-1, 1), windows={"ru": (-1, 7)}, weight=80,
                     greet=_g(en="Happy New Year!", hi="नए साल की हार्दिक शुभकामनाएँ!", mr="नववर्षाच्या हार्दिक शुभेच्छा!",
                              es="¡Feliz Año Nuevo!", fr="Bonne année !", de="Frohes neues Jahr!",
                              ja="あけましておめでとうございます！", ru="С Новым годом!")),
    "makar-sankranti": dict(name="Makar Sankranti & Pongal", emoji="🪁", effect="kites", palette=["#ff6b35", "#f7c59f", "#2ec4b6", "#e71d36", "#ffbf00"],
                            match=r"makar sankranti", countries=("in",), window=(0, 1), weight=85,
                            greet=_g(en="Happy Makar Sankranti & Pongal!", hi="मकर संक्रांति की हार्दिक शुभकामनाएँ!",
                                     mr="तिळगूळ घ्या, गोड गोड बोला! मकर संक्रांतीच्या शुभेच्छा!", es="¡Feliz Makar Sankranti!",
                                     fr="Joyeux Makar Sankranti !", de="Frohes Makar Sankranti!",
                                     ja="マカール・サンクランティおめでとう！", ru="С праздником Макар-Санкранти!")),
    "republic-day": dict(name="Republic Day (India)", emoji="🇮🇳", effect="confetti", palette=TRICOLOR_IN,
                         match=r"republic day", countries=("in",), window=(0, 0), weight=75,
                         greet=_g(en="Happy Republic Day! Jai Hind!", hi="गणतंत्र दिवस की हार्दिक शुभकामनाएँ! जय हिन्द!",
                                  mr="प्रजासत्ताक दिनाच्या हार्दिक शुभेच्छा! जय हिंद!", es="¡Feliz Día de la República de la India!",
                                  fr="Joyeuse fête de la République de l’Inde !", de="Alles Gute zum indischen Tag der Republik!",
                                  ja="インド共和国記念日おめでとう！", ru="С Днём Республики Индии!")),
    "holi": dict(name="Holi", emoji="🎨", effect="colors", palette=RAINBOW,
                 match=r"holi", countries=("in",), window=(-1, 1), weight=90,
                 greet=_g(en="Happy Holi! May your life be full of colour.", hi="होली की हार्दिक शुभकामनाएँ! बुरा न मानो, होली है!",
                          mr="होळीच्या रंगीबेरंगी शुभेच्छा!", es="¡Feliz Holi, la fiesta de los colores!",
                          fr="Joyeux Holi, la fête des couleurs !", de="Fröhliches Holi – das Fest der Farben!",
                          ja="ハッピー・ホーリー！色彩の祭りを楽しんで！", ru="С праздником Холи — праздником красок!")),
    "eid-al-fitr": dict(name="Eid al-Fitr", emoji="🌙", effect="stars", palette=["#ffd166", "#06d6a0", "#ffffff", "#118ab2"],
                        match=r"ramzan id|eid al-fitr|eid ul-fitr", countries=("in",), window=(-1, 2), weight=88,
                        greet=_g(en="Eid Mubarak!", hi="ईद मुबारक!", mr="ईद मुबारक!", es="¡Eid Mubarak!", fr="Aïd Moubarak !",
                                 de="Eid Mubarak – frohes Zuckerfest!", ja="イード・ムバーラク！",
                                 ru="Ид Мубарак! С праздником Ураза-байрам!")),
    "eid-al-adha": dict(name="Eid al-Adha", emoji="🌙", effect="stars", palette=["#ffd166", "#06d6a0", "#ffffff", "#118ab2"],
                        match=r"bakrid|eid al-adha", countries=("in",), window=(-1, 1), weight=88,
                        greet=_g(en="Eid al-Adha Mubarak!", hi="ईद-उल-अज़हा मुबारक!", mr="बकरी ईदच्या शुभेच्छा!",
                                 es="¡Eid al-Adha Mubarak!", fr="Aïd el-Kébir Moubarak !",
                                 de="Eid al-Adha Mubarak – frohes Opferfest!", ja="イード・アル＝アドハー・ムバーラク！",
                                 ru="С праздником Курбан-байрам!")),
    "buddha-purnima": dict(name="Buddha Purnima", emoji="🪷", effect="lanterns", palette=["#ffd166", "#f4a261", "#e9c46a", "#ffffff"],
                           match=r"buddha purnima", countries=("in",), window=(0, 0), weight=80,
                           greet=_g(en="Happy Buddha Purnima! Peace and light to all.", hi="बुद्ध पूर्णिमा की हार्दिक शुभकामनाएँ!",
                                    mr="बुद्ध पौर्णिमेच्या हार्दिक शुभेच्छा!", es="¡Feliz Buda Purnima!", fr="Joyeux Bouddha Purnima !",
                                    de="Frohes Buddha Purnima!", ja="ブッダ・プールニマおめでとう！", ru="С праздником Будда Пурнима!")),
    "independence-day": dict(name="Independence Day (India)", emoji="🇮🇳", effect="confetti", palette=TRICOLOR_IN,
                             match=r"independence day", countries=("in",), window=(0, 0), weight=75,
                             greet=_g(en="Happy Independence Day! Jai Hind!", hi="स्वतंत्रता दिवस की हार्दिक शुभकामनाएँ! जय हिन्द!",
                                      mr="स्वातंत्र्य दिनाच्या हार्दिक शुभेच्छा! जय हिंद!",
                                      es="¡Feliz Día de la Independencia de la India!", fr="Joyeuse fête de l’indépendance de l’Inde !",
                                      de="Alles Gute zum indischen Unabhängigkeitstag!", ja="インド独立記念日おめでとう！",
                                      ru="С Днём независимости Индии!")),
    "raksha-bandhan": dict(name="Raksha Bandhan", emoji="🎀", effect="petals", palette=["#ff6fae", "#ffc2d1", "#ffd166", "#ff8fab"],
                           match=r"raksha bandhan", countries=("in",), window=(0, 0), weight=80,
                           greet=_g(en="Happy Raksha Bandhan!", hi="रक्षाबंधन की हार्दिक शुभकामनाएँ!", mr="रक्षाबंधनाच्या हार्दिक शुभेच्छा!",
                                    es="¡Feliz Raksha Bandhan!", fr="Joyeux Raksha Bandhan !", de="Frohes Raksha Bandhan!",
                                    ja="ラクシャ・バンダンおめでとう！", ru="С праздником Ракша-бандхан!")),
    "janmashtami": dict(name="Janmashtami", emoji="🦚", effect="petals", palette=["#1d3557", "#2a9d8f", "#e9c46a", "#457b9d", "#06d6a0"],
                        match=r"janmashtami", countries=("in",), window=(0, 0), weight=82,
                        greet=_g(en="Happy Janmashtami!", hi="जन्माष्टमी की हार्दिक शुभकामनाएँ!", mr="गोकुळाष्टमीच्या हार्दिक शुभेच्छा!",
                                 es="¡Feliz Janmashtami!", fr="Joyeux Janmashtami !", de="Frohes Janmashtami!",
                                 ja="ジャンマシュタミおめでとう！", ru="С праздником Джанмаштами!")),
    "ganesh-chaturthi": dict(name="Ganesh Chaturthi", emoji="🌺", effect="petals", palette=["#ff9f1c", "#ffbf69", "#e63946", "#ffd60a"],
                             match=r"ganesh chaturthi", countries=("in",), window=(0, 2), weight=86,
                             greet=_g(en="Happy Ganesh Chaturthi! Ganpati Bappa Morya!",
                                      hi="गणेश चतुर्थी की हार्दिक शुभकामनाएँ! गणपति बप्पा मोरया!",
                                      mr="गणेशोत्सवाच्या हार्दिक शुभेच्छा! गणपती बाप्पा मोरया!", es="¡Feliz Ganesh Chaturthi!",
                                      fr="Joyeux Ganesh Chaturthi !", de="Frohes Ganesh Chaturthi!",
                                      ja="ガネーシャ・チャトゥルティおめでとう！", ru="С праздником Ганеш-Чатуртхи!")),
    "onam": dict(name="Onam", emoji="🌼", effect="petals", palette=["#ffd60a", "#ff9f1c", "#f8f9fa", "#e63946", "#80b918"],
                 match=r"onam", countries=("in",), window=(-1, 0), weight=80,
                 greet=_g(en="Happy Onam!", hi="ओणम की हार्दिक शुभकामनाएँ!", mr="ओणमच्या हार्दिक शुभेच्छा!", es="¡Feliz Onam!",
                          fr="Joyeux Onam !", de="Frohes Onam!", ja="オーナムおめでとう！", ru="С праздником Онам!")),
    "navratri": dict(name="Navratri", emoji="💃", effect="confetti", palette=["#ff006e", "#fb5607", "#ffbe0b", "#8338ec", "#3a86ff"],
                     match=r"first day of sharad navratri", countries=("in",), window=(0, 8), weight=78,
                     greet=_g(en="Happy Navratri!", hi="नवरात्रि की हार्दिक शुभकामनाएँ!", mr="नवरात्रीच्या हार्दिक शुभेच्छा!",
                              es="¡Feliz Navratri!", fr="Joyeuse Navratri !", de="Frohe Navratri!",
                              ja="ナヴァラートリおめでとう！", ru="С праздником Наваратри!")),
    "dussehra": dict(name="Dussehra", emoji="🏹", effect="fireworks", palette=["#ff7b00", "#ffd000", "#ff0054", "#ffffff"],
                     match=r"dussehra", countries=("in",), window=(0, 0), weight=88,
                     greet=_g(en="Happy Dussehra! May good always win.", hi="विजयादशमी की हार्दिक शुभकामनाएँ!",
                              mr="दसऱ्याच्या हार्दिक शुभेच्छा!", es="¡Feliz Dussehra!", fr="Joyeux Dussehra !",
                              de="Frohes Dussehra!", ja="ダシャラーおめでとう！", ru="С праздником Дуссера!")),
    "diwali": dict(name="Diwali", emoji="🪔", effect="diyas", palette=WARM,
                   match=r"diwali/deepavali|diwali|deepavali", countries=("in",), window=(-2, 2), weight=95,
                   greet=_g(en="Happy Diwali! May the festival of lights brighten your days.",
                            hi="दीपावली की हार्दिक शुभकामनाएँ!", mr="दिवाळीच्या हार्दिक शुभेच्छा!",
                            es="¡Feliz Diwali, el festival de las luces!", fr="Joyeux Diwali, la fête des lumières !",
                            de="Frohes Diwali – das Lichterfest!", ja="ハッピー・ディワリ！光の祭りを楽しんで！",
                            ru="С Дивали — праздником огней!")),
    "guru-nanak-jayanti": dict(name="Guru Nanak Jayanti", emoji="🙏", effect="diyas", palette=WARM,
                               match=r"guru nanak jayanti", countries=("in",), window=(0, 0), weight=80,
                               greet=_g(en="Happy Guru Nanak Jayanti!", hi="गुरु नानक जयंती की हार्दिक शुभकामनाएँ!",
                                        mr="गुरु नानक जयंतीच्या हार्दिक शुभेच्छा!", es="¡Feliz Guru Nanak Jayanti!",
                                        fr="Joyeux Guru Nanak Jayanti !", de="Frohes Guru Nanak Jayanti!",
                                        ja="グル・ナーナク生誕祭おめでとう！", ru="С днём рождения Гуру Нанака!")),
    "christmas": dict(name="Christmas", emoji="🎄", effect="snow", palette=["#ffffff", "#e63946", "#2a9d8f", "#ffd166"],
                      match=r"christmas|christmas day", countries=("in", "jp", "es", "mx", "fr", "de"), window=(-7, 1), weight=70,
                      greet=_g(en="Merry Christmas!", hi="क्रिसमस की हार्दिक शुभकामनाएँ!", mr="नाताळच्या हार्दिक शुभेच्छा!",
                               es="¡Feliz Navidad!", fr="Joyeux Noël !", de="Frohe Weihnachten!", ja="メリークリスマス！",
                               ru="С Рождеством!")),
    # ----------------------------------------------------------------- Russia
    "orthodox-christmas": dict(name="Orthodox Christmas", emoji="🎄", effect="snow", palette=["#ffffff", "#d4af37", "#c1121f"],
                               match=r"orthodox christmas day", countries=("ru",), window=(0, 0), weight=85,
                               greet=_g(en="Merry Orthodox Christmas!", ru="С Рождеством Христовым!")),
    "womens-day": dict(name="International Women's Day", emoji="💐", effect="petals", palette=["#ffd60a", "#ffc300", "#ff8fab", "#80b918"],
                       match=r"international women's day", countries=("ru", "de"), window=(0, 0), weight=78,
                       greet=_g(en="Happy International Women's Day!", ru="С 8 Марта! С Международным женским днём!",
                                de="Alles Gute zum Internationalen Frauentag!", hi="अंतरराष्ट्रीय महिला दिवस की शुभकामनाएँ!",
                                mr="जागतिक महिला दिनाच्या शुभेच्छा!", es="¡Feliz Día Internacional de la Mujer!",
                                fr="Bonne Journée internationale des droits des femmes !", ja="国際女性デーおめでとう！")),
    "russia-day": dict(name="Russia Day", emoji="🇷🇺", effect="confetti", palette=["#ffffff", "#0039a6", "#d52b1e"],
                       match=r"russia day", countries=("ru",), window=(0, 0), weight=72,
                       greet=_g(en="Happy Russia Day!", ru="С Днём России!")),
    # ------------------------------------------------------------------ Japan
    "hinamatsuri": dict(name="Hinamatsuri (Dolls' Festival)", emoji="🎎", effect="petals", palette=["#ffb7c5", "#ffc8dd", "#ffffff", "#ff8fab"],
                        match=r"dolls' festival/girls' festival", countries=("jp",), window=(0, 0), weight=80,
                        greet=_g(en="Happy Hinamatsuri – the Dolls' Festival!", ja="ひな祭りおめでとう！")),
    "kodomo-no-hi": dict(name="Children's Day (Kodomo no Hi)", emoji="🎏", effect="confetti", palette=["#1d4ed8", "#ef4444", "#22c55e", "#facc15", "#ffffff"],
                         match=r"children's day", countries=("jp",), window=(0, 0), weight=80,
                         greet=_g(en="Happy Children's Day (Kodomo no Hi)!", ja="こどもの日おめでとう！")),
    "tanabata": dict(name="Tanabata (Star Festival)", emoji="🎋", effect="stars", palette=["#a5b4fc", "#fde68a", "#f9a8d4", "#ffffff"],
                     match=r"star festival", countries=("jp",), window=(0, 0), weight=80,
                     greet=_g(en="Happy Tanabata! May your wish reach the stars.", ja="七夕の夜、願いが星に届きますように。")),
    # ---------------------------------------------------------- Spain & Mexico
    "epiphany": dict(name="Three Kings' Day", emoji="👑", effect="confetti", palette=["#ffd700", "#b5179e", "#4361ee", "#ffffff"],
                     match=r"epiphany", countries=("es",), window=(0, 0), weight=78,
                     greet=_g(en="Happy Three Kings' Day!", es="¡Feliz Día de Reyes!")),
    "carnival": dict(name="Carnival", emoji="🎭", effect="confetti", palette=RAINBOW,
                     match=r"carnival tuesday", countries=("es", "de"), window=(-4, 0), weight=76,
                     greet=_g(en="Happy Carnival!", es="¡Feliz Carnaval!", de="Helau und Alaaf – fröhlichen Karneval!",
                              fr="Joyeux Carnaval !")),
    "easter": dict(name="Easter", emoji="🐣", effect="petals", palette=["#ffd6e0", "#c1fba4", "#fff3b0", "#a0c4ff", "#cdb4db"],
                   match=r"easter sunday", countries=("es", "mx", "fr", "de"), window=(-2, 1), weight=80,
                   greet=_g(en="Happy Easter!", es="¡Felices Pascuas!", fr="Joyeuses Pâques !", de="Frohe Ostern!")),
    "day-of-the-dead": dict(name="Day of the Dead", emoji="💀", effect="petals", palette=["#ff8c00", "#ffb703", "#e85d04", "#9d4edd"],
                            match=r"all souls' day", countries=("mx",), window=(-1, 0), weight=85,
                            greet=_g(en="Happy Day of the Dead!", es="¡Feliz Día de Muertos!")),
    "mexico-independence": dict(name="Independence Day (Mexico)", emoji="🇲🇽", effect="confetti", palette=["#006847", "#ffffff", "#ce1126"],
                                match=r"independence day", countries=("mx",), window=(-1, 0), weight=75,
                                greet=_g(en="Happy Mexican Independence Day!", es="¡Viva México! Feliz Día de la Independencia.")),
    # ----------------------------------------------------------------- France
    "bastille-day": dict(name="Bastille Day", emoji="🇫🇷", effect="fireworks", palette=["#0055a4", "#ffffff", "#ef4135"],
                         match=r"bastille day", countries=("fr",), window=(0, 0), weight=75,
                         greet=_g(en="Happy Bastille Day!", fr="Bonne fête nationale ! Joyeux 14 Juillet !")),
    # ---------------------------------------------------------------- Germany
    "german-unity": dict(name="German Unity Day", emoji="🇩🇪", effect="confetti", palette=["#000000", "#dd0000", "#ffce00"],
                         match=r"day of german unity", countries=("de",), window=(0, 0), weight=75,
                         greet=_g(en="Happy German Unity Day!", de="Schönen Tag der Deutschen Einheit!")),
    "st-martin": dict(name="St. Martin's Day", emoji="🏮", effect="lanterns", palette=["#ffb703", "#fb8500", "#ffd166"],
                      match=r"st\. martin's day", countries=("de",), window=(0, 0), weight=78,
                      greet=_g(en="Happy St. Martin's Day!", de="Frohen Martinstag!")),
    "advent": dict(name="Advent", emoji="🕯️", effect="snow", palette=["#ffffff", "#ffd166", "#2a9d8f"],
                   match=r"first advent sunday", countries=("de",), window=(0, 20), weight=40,
                   greet=_g(en="Happy Advent!", de="Eine schöne Adventszeit!")),
    "halloween": dict(name="Halloween", emoji="🎃", effect="bats", palette=["#ff7518", "#6a0dad", "#1b1b1b"],
                      match=r"halloween", countries=("de",), window=(-2, 0), weight=76,
                      greet=_g(en="Happy Halloween!", de="Fröhliches Halloween!")),
}
_MATCHERS = [(key, re.compile(f.get("match", "^$"), re.I), set(f["countries"])) for key, f in FESTIVALS.items()]

# --------------------------------------------------- built-in fallback dates --
# Used only while Google's calendars have never been loaded. India's lunar festivals come
# from Google Calendar / Drik Panchang (checked November 2026); the rest are fixed or computed.
_FALLBACK_IN = {
    "diwali": ["2026-11-08", "2027-10-29", "2028-10-17"],
    "holi": ["2027-03-22", "2028-03-11"],
    "ganesh-chaturthi": ["2027-09-04", "2028-08-23"],
    "dussehra": ["2027-10-09", "2028-09-27"],
    "navratri": ["2027-09-30"],
    "eid-al-fitr": ["2027-03-10", "2028-02-27"],
    "eid-al-adha": ["2027-05-17", "2028-05-05"],
    "buddha-purnima": ["2027-05-20"],
    "raksha-bandhan": ["2027-08-17"],
    "janmashtami": ["2027-08-25"],
    "onam": ["2027-09-12"],
    "guru-nanak-jayanti": ["2027-11-14"],
}
_FIXED = {  # key -> {country: "MM-DD"}
    "new-year": {cc: "01-01" for cc in FEEDS},
    "makar-sankranti": {"in": "01-14"}, "republic-day": {"in": "01-26"}, "independence-day": {"in": "08-15"},
    "christmas": {cc: "12-25" for cc in ("in", "jp", "es", "mx", "fr", "de")},
    "orthodox-christmas": {"ru": "01-07"}, "womens-day": {"ru": "03-08", "de": "03-08"}, "russia-day": {"ru": "06-12"},
    "hinamatsuri": {"jp": "03-03"}, "kodomo-no-hi": {"jp": "05-05"}, "tanabata": {"jp": "07-07"},
    "epiphany": {"es": "01-06"}, "day-of-the-dead": {"mx": "11-02"}, "mexico-independence": {"mx": "09-16"},
    "bastille-day": {"fr": "07-14"}, "german-unity": {"de": "10-03"}, "st-martin": {"de": "11-11"},
    "halloween": {"de": "10-31"},
}


def easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _first_advent(year: int) -> date:
    christmas = date(year, 12, 25)
    last_sunday = christmas - timedelta(days=(christmas.weekday() + 1) % 7 or 7)
    return last_sunday - timedelta(weeks=3)


def fallback_events(first_year: int, years: int = YEARS_AHEAD) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    span = range(first_year, first_year + years + 1)
    for key, per_country in _FIXED.items():
        for cc, md in per_country.items():
            out.setdefault(key, {})[cc] = [f"{y}-{md}" for y in span]
    for y in span:
        e = easter(y)
        for cc in ("es", "mx", "fr", "de"):
            out.setdefault("easter", {}).setdefault(cc, []).append(e.isoformat())
        for cc in ("es", "de"):
            out.setdefault("carnival", {}).setdefault(cc, []).append((e - timedelta(days=47)).isoformat())
        out.setdefault("advent", {}).setdefault("de", []).append(_first_advent(y).isoformat())
    for key, dates in _FALLBACK_IN.items():
        out.setdefault(key, {})["in"] = list(dates)
    return out


# ------------------------------------------------------------- Google sync --
_CLEAN_RE = re.compile(r"\s*\((?:tentative|regional holiday|half-day)\)", re.I)


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_ics(text: str) -> list[tuple[str, str]]:
    """[(YYYY-MM-DD, name)] for every all-day event in an iCalendar file."""
    events: list[tuple[str, str]] = []
    start = name = ""
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            start = name = ""
        elif line.startswith("DTSTART"):
            value = line.split(":", 1)[-1].strip()
            if re.fullmatch(r"\d{8}", value):
                start = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        elif line.startswith("SUMMARY"):
            name = line.split(":", 1)[-1].strip().replace("\\,", ",").replace("\\;", ";")
        elif line == "END:VEVENT" and start and name:
            events.append((start, name))
    return events


def match_events(cc: str, events: list[tuple[str, str]], first_year: int, last_year: int) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for day, raw in events:
        if not first_year <= int(day[:4]) <= last_year:
            continue
        name = _CLEAN_RE.sub("", raw).replace("’", "'").strip()
        for key, rx, countries in _MATCHERS:
            if cc in countries and rx.fullmatch(name):
                found.setdefault(key, set()).add(day)
    return {key: sorted(days) for key, days in found.items()}


def fetch_country(cc: str, first_year: int, last_year: int) -> dict[str, Any]:
    url = GOOGLE_ICS.format(id=quote(f"{FEEDS[cc]}#holiday@group.v.calendar.google.com", safe="."))
    try:
        resp = httpclient.request("GET", url, headers={"Accept": "text/calendar"}, connect_timeout=6,
                                  read_timeout=15, max_bytes=3_000_000)
    except httpclient.FetchError as exc:
        return {"ok": False, "error": str(exc)[:160]}
    if not resp.ok or "BEGIN:VCALENDAR" not in resp.text()[:200]:
        return {"ok": False, "error": f"HTTP {resp.status}"}
    events = parse_ics(resp.text())
    matched = match_events(cc, events, first_year, last_year)
    last = max((d for d, _ in events), default="")
    return {"ok": True, "events": matched, "count": sum(len(v) for v in matched.values()), "last_date": last}


def fetch_all(today: date | None = None) -> dict[str, Any]:
    """Download every country calendar in parallel (no store lock held)."""
    today = today or datetime.now(timezone.utc).date()
    first, last = today.year, today.year + YEARS_AHEAD
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        results = dict(zip(FEEDS, pool.map(lambda cc: fetch_country(cc, first, last), FEEDS)))
    return {"checked_at": now_iso(), "first_year": first, "last_year": last, "countries": results}


def apply(doc: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Merge a fetch_all() result. A country that failed keeps its last good dates."""
    occ = doc.setdefault("occasions", {})
    cal = occ.setdefault("calendar", {})
    stored: dict[str, dict[str, list[str]]] = cal.setdefault("events", {})
    feeds = cal.setdefault("feeds", {})
    changes: list[str] = []
    horizon = (date.fromisoformat(result["checked_at"][:10]) + timedelta(days=400)).isoformat()
    for cc, res in result["countries"].items():
        if not res.get("ok"):
            feeds[cc] = {**feeds.get(cc, {}), "ok": False, "error": res.get("error", ""), "checked_at": result["checked_at"]}
            continue
        feeds[cc] = {"ok": True, "error": "", "checked_at": result["checked_at"], "count": res["count"],
                     "last_date": res.get("last_date", "")}
        for key in FESTIVALS:
            new = res["events"].get(key, [])
            old = stored.get(key, {}).get(cc, [])
            soon_new = {d for d in new if d <= horizon}
            soon_old = {d for d in old if d <= horizon}
            if old and soon_new != soon_old:
                added, gone = sorted(soon_new - soon_old), sorted(soon_old - soon_new)
                changes.append(f"{FESTIVALS[key]['name']} ({COUNTRY_NAMES[cc]}): "
                               + ", ".join([f"+{d}" for d in added] + [f"-{d}" for d in gone]))
            if new:
                stored.setdefault(key, {})[cc] = new
            elif cc in stored.get(key, {}):
                stored[key].pop(cc)
    ok = sum(1 for r in result["countries"].values() if r.get("ok"))
    cal.update(checked_at=result["checked_at"], source="Google Calendar public holidays",
               ok=ok, total=len(result["countries"]))
    if ok:
        cal["last_success"] = result["checked_at"]
        cal["until"] = f"{result['last_year']}-12-31"
    if changes:
        cal["changes"] = ([f"{result['checked_at'][:10]}: {c}" for c in changes] + cal.get("changes", []))[:30]
    return {"ok": ok, "total": len(result["countries"]), "changes": len(changes)}


# ---------------------------------------------------------------- settings --
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def settings_of(doc: dict[str, Any]) -> dict[str, Any]:
    raw = (doc.get("occasions") or {}).get("settings") or {}
    mode = raw.get("mode") if raw.get("mode") in ("auto", "forced", "off") else "auto"
    return {"mode": mode, "forced": str(raw.get("forced") or ""), "until": str(raw.get("until") or ""),
            "home_fallback": raw.get("home_fallback", True) is not False}


def overrides_of(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return (doc.get("occasions") or {}).get("festivals") or {}


def custom_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in (doc.get("occasions") or {}).get("custom") or [] if isinstance(c, dict) and c.get("key")]


def css_url(url: str) -> str:
    """An https image URL made safe to sit inside url("...") in a <style> block ('' if unsafe)."""
    url = (url or "").strip()
    if not re.match(r"^https://[^\s]+$", url, re.I) or len(url) > 600:
        return ""
    return quote(url, safe=":/?#[]@!$&*+,;=%-._~")


def validate_image(url: str) -> str:
    url = (url or "").strip()
    if url and not css_url(url):
        raise ValueError("The image must be an https:// link (e.g. from GitHub, Unsplash or Imgur).")
    return url


def save_settings(doc: dict[str, Any], *, mode: str, forced: str, until: str, home_fallback: bool) -> None:
    if mode not in ("auto", "forced", "off"):
        raise ValueError("Unknown mode.")
    if mode == "forced" and forced not in FESTIVALS and forced not in {c["key"] for c in custom_of(doc)}:
        raise ValueError("Pick the festival to show.")
    if until and not _DATE.match(until):
        raise ValueError("Use a date like 2026-12-31.")
    doc.setdefault("occasions", {})["settings"] = {"mode": mode, "forced": forced if mode == "forced" else "",
                                                   "until": until if mode == "forced" else "",
                                                   "home_fallback": bool(home_fallback)}


def save_festival(doc: dict[str, Any], *, key: str, enabled: bool, greeting: str, image: str, effect: str) -> None:
    if key not in FESTIVALS:
        raise KeyError(key)
    if effect and effect not in EFFECTS:
        raise ValueError("Unknown effect.")
    entry = {"enabled": bool(enabled), "greeting": " ".join(greeting.split())[:160],
             "image": validate_image(image), "effect": effect or ""}
    festivals = doc.setdefault("occasions", {}).setdefault("festivals", {})
    if entry == {"enabled": True, "greeting": "", "image": "", "effect": ""}:
        festivals.pop(key, None)  # back to the defaults
    else:
        festivals[key] = entry


MAX_CUSTOM = 30


def save_custom(doc: dict[str, Any], *, key: str, name: str, emoji: str, start: str, end: str, yearly: bool,
                greeting: str, image: str, effect: str, color: str, enabled: bool) -> str:
    name = " ".join(name.split())[:60]
    if not name:
        raise ValueError("Give your occasion a name.")
    if not _DATE.match(start or "") or not _DATE.match(end or start or ""):
        raise ValueError("Pick the start (and end) date.")
    end = end or start
    if end < start:
        raise ValueError("The end date is before the start date.")
    if (date.fromisoformat(end) - date.fromisoformat(start)).days > 60:
        raise ValueError("An occasion can last at most 60 days.")
    if effect not in EFFECTS:
        raise ValueError("Unknown effect.")
    color = color if _HEX.match(color or "") else "#f59e0b"
    items = custom_of(doc)
    if key:
        target = next((c for c in items if c["key"] == key), None)
        if target is None:
            raise KeyError(key)
    else:
        if len(items) >= MAX_CUSTOM:
            raise ValueError(f"You can have up to {MAX_CUSTOM} occasions of your own.")
        target = {"key": "my-" + secrets.token_hex(4)}
        items.append(target)
    target.update(name=name, emoji=(emoji or "🎉").strip()[:8], start=start, end=end, yearly=bool(yearly),
                  greeting=" ".join(greeting.split())[:160], image=validate_image(image), effect=effect,
                  color=color, enabled=bool(enabled))
    doc.setdefault("occasions", {})["custom"] = items
    return target["key"]


def delete_custom(doc: dict[str, Any], key: str) -> None:
    items = custom_of(doc)
    if key not in {c["key"] for c in items}:
        raise KeyError(key)
    doc.setdefault("occasions", {})["custom"] = [c for c in items if c["key"] != key]
    occ = doc["occasions"]
    if (occ.get("settings") or {}).get("forced") == key:
        occ["settings"].update(mode="auto", forced="", until="")


# --------------------------------------------------------------- selection --
def local_today(cc: str, now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    zone, offset = ZONES.get(cc, ZONES[HOME])
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo(zone)).date()
    except Exception:  # noqa: BLE001 - no tz database: a fixed offset is close enough for dates
        return (now + timedelta(hours=offset)).date()


def _events(doc: dict[str, Any], today: date) -> tuple[dict[str, dict[str, list[str]]], set[str]]:
    """(events, countries with live Google data). Missing countries fall back to built-in dates."""
    cal = (doc.get("occasions") or {}).get("calendar") or {}
    google = cal.get("events") or {}
    live = {cc for cc, f in (cal.get("feeds") or {}).items() if f.get("count")}
    merged: dict[str, dict[str, list[str]]] = {}
    builtin = fallback_events(today.year - 1, YEARS_AHEAD + 1)
    for key in FESTIVALS:
        per = dict(builtin.get(key, {}))
        for cc in live:
            per.pop(cc, None)  # Google is the source of truth for countries it covers
        per.update({cc: d for cc, d in (google.get(key) or {}).items() if cc in live})
        merged[key] = per
    return merged, live


def _window(key: str, cc: str) -> tuple[int, int]:
    f = FESTIVALS[key]
    return f.get("windows", {}).get(cc, f["window"])


def candidates(doc: dict[str, Any], cc: str, today: date, events=None) -> list[dict[str, Any]]:
    events = events if events is not None else _events(doc, today)[0]
    overrides = overrides_of(doc)
    found = []
    for key, per in events.items():
        if overrides.get(key, {}).get("enabled") is False:
            continue
        before, after = _window(key, cc)
        for day_s in per.get(cc, []):
            day = date.fromisoformat(day_s)
            if day + timedelta(days=before) <= today <= day + timedelta(days=after):
                found.append({"key": key, "day": day_s, "country": cc,
                              "score": FESTIVALS[key]["weight"] - 3 * abs((today - day).days)})
    found.sort(key=lambda c: c["score"], reverse=True)
    return found


def _custom_active(item: dict[str, Any], today: date) -> bool:
    if not item.get("enabled", True):
        return False
    start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
    if item.get("yearly"):
        for year in (today.year - 1, today.year):
            try:
                s = start.replace(year=year)
                e = s + (end - start)
            except ValueError:  # 29 February
                continue
            if s <= today <= e:
                return True
        return False
    return start <= today <= end


def pick(doc: dict[str, Any], lang: str, now: datetime | None = None) -> tuple[str, str] | None:
    """(key, country) of what a visitor using `lang` should see right now, or None."""
    conf = settings_of(doc)
    if conf["mode"] == "off":
        return None
    regions = REGIONS.get(lang, (HOME,))
    today_home = local_today(regions[0], now)
    if conf["mode"] == "forced" and conf["forced"]:
        if not conf["until"] or today_home.isoformat() <= conf["until"]:
            return conf["forced"], regions[0]
    for item in custom_of(doc):  # your own occasions come first, for everyone
        if _custom_active(item, today_home):
            return item["key"], regions[0]
    events, live = _events(doc, today_home)
    for cc in regions:
        if cc != HOME and cc not in live:
            continue  # that country's Google calendar failed / never loaded -> India below
        found = candidates(doc, cc, local_today(cc, now), events)
        if found:
            return found[0]["key"], cc
    if HOME not in regions:
        calendar_failed = any(cc not in live for cc in regions)
        if conf["home_fallback"] or calendar_failed:  # nothing local today, or the API failed: India
            found = candidates(doc, HOME, local_today(HOME, now), events)
            if found:
                return found[0]["key"], HOME
    return None


def present(doc: dict[str, Any], key: str, lang: str, country: str = HOME) -> dict[str, Any] | None:
    """Everything the page needs to show an occasion (only safe values: colours/effects from lists)."""
    custom = next((c for c in custom_of(doc) if c["key"] == key), None)
    year = local_today(country).year
    if custom:
        color = custom.get("color") or "#f59e0b"
        return {"key": key, "name": custom["name"], "emoji": custom.get("emoji") or "🎉",
                "greeting": custom.get("greeting") or custom["name"], "effect": custom.get("effect") or "confetti",
                "palette": [color, "#ffffff", "#ffd166"], "image": css_url(custom.get("image", "")),
                "id": f"{key}-{year}", "custom": True}
    f = FESTIVALS.get(key)
    if not f:
        return None
    o = overrides_of(doc).get(key, {})
    greet = f["greet"]
    return {"key": key, "name": f["name"], "emoji": f["emoji"],
            "greeting": o.get("greeting") or greet.get(lang) or greet["en"],
            "effect": o.get("effect") or f["effect"], "palette": list(f["palette"]),
            "image": css_url(o.get("image", "")), "id": f"{key}-{year}", "custom": False}


def for_visitor(doc: dict[str, Any], lang: str, preview: str = "") -> dict[str, Any] | None:
    if preview:
        return present(doc, preview, lang)
    chosen = pick(doc, lang)
    return present(doc, chosen[0], lang, chosen[1]) if chosen else None


# ------------------------------------------------------------------- admin --
def upcoming(doc: dict[str, Any], cc: str, limit: int = 8) -> list[dict[str, str]]:
    today = local_today(cc)
    events, _ = _events(doc, today)
    rows = []
    for key, per in events.items():
        for day in per.get(cc, []):
            if day >= today.isoformat():
                rows.append({"key": key, "name": FESTIVALS[key]["name"], "emoji": FESTIVALS[key]["emoji"], "day": day})
    rows.sort(key=lambda r: r["day"])
    return rows[:limit]


def admin_view(doc: dict[str, Any]) -> dict[str, Any]:
    cal = (doc.get("occasions") or {}).get("calendar") or {}
    today = local_today(HOME)
    events, live = _events(doc, today)
    known_until = max((d for per in (cal.get("events") or {}).values() for days in per.values() for d in days),
                      default="")
    now_showing = {}
    for lang in REGIONS:
        chosen = pick(doc, lang)
        now_showing[lang] = present(doc, chosen[0], lang, chosen[1])["name"] if chosen else ""
    festivals = []
    for key, f in FESTIVALS.items():
        o = overrides_of(doc).get(key, {})
        countries = sorted({cc for cc, days in events.get(key, {}).items() if days} or set(f["countries"]))
        festivals.append({"key": key, "name": f["name"], "emoji": f["emoji"], "countries": countries,
                          "default_effect": f["effect"], "default_greeting": f["greet"]["en"],
                          "enabled": o.get("enabled", True), "greeting": o.get("greeting", ""),
                          "image": o.get("image", ""), "effect": o.get("effect", ""),
                          "preview_lang": "en" if HOME in countries or not countries else REGION_LANG.get(countries[0], "en")})
    return {
        "settings": settings_of(doc),
        "festivals": festivals,
        "custom": custom_of(doc),
        "effects": [{"key": k, "label": EFFECT_LABELS[k]} for k in EFFECTS],
        "calendar": {"checked_at": cal.get("checked_at", ""), "last_success": cal.get("last_success", ""),
                     "ok": cal.get("ok", 0), "total": cal.get("total", len(FEEDS)), "known_until": known_until,
                     "feeds": {cc: {**(cal.get("feeds") or {}).get(cc, {}), "name": COUNTRY_NAMES[cc]} for cc in FEEDS},
                     "changes": cal.get("changes", [])[:10], "live": sorted(live)},
        "upcoming": {cc: upcoming(doc, cc) for cc in FEEDS},
        "now_showing": now_showing,
        "regions": {lang: [COUNTRY_NAMES[c] for c in ccs] for lang, ccs in REGIONS.items()},
        "max_custom": MAX_CUSTOM,
    }
