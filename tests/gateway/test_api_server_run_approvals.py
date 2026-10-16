"""Lost approval acknowledgements reuse receipts, never resolve a second action."""

from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.platforms.api_server_runs import _RunStream
from tests.gateway.test_api_server_runs import _create_runs_app, _make_adapter, _use_idempotency_db
from tools import approval as core
from tools import approval_gateway_wait


def prepare(tmp_path):
    adapter = _make_adapter("owner-key")
    path = tmp_path / "receipts.db"
    _use_idempotency_db(adapter, path)
    scope = adapter._run_idempotency_scope(MagicMock(headers={}))
    store = adapter._run_idempotency_store
    status = {"run_id": "run_approve", "status": "waiting_for_approval"}
    store.reserve(scope, "lost-ack", "fingerprint", "run_approve", status,
                  owner_pid=999_999_999, owner_started=1)
    store.initialize_run_events(scope, "run_approve")
    adapter._run_statuses["run_approve"] = status
    adapter._run_owners["run_approve"] = scope
    adapter._run_idempotency_ids.add("run_approve")
    adapter._run_approval_sessions["run_approve"] = "run_approve"
    adapter._run_streams["run_approve"] = _RunStream(store, scope, "run_approve")
    return adapter, path, scope


@pytest.mark.asyncio
async def test_exact_approval_receipt_survives_restart(tmp_path):
    adapter, path, _ = prepare(tmp_path)
    entry = approval_gateway_wait._ApprovalEntry({"request_id": "exact", "command": "touch result"})
    with core._lock:
        core._gateway_queues["run_approve"] = [entry]
    headers = {"Authorization": "Bearer owner-key"}
    body = {"choice": "once", "request_id": "exact"}
    try:
        async with TestClient(TestServer(_create_runs_app(adapter))) as client:
            missing = await client.post("/v1/runs/run_approve/approval", headers=headers, json={"choice": "once"})
            assert missing.status == 400
            first = await client.post("/v1/runs/run_approve/approval", headers=headers, json=body)
            original = await first.json()
            assert first.status == 200 and entry.result == "once"
            conflict = await client.post("/v1/runs/run_approve/approval", headers=headers,
                                         json=body | {"choice": "deny"})
            assert conflict.status == 409
            entry.result = None
            replay = await client.post("/v1/runs/run_approve/approval", headers=headers, json=body)
            assert await replay.json() == original
            assert entry.result is None
        adapter._run_idempotency_store.close()
        restarted = _make_adapter("owner-key")
        _use_idempotency_db(restarted, path)
        async with TestClient(TestServer(_create_runs_app(restarted))) as client:
            replay = await client.post("/v1/runs/run_approve/approval", headers=headers, json=body)
            assert replay.status == 200 and await replay.json() == original
            assert entry.result is None
        restarted._run_idempotency_store.close()
    finally:
        core.unregister_gateway_notify("run_approve")


@pytest.mark.asyncio
async def test_resolution_before_receipt_failure_is_held_uncertain(tmp_path):
    adapter, _, scope = prepare(tmp_path)
    headers = {"Authorization": "Bearer owner-key"}
    body = {"choice": "once", "request_id": "exact"}
    entry = approval_gateway_wait._ApprovalEntry({"request_id": "exact", "command": "touch result"})
    with core._lock:
        core._gateway_queues["run_approve"] = [entry]
    try:
        async with TestClient(TestServer(_create_runs_app(adapter))) as client:
            with patch.object(adapter._run_idempotency_store, "settle_approval_receipt", side_effect=OSError("disk")):
                failed = await client.post("/v1/runs/run_approve/approval", headers=headers, json=body)
                assert failed.status == 500 and entry.result == "once"
            entry.result = None
            replay = await client.post("/v1/runs/run_approve/approval", headers=headers, json=body)
            assert replay.status == 409
            assert (await replay.json())["error"]["code"] == "approval_delivery_uncertain"
            assert entry.result is None
        with pytest.raises(ValueError):
            adapter._run_idempotency_store.approval_receipt("other", "run_approve", "exact", "once")
        assert adapter._run_idempotency_store.approval_receipt(scope, "run_approve", "exact", "once")[0] == "uncertain"
    finally:
        core.unregister_gateway_notify("run_approve")
        adapter._run_idempotency_store.close()
