"""Persistent SSE recovery never grants a fresh run or another principal access."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
from gateway.platforms.api_server_runs import _RunStream
from tests.gateway.test_api_server_runs import _create_runs_app, _make_adapter, _use_idempotency_db


def reserve(store, scope="owner", run_id="run_test", status="running"):
    store.reserve(scope, run_id, "fingerprint", run_id, {"run_id": run_id, "status": status},
                  owner_pid=999_999_999, owner_started=1)
    store.initialize_run_events(scope, run_id)


def test_fresh_process_scoped_replay_and_concurrent_sequence(tmp_path):
    path = tmp_path / "receipts.db"
    first = RunIdempotencyStore(str(path))
    reserve(first)
    second = RunIdempotencyStore(str(path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.append_run_event, "owner", "run_test",
                               {"run_id": "run_test", "event": "tool.started", "index": i})
                   for i, store in enumerate([first, second] * 10)]
        assert sorted(f.result() for f in futures) == list(range(20))
    assert first.run_event_snapshot("other", "run_test") is None
    with pytest.raises(ValueError):
        second.append_run_event("other", "run_test", {"run_id": "run_test"})
    first.seal_run_events("owner", "run_test")
    first.close()
    second.close()
    script = """
import json,sys
from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
s=RunIdempotencyStore(sys.argv[1])
print(json.dumps(s.run_event_snapshot('owner','run_test')))
s.close()
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True,
                            text=True, check=True, env=dict(os.environ))
    next_seq, closed, events = json.loads(result.stdout)
    assert closed and next_seq == 21
    assert [seq for seq, _ in events] == list(range(21))
    assert events[-1][1] is None
    assert path.stat().st_mode & 0o777 == 0o600


def test_bounded_tail_and_write_failure_never_publish(tmp_path):
    store = RunIdempotencyStore(str(tmp_path / "receipts.db"))
    reserve(store)
    stream = _RunStream(store, "owner", "run_test")
    for i in range(store.EVENT_LIMIT + 4):
        stream.put_nowait({"run_id": "run_test", "event": "tool.started", "index": i})
    recovered = _RunStream(store, "owner", "run_test")
    recovered.restore()
    _, replay = recovered.attach(-1)
    assert replay[0][0] == 4
    assert recovered.next_seq == store.EVENT_LIMIT + 4
    subscriber, _ = stream.attach(stream.next_seq - 1)
    store.close()
    with pytest.raises(Exception):
        stream.put_nowait({"run_id": "run_test", "event": "run.completed"})
    assert stream.failed and subscriber.qsize() == 1
    with pytest.raises(RuntimeError):
        stream.attach(-1)


@pytest.mark.asyncio
async def test_http_terminal_replay_after_restart_and_lost_start_ack(tmp_path):
    path = tmp_path / "receipts.db"
    adapter = _make_adapter("owner-key")
    _use_idempotency_db(adapter, path)
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": "done"}
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    headers = {"Authorization": "Bearer owner-key", "Idempotency-Key": "lost-ack"}
    async with TestClient(TestServer(_create_runs_app(adapter))) as client:
        with patch.object(adapter, "_create_agent", return_value=agent):
            response = await client.post("/v1/runs", json={"input": "hello"}, headers=headers)
            run_id = (await response.json())["run_id"]
            events_response = await client.get(f"/v1/runs/{run_id}/events", headers=headers)
            async with asyncio.timeout(10):
                original = await events_response.text()
            assert "run.completed" in original
    adapter._run_idempotency_store.close()
    restarted = _make_adapter("owner-key")
    _use_idempotency_db(restarted, path)
    async with TestClient(TestServer(_create_runs_app(restarted))) as client:
        with patch.object(restarted, "_create_agent") as create:
            replay = await client.post("/v1/runs", json={"input": "hello"}, headers=headers)
            assert (await replay.json())["run_id"] == run_id
            event_response = await client.get(f"/v1/runs/{run_id}/events", headers=headers)
            assert await event_response.text() == original
            partial = await client.get(f"/v1/runs/{run_id}/events",
                                       headers=headers | {"Last-Event-ID": "0"})
            text = await partial.text()
            assert "id: 0\n" not in text and "run.completed" not in text
            assert ": stream closed" in text
            create.assert_not_called()
    restarted._run_idempotency_store.close()
    other = _make_adapter("other-key")
    _use_idempotency_db(other, path)
    async with TestClient(TestServer(_create_runs_app(other))) as client:
        denied = await client.get(f"/v1/runs/{run_id}/events",
                                  headers={"Authorization": "Bearer other-key"})
        assert denied.status == 404
    other._run_idempotency_store.close()
    assert agent.run_conversation.call_count == 1


@pytest.mark.asyncio
async def test_dead_owner_approval_replay_is_evidence_only(tmp_path):
    path = tmp_path / "receipts.db"
    adapter = _make_adapter("owner-key")
    _use_idempotency_db(adapter, path)
    request = MagicMock(headers={})
    scope = adapter._run_idempotency_scope(request)
    store = adapter._run_idempotency_store
    reserve(store, scope, status="waiting_for_approval")
    store.append_run_event(scope, "run_test", {"run_id": "run_test", "event": "approval.request",
                                              "request_id": "approval-exact"})
    store.close()
    restarted = _make_adapter("owner-key")
    _use_idempotency_db(restarted, path)
    headers = {"Authorization": "Bearer owner-key"}
    async with TestClient(TestServer(_create_runs_app(restarted))) as client:
        for _ in range(2):
            response = await client.get("/v1/runs/run_test/events", headers=headers)
            text = await response.text()
            assert text.count("approval-exact") == 1 and text.count("run.interrupted") == 1
        approval = await client.post("/v1/runs/run_test/approval", headers=headers,
                                     json={"choice": "once", "request_id": "approval-exact"})
        assert approval.status == 409
        assert (await approval.json())["error"]["code"] == "approval_not_active"
        status = await client.get("/v1/runs/run_test", headers=headers)
        assert (await status.json())["status"] == "interrupted"
    restarted._run_idempotency_store.close()
