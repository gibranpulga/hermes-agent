---
title: Plugin job handoff
description: Submit bounded background work from a hosted plugin API to its profile-scoped plugin worker.
---

# Plugin job handoff

Dashboard plugin routes execute in the profile's plugin host when
`plugins.isolation: host` is enabled. A regular plugin can register a named
handler there and its hosted dashboard API can submit bounded work to it.
This is useful for asynchronous curation and similar work that needs `ctx.llm`
without making the authenticated HTTP request wait for a model call.

```python
async def curate(payload):
    result = await ctx.llm.acomplete_structured(
        instructions="Return a relevance decision and concise summary.",
        input=payload,
        task="feed_curation",
    )
    return result.parsed

def register(ctx):
    ctx.register_auxiliary_task(
        "feed_curation", display_name="Feed curation",
        description="Assess Feed candidates.",
    )
    ctx.register_job_handler("curate", curate)
```

The dashboard API uses `request.app.state.hermes_jobs`:

```python
job = await request.app.state.hermes_jobs.submit("curate", validated_payload)
status = await request.app.state.hermes_jobs.get(job["id"])
```

The facade is bound to the plugin and profile that own the API. Clients cannot
select a profile, plugin, callable, or model in a submission. Payloads must be
JSON objects no larger than 64 KiB. At most four jobs may be queued or running
per plugin host. Results are JSON and limited to 64 KiB. Failures expose only
the exception class; full exception text must never be returned because it may
contain private prompts or provider data. Completed records expire after 24
hours. The current broker is in memory, so queued work and results are lost if
the plugin host restarts. Persisted queue storage is a separate follow-up for
workflows that require restart recovery.

Handlers should accept only the minimum generalized context and candidate
data needed for their task. Do not submit raw conversation or memory text,
credentials, or private reasoning. Return an explicitly shaped safe result.
The API route remains behind dashboard authentication and profile scope.
