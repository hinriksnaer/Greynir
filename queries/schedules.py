"""

    Greynir: Natural language processing for Icelandic

    TV & radio schedule query response module

    Copyright (C) 2020 Miðeind ehf.

       This program is free software: you can redistribute it and/or modify
       it under the terms of the GNU General Public License as published by
       the Free Software Foundation, either version 3 of the License, or
       (at your option) any later version.
       This program is distributed in the hope that it will be useful,
       but WITHOUT ANY WARRANTY; without even the implied warranty of
       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
       GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program. If not, see http://www.gnu.org/licenses/.


    This module handles queries related to television and radio schedules.
    Only handles RÚV television (for now).

"""

# TODO: Support TV schedule queries for other stations than RÚV
# TODO: Support radio schedules
# TODO: "Hvað er í sjónvarpinu í kvöld?"
# TODO: Fix formatting issues w. trailing spaces, periods at the end of answer str.

import logging
import re
import random
from datetime import datetime, timedelta

from queries import query_json_api, gen_answer


_SCHEDULES_QTYPE = "Schedule"

_TELEVISION_QKEY = "TelevisionSchedule"
_TELEVISION_EVENING_QKEY = "TelevisionEvening"


TOPIC_LEMMAS = ["sjónvarp", "sjónvarpsdagskrá", "dagskrá", "rúv", "ríkissjónvarp"]


def help_text(lemma):
    """ Help text to return when query.py is unable to parse a query but
        one of the above lemmas is found in it """
    return "Ég get svarað ef þú spyrð til dæmis: {0}?".format(
        random.choice(
            (
                "Hvað er í sjónvarpinu í kvöld",
                "Hvað er á RÚV í augnablikinu",
                "Hvaða efni er verið að sýna í sjónvarpinu",
            )
        )
    )


# This module wants to handle parse trees for queries
HANDLE_TREE = True

# The context-free grammar for the queries recognized by this plug-in module
# Uses "QSch" as prefix for grammar namespace
GRAMMAR = """

Query →
    QSchedule '?'?

QSchedule →
    QScheduleTV | QScheduleRadio

QScheduleTV →
    QSchTelevisionQuery
    | QSchTelevisionEveningQuery

QSchTelevisionQuery →
    QSchWhatIsNom QSchEiginlega? QSchGoingOn? QSchOnTV QSchNow?
    | QSchWhatIsDative QSchEiginlega? QSchBeingShown QSchOnTV QSchNow?
    | "dagskrá" QSchBeingShown? QSchOnTV? QSchNow?
    | "dagskrá" QSchGoingOn? QSchOnTV? QSchNow?
    | QSchWhatIsNom "á" "dagskrá" QSchOnTV QSchNow?

QSchTelevisionEveningQuery →
   "hvað" "er" QSchEiginlega? QSchOnSchedule? QSchOnTV QSchThisEvening
   | "hvernig" "er" QSchEiginlega? QSchTheSchedule QSchOnTV? QSchThisEvening
   | "hver" "er" QSchEiginlega? QSchTheSchedule QSchOnTV? QSchThisEvening

QScheduleRadio →
    QSchRadioStationNowQuery

QSchRadioStationNowQuery →
    QSchWhatIsNom QSchEiginlega? "á" QRadioStation

QSchWhatIsNom →
    "hvað" "er" | "hvaða" "þáttur" "er" | "hvaða" "dagskrárliður" "er" | "hvaða" "efni" "er"

QSchWhatIsDative →
    "hvað" "er" | "hvaða" "þátt" "er" | "hvaða" "dagskrárlið" "er" | "hvaða" "efni" "er"

QSchOnTV →
    QSchOnRUV | QSchOnStod2

QSchOnRUV →
    "í" "sjónvarpinu" | "á" "rúv" | "í" "ríkissjónvarpinu"
    | "á" "stöð" "eitt" | "hjá"? "rúv" | "sjónvarpsins" | "sjónvarps"

QSchOnStod2 →
    "á" "stöð" "tvö" | "á" "stöð" "2"

QSchOnRadio →
    "í" "útvarpinu" | "í" "ríkisútvarpinu" | "á" "ríkisútvarpinu"

# Supported radio stations
QRadioStation →
    QSchRas1 | QSchRas2

QSchRas2 →
    "rás" "tvö" | "rás" "2"

QSchRas1 →
    "rás" "eitt" | "rás" "1"

QSchNow →
    "nákvæmlega"? "núna" | "eins" "og" "stendur" | "í" "augnablikinu"

QSchGoingOn →
    "í" "gangi"

QSchBeingShown →
    "verið" "að" "sýna" 

QSchEiginlega →
    "eiginlega"

QSchOnSchedule →
    "á" "dagskrá"
    | "í" "dagskrá"
    | "á" "dagskránni"
    | "í" "boði"
    | "boðið" "upp" "á"

QSchTheSchedule →
    "dagskráin" | "sjónvarpsdagskráin"

QSchThisEvening →
    "núna"? "í" "kvöld"

$score(+55) QSchedule

"""


def QSchTelevisionQuery(node, params, result):
    # Set the query type
    result.qtype = _SCHEDULES_QTYPE
    result.qkey = _TELEVISION_QKEY


def QSchTelevisionEveningQuery(node, params, result):
    # Set the query type
    result.qtype = _SCHEDULES_QTYPE
    result.qkey = _TELEVISION_EVENING_QKEY


def _clean_desc(d):
    """ Return first sentence in multi-sentence string. """
    return d.replace("Dr.", "Doktor").replace("?", ".").split(".")[0]


_RUV_SCHEDULE_API_ENDPOINT = "https://apis.is/tv/ruv/"
_API_ERRMSG = "Ekki tókst að sækja sjónvarpsdagskrá."
_CACHED_SCHEDULE = None
_LAST_FETCHED = None


def _query_tv_schedule_api():
    """ Fetch current television schedule from API, or return cached copy. """
    global _CACHED_SCHEDULE
    global _LAST_FETCHED
    if (
        not _CACHED_SCHEDULE
        or not _LAST_FETCHED
        or _LAST_FETCHED.date() != datetime.today().date()
    ):
        _CACHED_SCHEDULE = None
        sched = query_json_api(_RUV_SCHEDULE_API_ENDPOINT)
        if sched and "results" in sched and len(sched["results"]):
            _LAST_FETCHED = datetime.utcnow()
            _CACHED_SCHEDULE = sched["results"]
    return _CACHED_SCHEDULE


def _span(p):
    """ Return the time span of a program """
    start = datetime.strptime(p["startTime"], "%Y-%m-%d %H:%M:%S")
    h, m, s = p["duration"].split(":")
    dur = timedelta(hours=int(h), minutes=int(m))
    return start, start + dur


def _curr_prog(sched):
    """ Return current TV program, given a TV schedule 
        i.e. a list of programs in chronological sequence. """
    now = datetime.utcnow()
    for p in sched:
        t1, t2 = _span(p)
        if t1 <= now < t2:
            return p
        if t1 > now:
            # We're past the current time in the schedule
            break
    return None


def _evening_prog(sched):
    """ Return programs on a TV schedule starting from 19:00,
        or at the current time if later """
    start = datetime.utcnow()
    if (start.hour, start.minute) < (19, 0):
        start = datetime(start.year, start.month, start.day, 19, 0, 0)
    result = []
    for p in sched:
        t1, t2 = _span(p)
        if (t1 <= start < t2) or t1 >= start:
            # Item is being shown or will be shown later in the evening
            result.append(p)
    return result


def _gen_curr_program_answer(q):
    """ Generate answer to query about current TV program """
    sched = _query_tv_schedule_api()
    if not sched:
        return gen_answer(_API_ERRMSG)

    prog = _curr_prog(sched)
    if not prog:
        return gen_answer("Það er engin dagskrá á RÚV núna.")

    title = prog["title"]
    ep = "" if "fréttir" in title.lower() else ""
    answ = "RÚV er að sýna {0}{1}. {2}.".format(
        ep, title, _clean_desc(prog["description"])
    )
    return gen_answer(answ)


def _gen_evening_program_answer(q):
    """ Generate answer to query about the evening's TV programs """
    sched = _query_tv_schedule_api()
    if not sched:
        return gen_answer(_API_ERRMSG)

    prog = _evening_prog(sched)
    if not prog:
        return gen_answer("Það er enginn liður eftir á dagskrá RÚV.")

    answ = ["Klukkan"]
    for p in prog:
        answ.append("{0} : {1}.\n".format(p["startTime"][11:16], p["title"]))
    voice_answer = "".join(answ)
    answer = "".join(answ[1:]).replace(" : ", " ")
    return dict(answer=answer), answer, voice_answer


def sentence(state, result):
    """ Called when sentence processing is complete """
    q = state["query"]
    if "qtype" in result:
        # Successfully matched a query type
        q.set_qtype(result.qtype)
        q.set_key(result.qkey)

        try:
            if result.qkey == _TELEVISION_QKEY:
                r = _gen_curr_program_answer(q)
            else:
                r = _gen_evening_program_answer(q)
            q.set_answer(*r)
            q.set_beautified_query(q._beautified_query.replace("rúv", "RÚV"))
            # TODO: Set intelligent expiry time
            q.set_expires(datetime.utcnow() + timedelta(minutes=3))
        except Exception as e:
            logging.warning(
                "Exception while processing TV schedule query: {0}".format(e)
            )
            q.set_error("E_EXCEPTION: {0}".format(e))

    else:
        q.set_error("E_QUERY_NOT_UNDERSTOOD")
