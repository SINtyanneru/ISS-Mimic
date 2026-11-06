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
    """

    def __init__(self, connection):
        self._conn = connection
        self._lock = threading.Lock()

    def execute(self, sql, params=()):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(sql.replace("?", "%s"), params)

    def executemany(self, sql, seq_of_params):
        with self._lock:
            with self._conn.cursor() as cur:
                cur.executemany(sql.replace("?", "%s"), seq_of_params)

    def close(self):
        self._conn.close()


def init_mysql_table(db):
    """Create the telemetry table in MySQL and seed it with the same rows
    the SQLite database is initialized with (existing rows are kept)."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS telemetry ("
        "`Label` VARCHAR(255) PRIMARY KEY, `Timestamp` TEXT, "
        "`Value` TEXT, `ID` VARCHAR(255), `dbID` INT)"
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
        db = MySQLTelemetryDB(conn)
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
    input("{0:-^80}\n".format("Press enter to disconnect & quit."))

def main():
    print('ISS Telemetry script active')
    log_info('ISS Telemetry script active')

    #loggerProvider = ConsoleLoggerProvider(ConsoleLogLevel.WARN)
    #LightstreamerClient.setLoggerProvider(loggerProvider)

    ls_client = LightstreamerClient("https://push.lightstreamer.com", "ISSLIVE")
    ls_client.connectionOptions.setSlowingEnabled(False)

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
