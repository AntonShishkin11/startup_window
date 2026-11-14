"""Local prototype of the Okno waitlist."""
import argparse
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

SERVICES = {"trim": "Стрижка", "shed": "Экспресс-линька"}
WEB = Path(__file__).with_name("web")
SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id),
    service TEXT NOT NULL, day TEXT NOT NULL, starts TEXT NOT NULL, ends TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY, service TEXT NOT NULL, day TEXT NOT NULL,
    starts TEXT NOT NULL, ends TEXT NOT NULL, confirmed_id INTEGER REFERENCES customers(id)
);
CREATE TABLE IF NOT EXISTS offers (
    slot_id INTEGER REFERENCES slots(id), customer_id INTEGER REFERENCES customers(id),
    accepted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (slot_id, customer_id)
);
"""


class InputError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def connect(db_path):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as db, db:
        db.executescript(SCHEMA)
        db.executemany("INSERT OR IGNORE INTO customers VALUES (?, ?)",
                       [(1, "Анна"), (2, "Игорь"), (3, "Мария"), (4, "Олег")])


def demo_day():
    return (date.today() + timedelta(days=1)).isoformat()


def interval(data):
    service, day, starts, ends = (data.get(k) for k in ("service", "day", "starts", "ends"))
    if not isinstance(service, str) or service not in SERVICES:
        raise InputError("Выберите услугу из списка.")
    try:
        parsed_day = date.fromisoformat(day)
        if parsed_day.isoformat() != day or parsed_day < date.today():
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise InputError("Укажите сегодняшнюю или будущую дату.") from exc
    for value in (starts, ends):
        if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise InputError("Укажите время начала и окончания.")
    if starts >= ends:
        raise InputError("Время окончания должно быть позже начала.")
    return service, day, starts, ends


def snapshot(db):
    def rows(query):
        return [dict(row) for row in db.execute(query)]

    return {
        "services": SERVICES, "today": date.today().isoformat(), "demoDay": demo_day(),
        "customers": rows("SELECT * FROM customers ORDER BY id"),
        "requests": rows("SELECT * FROM requests ORDER BY customer_id"),
        "slots": rows("SELECT * FROM slots ORDER BY id DESC"),
        "offers": rows("SELECT * FROM offers ORDER BY slot_id DESC, customer_id"),
    }


def change(db, action, data):
    if action == "demo":
        db.execute("DELETE FROM offers")
        db.execute("DELETE FROM slots")
        db.execute("DELETE FROM requests")
        db.executemany("INSERT INTO requests VALUES (?, ?, ?, ?, ?)", [
            (1, "trim", demo_day(), "15:30", "18:00"),
            (2, "trim", demo_day(), "16:00", "19:00"),
            (3, "shed", demo_day(), "15:00", "19:00"),
            (4, "trim", demo_day(), "18:00", "20:00"),
        ])
        return
    if action == "request":
        customer_id = data.get("customer_id")
        if type(customer_id) is not int or not db.execute(
            "SELECT 1 FROM customers WHERE id = ?", (customer_id,)
        ).fetchone():
            raise InputError("Выберите профиль клиента.")
        db.execute("INSERT OR REPLACE INTO requests VALUES (?, ?, ?, ?, ?)",
                   (customer_id, *interval(data)))
        return
    if action == "slot":
        service, day, starts, ends = interval(data)
        if db.execute(
            "SELECT 1 FROM slots WHERE service = ? AND day = ? AND starts = ? AND ends = ?",
            (service, day, starts, ends),
        ).fetchone():
            raise InputError("Такое окно уже добавлено.", 409)
        slot_id = db.execute(
            "INSERT INTO slots (service, day, starts, ends) VALUES (?, ?, ?, ?)",
            (service, day, starts, ends),
        ).lastrowid
        db.execute(
            """INSERT INTO offers (slot_id, customer_id)
               SELECT ?, customer_id FROM requests
               WHERE service = ? AND day = ? AND starts <= ? AND ends >= ?""",
            (slot_id, service, day, starts, ends),
        )
        return
    if action not in {"respond", "confirm"}:
        raise InputError("Действие не найдено.", 404)
    slot_id, customer_id = data.get("slot_id"), data.get("customer_id")
    if type(slot_id) is not int or type(customer_id) is not int:
        raise InputError("Выберите окно и клиента.")
    offer = db.execute(
        """SELECT o.accepted, s.confirmed_id FROM offers o JOIN slots s ON s.id = o.slot_id
           WHERE o.slot_id = ? AND o.customer_id = ?""", (slot_id, customer_id)
    ).fetchone()
    if not offer:
        raise InputError("У клиента нет предложения на это время.", 404)
    if offer["confirmed_id"] is not None:
        raise InputError("Это окно уже закрыто.", 409)
    if action == "respond":
        db.execute("UPDATE offers SET accepted = 1 WHERE slot_id = ? AND customer_id = ?",
                   (slot_id, customer_id))
        return
    if not offer["accepted"]:
        raise InputError("Сначала клиент должен откликнуться.", 409)
    db.execute("UPDATE slots SET confirmed_id = ? WHERE id = ?", (customer_id, slot_id))


def handler_for(db_path):
    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value, status=200):
            self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

        def do_GET(self):
            route = urlsplit(self.path).path
            assets = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
                      "/app.js": ("app.js", "text/javascript")}
            if route == "/api/state":
                with closing(connect(db_path)) as db, db:
                    db.execute("BEGIN")
                    state = snapshot(db)
                self.send_json(state)
            elif route in assets:
                filename, mime = assets[route]
                self.send_bytes((WEB / filename).read_bytes(), mime + "; charset=utf-8")
            else:
                self.send_json({"error": "Страница не найдена."}, 404)

        def do_POST(self):
            route = urlsplit(self.path).path
            if not route.startswith("/api/"):
                self.send_json({"error": "Страница не найдена."}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                    raise InputError("Ожидаются данные в формате JSON.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise InputError("Ожидается объект JSON.")
                with closing(connect(db_path)) as db, db:
                    # Serialize updates so two confirmations cannot reserve the same slot.
                    db.execute("BEGIN IMMEDIATE")
                    change(db, route.removeprefix("/api/"), data)
                    state = snapshot(db)
                self.send_json(state)
            except InputError as exc:
                self.send_json({"error": str(exc)}, exc.status)
            except (ValueError, UnicodeDecodeError):
                self.send_json({"error": "Некорректные данные запроса."}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", type=Path, default=Path("data/okno.sqlite3"))
    args = parser.parse_args()
    initialize(args.db)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(args.db)) as server:
        print(f"Окно запущено: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
