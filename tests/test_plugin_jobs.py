import asyncio

import pytest

from hermes_cli.plugin_jobs import PluginJobBroker, PluginJobs


def test_plugin_job_handoff_is_bounded_and_returns_only_handler_result():
    async def run():
        loop = asyncio.get_running_loop()
        broker = PluginJobBroker("feed", loop)
        broker.register("curate", lambda payload: {"accepted": payload["title"]})
        api = PluginJobs(broker)
        submitted = await api.submit("curate", {"title": "public release"})
        for _ in range(20):
            result = await api.get(submitted["id"])
            if result["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0)
        assert result == {
            "id": submitted["id"], "status": "completed",
            "result": {"accepted": "public release"}, "error": None,
        }
        with pytest.raises(LookupError):
            await api.submit("other_plugin_handler", {})
        with pytest.raises(ValueError, match="64 KiB"):
            await api.submit("curate", {"data": "x" * (64 * 1024)})
        broker.close()

    asyncio.run(run())


def test_plugin_job_failures_do_not_expose_exception_text():
    async def run():
        broker = PluginJobBroker("feed", asyncio.get_running_loop())

        async def fail(_payload):
            raise RuntimeError("private prompt and secret")

        broker.register("curate", fail)
        job = await broker.submit("curate", {})
        for _ in range(20):
            result = await broker.get(job["id"])
            if result["status"] == "failed":
                break
            await asyncio.sleep(0)
        assert result["error"] == "RuntimeError"
        assert "private prompt" not in str(result)
        broker.close()

    asyncio.run(run())
