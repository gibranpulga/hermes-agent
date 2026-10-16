"""Exact approval delivery receipts; an uncertain resolution cannot be retried."""

import json


def approval_delivery_response(outcome, receipt, error):
    from aiohttp import web
    if outcome == "resolved":
        return web.json_response(receipt["body"], status=receipt["status"])
    if outcome in {"conflict", "uncertain"}:
        return error("Approval delivery conflicts or requires reconciliation",
                     code="approval_delivery_" + outcome, status=409)
    return None


class RunApprovalReceiptMixin:
    def initialize_approval_receipts(self):
        self._conn.execute("""CREATE TABLE IF NOT EXISTS run_approval_receipts (
            run_id TEXT NOT NULL, request_id TEXT NOT NULL, choice TEXT NOT NULL,
            response_json TEXT, PRIMARY KEY(run_id,request_id))""")
        self._conn.commit()

    def approval_receipt(self, scope, run_id, request_id, choice, *, reserve=False):
        with self._immediate_txn():
            if not self._conn.execute("SELECT 1 FROM run_idempotency WHERE scope=? AND run_id=?",
                                      (scope, run_id)).fetchone():
                raise ValueError("Approval receipt owner is absent")
            row = self._conn.execute(
                "SELECT choice,response_json FROM run_approval_receipts WHERE run_id=? AND request_id=?",
                (run_id, request_id)).fetchone()
            if row is None and reserve:
                self._conn.execute("INSERT INTO run_approval_receipts VALUES(?,?,?,NULL)",
                                   (run_id, request_id, choice))
            self._conn.commit()
        if row is None:
            return ("reserved" if reserve else "missing"), None
        if row[0] != choice:
            return "conflict", None
        return ("uncertain", None) if row[1] is None else ("resolved", json.loads(row[1]))

    def settle_approval_receipt(self, scope, run_id, request_id, choice, response):
        encoded = json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._immediate_txn():
            changed = self._conn.execute(
                "UPDATE run_approval_receipts SET response_json=? WHERE run_id=? AND request_id=? "
                "AND choice=? AND response_json IS NULL AND EXISTS "
                "(SELECT 1 FROM run_idempotency WHERE scope=? AND run_id=?)",
                (encoded, run_id, request_id, choice, scope, run_id)).rowcount
            if changed != 1:
                raise ValueError("Approval receipt is not reserved")
            self._conn.commit()

    def prune_approval_receipts(self):
        self._conn.execute(
            "DELETE FROM run_approval_receipts WHERE run_id NOT IN (SELECT run_id FROM run_idempotency)")
