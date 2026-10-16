"""Bounded run event journals in the existing profile-scoped receipt database."""

import json


class RunEventJournalMixin:
    """Uses the receipt store's connection, lock and retention, never another scheduler."""

    EVENT_LIMIT = 1000
    EVENT_BYTES_LIMIT = 256 * 1024

    def initialize_events(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS run_event_heads (
                run_id TEXT PRIMARY KEY, next_seq INTEGER NOT NULL DEFAULT 0,
                closed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS run_events (
                run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT,
                PRIMARY KEY (run_id, seq));
        """)

    def initialize_run_events(self, scope, run_id):
        with self._immediate_txn():
            if not self._conn.execute(
                "SELECT 1 FROM run_idempotency WHERE scope=? AND run_id=?", (scope, run_id)
            ).fetchone():
                raise ValueError("Run event owner is absent")
            self._conn.execute("INSERT OR IGNORE INTO run_event_heads(run_id) VALUES(?)", (run_id,))
            self._conn.commit()

    def append_run_event(self, scope, run_id, event):
        encoded = None if event is None else json.dumps(event, separators=(",", ":"), allow_nan=False)
        if event is not None and event.get("run_id") != run_id:
            raise ValueError("Run event identity mismatch")
        if encoded is not None and len(encoded.encode()) > self.EVENT_BYTES_LIMIT:
            raise ValueError("Run event exceeds journal limit")
        with self._immediate_txn():
            row = self._conn.execute(
                "SELECT h.next_seq,h.closed FROM run_event_heads h JOIN run_idempotency r "
                "ON r.run_id=h.run_id WHERE r.scope=? AND h.run_id=?", (scope, run_id)
            ).fetchone()
            if row is None or row[1]:
                raise ValueError("Run event journal is absent or closed")
            seq = row[0]
            self._conn.execute("INSERT INTO run_events VALUES(?,?,?)", (run_id, seq, encoded))
            self._conn.execute(
                "UPDATE run_event_heads SET next_seq=?,closed=? WHERE run_id=?",
                (seq + 1, int(event is None), run_id))
            self._conn.execute(
                "DELETE FROM run_events WHERE run_id=? AND seq<?", (run_id, seq + 1 - self.EVENT_LIMIT))
            self._conn.commit()
        self._tighten_permissions()
        return seq

    def run_event_snapshot(self, scope, run_id):
        # One read transaction prevents a concurrent append from splitting head and tail.
        with self._immediate_txn():
            row = self._conn.execute(
                "SELECT h.next_seq,h.closed FROM run_event_heads h JOIN run_idempotency r "
                "ON r.run_id=h.run_id WHERE r.scope=? AND h.run_id=?", (scope, run_id)
            ).fetchone()
            events = self._conn.execute(
                "SELECT seq,event_json FROM run_events WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall() if row else []
            self._conn.commit()
        if row is None:
            return None
        return row[0], bool(row[1]), [(seq, None if data is None else json.loads(data)) for seq, data in events]

    def seal_run_events(self, scope, run_id, event=None):
        """Reconcile a dead owner once; never make an interrupted turn executable."""
        with self._immediate_txn():
            row = self._conn.execute(
                "SELECT h.next_seq,h.closed FROM run_event_heads h JOIN run_idempotency r "
                "ON r.run_id=h.run_id WHERE r.scope=? AND h.run_id=?", (scope, run_id)
            ).fetchone()
            if row and not row[1]:
                seq = row[0]
                if event is not None:
                    if event.get("run_id") != run_id:
                        raise ValueError("Run event identity mismatch")
                    self._conn.execute("INSERT INTO run_events VALUES(?,?,?)",
                                       (run_id, seq, json.dumps(event, allow_nan=False)))
                    seq += 1
                self._conn.execute("INSERT INTO run_events VALUES(?,?,NULL)", (run_id, seq))
                self._conn.execute("UPDATE run_event_heads SET next_seq=?,closed=1 WHERE run_id=?",
                                   (seq + 1, run_id))
                self._conn.execute("DELETE FROM run_events WHERE run_id=? AND seq<?",
                                   (run_id, seq + 1 - self.EVENT_LIMIT))
            self._conn.commit()

    def prune_run_events(self):
        # Called within receipt pruning's transaction. Journals share its recovery horizon.
        self._conn.execute("DELETE FROM run_events WHERE run_id NOT IN (SELECT run_id FROM run_idempotency)")
        self._conn.execute("DELETE FROM run_event_heads WHERE run_id NOT IN (SELECT run_id FROM run_idempotency)")
