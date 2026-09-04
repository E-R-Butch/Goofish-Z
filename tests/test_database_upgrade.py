import json
import sqlite3
from contextlib import closing

from support import OfflineCase, fixture
from goofish_z.db import WatchDB


class UpgradeTest(OfflineCase):
    def test_existing_database_records_survive_additive_migration(self):
        path = self.root / "legacy.db"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript('''
                CREATE TABLE watch_items (id INTEGER PRIMARY KEY,keyword TEXT,max_price REAL,min_price REAL,
                    enabled INTEGER DEFAULT 1,created_at INTEGER,last_check_at INTEGER);
                CREATE TABLE price_history (id INTEGER PRIMARY KEY,watch_id INTEGER,item_id TEXT,title TEXT,
                    price REAL,location TEXT,url TEXT,raw TEXT,checked_at INTEGER);
                CREATE TABLE alerts (id INTEGER PRIMARY KEY,watch_id INTEGER,item_id TEXT,title TEXT,
                    price REAL,reason TEXT,created_at INTEGER);
                INSERT INTO watch_items VALUES (1,'synthetic',100,NULL,1,1000,1000);
                INSERT INTO price_history VALUES (1,1,'0000000000000','synthetic',80,'','','{}',1000);
                INSERT INTO alerts VALUES (1,1,'0000000000000','synthetic',80,'synthetic reason',1000);
            ''')
        db = WatchDB(path)
        self.assertEqual(db.recent_alerts()[0]["reason"], "synthetic reason")
        self.assertEqual(db.record_poll(1,[fixture()]), [])
        self.assertEqual(len(db.recent_alerts()),1)
        self.assertEqual(len(db.history(1)),2)
        self.assertTrue(db.mark_alert_read(1))
