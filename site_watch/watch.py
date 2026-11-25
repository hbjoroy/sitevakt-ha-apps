"""Independent HTTPS inventory and store-and-forward notifications for HA OS."""
import hashlib
from http.client import HTTPException
import json
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from dashboard import Dashboard

REPORTS = "https://sproyt.bjoroy.me/api/v1/integrations/grafana/reports"
USER_AGENT = "Sitevakt/0.2.0"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def probe(target):
    try:
        request = Request(target["url"], headers={"User-Agent": USER_AGENT})
        with build_opener(NoRedirect).open(request, timeout=5) as response:
            body = response.read(65537)
            if response.status != 200 or len(body) > 65536:
                return False
            check = target["check"]
            if check in ("ok", "ready"):
                return body.strip() == check.encode()
            data = json.loads(body)
            if check == "grafana":
                return isinstance(data, dict) and data.get("database") == "ok"
            return isinstance(data, dict) and data.get("name") == "Home Assistant"
    except (OSError, ValueError, EOFError, HTTPException):
        return False


def database(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS health (
            name TEXT PRIMARY KEY, failures INTEGER, first_bad INTEGER,
            down_since INTEGER, last_check INTEGER);
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
    """)
    return db


def enqueue(db, observer, key, title, summary, start, end):
    identity = hashlib.sha256(f"{observer}/{key}".encode()).hexdigest()
    report = dict(version="1", report_id=identity, title=title, summary=summary,
                  period_start=stamp(start), period_end=stamp(max(start, end)))
    db.execute("INSERT INTO outbox(payload) VALUES (?)", (json.dumps(report),))


def observe(db, observer, name, ok, now):
    row = db.execute("SELECT failures, first_bad, down_since, last_check FROM health WHERE name=?",
                     (name,)).fetchone()
    failures, first, incident, previous = row or (0, None, None, 0)
    if previous and now - previous > 180:
        failures, first = 0, None
    if ok:
        if incident is not None:
            enqueue(db, observer, f"{name}/{incident}/up", f"{name} svarar att",
                    f"Sett frå {observer}. Første stadfesta svar etter brotet.", incident, now)
        failures, first, incident = 0, None, None
    else:
        first = first or now
        failures += 1
        if failures >= 3 and incident is None:
            incident = first
            enqueue(db, observer, f"{name}/{first}/down", f"{name} utilgjengeleg",
                    f"Tre mislukka kontrollar frå {observer}.", first, now)
    db.execute("INSERT OR REPLACE INTO health VALUES (?,?,?,?,?)",
               (name, failures, first, incident, now))


def inventory(db, observer, targets, results, now):
    day = stamp(now)[:10]
    with db:
        last_round = db.execute("SELECT value FROM meta WHERE name='last_round'").fetchone()
        if last_round and now - int(last_round[0]) > 180:
            then = int(last_round[0])
            enqueue(db, observer, f"gap/{then}/{now}", f"Målegap på {observer}",
                    "Ingen fullførte kontrollrundar i dette intervallet.", then, now)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('last_round',?)", (str(now),))
        for target, ok in zip(targets, results, strict=True):
            observe(db, observer, target["name"], ok, now)
        last = db.execute("SELECT value FROM meta WHERE name='inventory_day'").fetchone()
        if last != (day,):
            lines = [f"{t['name']}: {'svarar' if ok else 'mislukka kontroll'}"
                     for t, ok in zip(targets, results)]
            enqueue(db, observer, f"inventory/{day}", f"Tenesteinventar frå {observer}",
                    "\n".join(lines), now, now)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('inventory_day',?)", (day,))


def deliver(db, token):
    """Drain in order. Require a Sprøyt receipt even on HTTP success."""
    for rowid, payload in db.execute("SELECT id,payload FROM outbox ORDER BY id LIMIT 16").fetchall():
        request = Request(REPORTS, data=payload.encode(), headers={
            "User-Agent": USER_AGENT, "Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with build_opener(NoRedirect).open(request, timeout=5) as response:
                receipt = json.loads(response.read(4096))
                if not isinstance(receipt, dict) or not (
                    receipt.get("accepted") == 1 or receipt.get("duplicate") == 1
                ):
                    raise ValueError("missing delivery receipt")
        except HTTPError as err:
            logging.warning("Sprøyt HTTP %d. Meldinga ligg i kø.", err.code)
            value = err.headers.get("Retry-After", "")
            if value.isdecimal():
                return max(60, int(value))
            if value:
                try:
                    return max(60, parsedate_to_datetime(value).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
            return 300 if err.code in (400, 401, 403) else 60
        except (OSError, ValueError, EOFError, HTTPException):
            logging.warning("Manglar leveringskvittering. Meldinga ligg i kø.")
            return 60
        with db:
            db.execute("DELETE FROM outbox WHERE id=?", (rowid,))
    return 0


def main():
    options = json.loads(Path("/data/options.json").read_text())
    observer, targets = options["observer"], options["targets"]
    names = [t["name"] for t in targets]
    if not 1 <= len(names) <= 32 or len(set(names)) != len(names):
        raise ValueError("Set opp 1–32 mål med ulike namn")
    for name in [observer, *names]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
            raise ValueError("Bruk korte ID-ar med små bokstavar, tal og bindestrek")
    for t in targets:
        url = urlsplit(t["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Bruk HTTPS utan legitimasjon i mål-URL")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    db = database("/data/site-watch.sqlite3")
    token = options.get("drift_token")
    if not token:
        logging.warning("Drift-token manglar. Rapportar blir lagra lokalt.")
    retry_at = 0
    last_status = None
    next_summary = 0
    previous_names = {row[0] for row in db.execute("SELECT name FROM health")}
    dashboard = Dashboard(observer, names, previous_names.difference(names))
    pending = None
    with ThreadPoolExecutor(max_workers=4) as pool, ThreadPoolExecutor(max_workers=1) as publisher:
        while True:
            started = time.monotonic()
            results = list(pool.map(probe, targets))
            inventory(db, observer, targets, results, int(time.time()))
            if pending is None or pending.done():
                down = {row[0] for row in db.execute("SELECT name FROM health WHERE down_since IS NOT NULL")}
                states = {"maal_" + n: ("ON" if n in down else "OFF") for n in names}
                states["tenestefeil"] = "ON" if "ON" in states.values() else "OFF"
                pending = publisher.submit(dashboard.publish, states)
            if token and started >= retry_at:
                delay = deliver(db, token)
                retry_at = time.monotonic() + delay
            queued = db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            status = (tuple(results), queued)
            if status != last_status or started >= next_summary:
                logging.info("Inventar %s: %d/%d svarar, %d i kø", observer, sum(results), len(names), queued)
                last_status = status
                next_summary = started + 3600
            time.sleep(max(1, 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
