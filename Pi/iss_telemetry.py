#!/usr/bin/env python3

from datetime import datetime, timedelta, timezone
import configparser
import pathlib
import sqlite3
import threading
import os.path as op #use for getting mimic directory
from utils.logger import log_info, log_error

from lightstreamer.client import LightstreamerClient, ConsoleLoggerProvider, ConsoleLogLevel, Subscription

from telemetry_ids import IDENTIFIERS

mimic_directory = op.abspath(op.join(__file__, op.pardir, op.pardir, op.pardir))

log_info("Initialized Telemetry Log")

FIVE_SECONDS_AS_HOURS = 5.0 / 3600  # 3600 seconds/hr

CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "config.ini"


class MySQLTelemetryDB:
    """sqlite3.Connection-like wrapper around a PyMySQL connection.

    The listeners issue sqlite-style statements through ``db.execute(sql,
    params)`` using ``?`` placeholders. This wrapper translates the
    placeholders to PyMySQL's ``%s`` so the listener code stays unchanged.
    A lock serializes access because PyMySQL connections are not safe to
    share between the Lightstreamer callback threads.

    In addition, every value update is appended to the
    ``telemetry_history`` table so the most recent ``history_per_label``
    values of each Label stay available in MySQL (0 disables history).

    Value updates are NOT written on the caller's thread. The Lightstreamer
    listeners run on the client's single event thread, so a blocking
    database write there delays every following update and lets the
    client's internal queue (and with it, memory usage) grow whenever the
    database cannot keep up. Instead, ``execute`` only keeps the newest
    pending update per telemetry row in a small buffer, and a dedicated
    writer thread performs the actual writes. While the database is busy,
    stale values for the same row are overwritten in the buffer before
    they are ever written, so the write load shrinks instead of piling up.
    Other statements (e.g. the CREATE TABLE calls during setup) still run
    synchronously on the caller's thread.
    """

    # Run the history pruning DELETE only once per this many inserted
    # history rows of the same Label. Between prunes a Label can hold up
    # to history_per_label + PRUNE_EVERY - 1 rows.
    PRUNE_EVERY = 20

    def __init__(self, connection, history_per_label=100):
        self._conn = connection
        self._lock = threading.Lock()
        self._history_per_label = history_per_label
        self._label_by_id = {}
        self._prune_counter = {}
        self._pending = {}
        self._pending_cond = threading.Condition()
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._writer_loop,
                                        name="MySQLTelemetryWriter",
                                        daemon=True)
        self._writer.start()

    @staticmethod
    def _update_key(sql, params):
        """Buffer key for a telemetry value update, or None.

        Only the listeners' value updates (``UPDATE telemetry SET Value
        ... WHERE ID = ?`` / ``... WHERE Label = ?``) are buffered; the
        key identifies the target row so a newer update replaces a still
        pending older one for the same row.
        """
        if not params:
            return None
        normalized = " ".join(sql.split())
        if not normalized.startswith("UPDATE telemetry SET Value"):
            return None
        if normalized.endswith("WHERE ID = ?"):
            return ("ID", params[-1])
        if normalized.endswith("WHERE Label = ?"):
            return ("Label", params[-1])
        return None

    def execute(self, sql, params=()):
        key = self._update_key(sql, params)
        if key is None:
            self._execute_now(sql, params)
            return
        with self._pending_cond:
            self._pending[key] = (sql, params)
            self._pending_cond.notify()

    def _execute_now(self, sql, params=()):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(sql.replace("?", "%s"), params)
            self._record_history(sql, params)

    def _writer_loop(self):
        while True:
            with self._pending_cond:
                while not self._pending and not self._stop.is_set():
                    self._pending_cond.wait(timeout=1.0)
                batch = self._pending
                self._pending = {}
            if not batch:
                if self._stop.is_set():
                    return
                continue
            for sql, params in batch.values():
                try:
                    self._execute_now(sql, params)
                except Exception as ex:
                    log_error(f"MySQL telemetry write failed: {ex}")

    def _label_for_id(self, item_id):
        """Resolve the telemetry Label for a Lightstreamer item ID."""
        if item_id not in self._label_by_id:
            with self._conn.cursor() as cur:
                cur.execute("SELECT `Label` FROM telemetry WHERE `ID` = %s",
                            (item_id,))
                row = cur.fetchone()
            self._label_by_id[item_id] = row[0] if row else None
        return self._label_by_id[item_id]

    def _record_history(self, sql, params):
        """Append a value update to telemetry_history and prune old rows.

        Only the listeners' value updates (``UPDATE telemetry SET Value
        ...``) are recorded. The caller must hold ``self._lock``. Existing
        tables and rows are never modified or deleted here, except for the
        history rows beyond the newest ``history_per_label`` per Label.
        """
        if self._history_per_label <= 0 or not params:
            return
        normalized = " ".join(sql.split())
        if not normalized.startswith("UPDATE telemetry SET Value"):
            return
        value = params[0]
        timestamp = params[1] if "Timestamp = ?" in normalized else None
        if normalized.endswith("WHERE ID = ?"):
            label = self._label_for_id(params[-1])
        elif normalized.endswith("WHERE Label = ?"):
            label = params[-1]
        else:
            return
        if label is None:
            return
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO telemetry_history (`Label`, `Timestamp`, `Value`) "
                "VALUES (%s, %s, %s)", (label, timestamp, value))
            # The pruning DELETE is the most expensive statement of the
            # three issued per update, so it only runs every PRUNE_EVERY
            # inserted rows of this Label instead of on every update.
            count = self._prune_counter.get(label, 0) + 1
            if count >= self.PRUNE_EVERY:
                self._prune_counter[label] = 0
                cur.execute(
                    "DELETE FROM telemetry_history WHERE `Label` = %s AND `seq` NOT IN "
                    "(SELECT `seq` FROM (SELECT `seq` FROM telemetry_history "
                    "WHERE `Label` = %s ORDER BY `seq` DESC LIMIT %s) AS keep_rows)",
                    (label, label, self._history_per_label))
            else:
                self._prune_counter[label] = count

    def executemany(self, sql, seq_of_params):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.executemany(sql.replace("?", "%s"), seq_of_params)

    def close(self):
        # Flush the updates that are still buffered, then close.
        self._stop.set()
        with self._pending_cond:
            self._pending_cond.notify_all()
        self._writer.join(timeout=30)
        self._conn.close()


def init_mysql_table(db):
    """Create the telemetry tables in MySQL and seed the telemetry table
    with the same rows the SQLite database is initialized with (existing
    tables and rows are kept; nothing is dropped or truncated)."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS telemetry ("
        "`Label` VARCHAR(255) PRIMARY KEY, `Timestamp` TEXT, "
        "`Value` TEXT, `ID` VARCHAR(255), `dbID` INT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS telemetry_history ("
        "`seq` BIGINT NOT NULL AUTO_INCREMENT, "
        "`Label` VARCHAR(255) NOT NULL, `Timestamp` TEXT, `Value` TEXT, "
        "PRIMARY KEY (`seq`), KEY `idx_label_seq` (`Label`, `seq`))"
    )
    from database_initialize import telemetry_data
    db.executemany("INSERT IGNORE INTO telemetry VALUES(?, ?, ?, ?, ?)",
                   telemetry_data)
    log_info(f"MySQL telemetry table ready ({len(telemetry_data)} seed rows)")


def open_database():
    """Open the telemetry database.

    If config.ini has a [mysql] section with ``enabled = true``, telemetry
    is written to MySQL. Otherwise the original local SQLite database is
    used and behaviour is unchanged.
    """
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    if config.getboolean("mysql", "enabled", fallback=False):
        import pymysql
        conn = pymysql.connect(
            host=config.get("mysql", "host", fallback="127.0.0.1"),
            port=config.getint("mysql", "port", fallback=3306),
            user=config.get("mysql", "user", fallback="mimic"),
            password=config.get("mysql", "password", fallback=""),
            database=config.get("mysql", "database", fallback="mimic"),
            autocommit=True,
            charset="utf8mb4",
        )
        db = MySQLTelemetryDB(
            conn,
            history_per_label=config.getint("mysql", "history_per_label",
                                            fallback=100))
        init_mysql_table(db)
        log_info("Telemetry database: MySQL "
                 f"{config.get('mysql', 'host', fallback='127.0.0.1')}/"
                 f"{config.get('mysql', 'database', fallback='mimic')}")
        return db

    db_path = pathlib.Path('/dev/shm/iss_telemetry.db')
    if not db_path.exists():
        db_path = pathlib.Path.home() / '.mimic_data' / 'iss_telemetry.db'
    return sqlite3.connect(str(db_path), isolation_level=None,
                           check_same_thread=False)

class LSSubscriptionListener:
    """Though SubscriptionListener appears to be in the library,
    it doesn't seem to have any methods, so extending that isn't useful...?"""
    def onItemUpdate(self, update):
        pass

    def onListenStart(self, subscription):  # Added missing argument
        pass

    def onClearSnapshot(self, itemName, itemPos):
        pass

    def onCommandSecondLevelItemLostUpdates(self, lostUpdates, key):
        pass

    def onCommandSecondLevelSubscriptionError(self, code, message, key):
        pass

    def onEndOfSnapshot(self, itemName, itemPos):
        pass

    def onItemLostUpdates(self, itemName, itemPos, lostUpdates):
        pass

    def onListenEnd(self, subscription):
        pass

    def onSubscription(self):
        pass

    def onSubscriptionError(self, code, message):
        pass

    def onUnsubscription(self):
        pass

    def onRealMaxFrequency(self, frequency):
        pass

class StatusUpdater:  # extend lightstreamer_client.ClientListener? or custom?
    def __init__(self, db):
        self.db = db

    def onStatusChange(self, newStatus):
        log_info(f"Client status: {newStatus}")
        self.db.execute("UPDATE telemetry SET Value = ? WHERE Label = ?", (newStatus, "ClientStatus"))


class MainListener(LSSubscriptionListener):
    def __init__(self, db):
        super().__init__()
        self.db = db

    def onSubscription(self):
        log_info("Subscribed!")
        self.db.execute("UPDATE telemetry SET Value = ? WHERE Label = ?", ("Subscribed", "Lightstreamer"))

    def onUnsubscription(self):
        log_info("Unsubscribed!")
        self.db.execute("UPDATE telemetry SET Value = ? WHERE Label = ?", ("Unsubscribed", "Lightstreamer"))

    def onItemUpdate(self, update):
        self.db.execute("UPDATE telemetry SET Value = ?, Timestamp = ? WHERE ID = ?",
                        (update.getValue("Value"), update.getValue("TimeStamp"), update.getItemName()))


class TimeListener(LSSubscriptionListener):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.AOSnum = 0
        now = datetime.now(timezone.utc)
        new_years_eve = datetime(now.year - 1, 12, 31, tzinfo=now.tzinfo)
        diff = now - new_years_eve
        self.start_time = diff / timedelta(hours=1)  # convert to fractional hours

    def onItemUpdate(self, update):
        status = update.getValue("Status.Class")
        AOStimestamp = float(update.getValue("TimeStamp"))
        difference = self.start_time - AOStimestamp

        if status == "24":
            if difference > FIVE_SECONDS_AS_HOURS:
                log_info(f"Signal Error!     @ {AOStimestamp}")
                self.AOSnum = 2
            else:
                if self.AOSnum != 1:
                    log_info(f"Connected to the ISS!     @ {AOStimestamp}")
                self.AOSnum = 1
        else:
            if difference > FIVE_SECONDS_AS_HOURS:
                log_info(f"Signal Error!     @ {AOStimestamp}")
                self.AOSnum = 2
            else:
                log_info(f"Signal Lost!     @ {AOStimestamp}")
                self.AOSnum = 0

        self.db.execute("UPDATE telemetry SET Value = ?, Timestamp = ? WHERE Label = ?",
                        (self.AOSnum, AOStimestamp, "aos"))


def wait_for_input():
    try:
        input("{0:-^80}\n".format("Press enter to disconnect & quit."))
    except EOFError:
        # No interactive stdin (e.g. when running as a systemd service):
        # keep running until the process is stopped.
        log_info("No interactive stdin; running until stopped.")
        threading.Event().wait()

def main():
    print('ISS Telemetry script active')
    log_info('ISS Telemetry script active')

    #loggerProvider = ConsoleLoggerProvider(ConsoleLogLevel.WARN)
    #LightstreamerClient.setLoggerProvider(loggerProvider)

    ls_client = LightstreamerClient("https://push.lightstreamer.com", "ISSLIVE")
    # Keep the slowing protection enabled: if this client cannot keep up
    # with the stream, the client falls back to polling and the server
    # merges obsolete updates instead of the client queueing them all.
    ls_client.connectionOptions.setSlowingEnabled(True)

    main_sub = Subscription(mode="MERGE",
                            items=IDENTIFIERS,
                            fields=["TimeStamp", "Value"])
    time_sub = Subscription(mode="MERGE",
                            items=["TIME_000001"],
                            fields=["TimeStamp", "Value", "Status.Class", "Status.Indicator"])

    db = open_database()
    main_sub.addListener(MainListener(db))
    time_sub.addListener(TimeListener(db))

    ls_client.subscribe(main_sub)
    ls_client.subscribe(time_sub)

    try:
        ls_client.connect()
        print(ls_client.getStatus())
        log_info(ls_client.getStatus())
        wait_for_input()
    finally:
        ls_client.unsubscribe(main_sub)
        ls_client.unsubscribe(time_sub)
        ls_client.disconnect()
        db.close()
        log_info("DB closed - app exit")


if __name__ == '__main__':
    main()
