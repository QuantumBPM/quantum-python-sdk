# QuantumBPM Python SDK

Official Python SDK for the [QuantumBPM](https://quantumbpm.com) platform - DMN evaluation, BPMN process orchestration, and external job workers.

## Installation

```bash
pip install quantumbpm-sdk
```

Python 3.10+. Async-first; built on `asyncio`.

## What's in the box

| Module                    | Purpose                                                                       |
| ------------------------- | ----------------------------------------------------------------------------- |
| `quantumbpm.QuantumBPM`   | Top-level client exposing `.dmn`, `.bpmn`, plus `new_worker(...)`             |
| `quantumbpm.auth`         | `TokenProvider`, `ZitadelTokenProvider`, `StaticTokenProvider`                |
| `quantumbpm.dmn`          | DMN evaluation: stored definitions, ad-hoc XML, batch                         |
| `quantumbpm.bpmn`         | BPMN resources, instances, messaging, user tasks, processes                   |
| `quantumbpm.workers`      | External job worker runtime - long-poll, lock heartbeat, dispatch             |
| `quantumbpm.variables`    | `Vars` wrapper with typed accessors and FEEL-context conversion               |
| `quantumbpm.api[_client]` | OpenAPI-generated client. Reachable via `client.raw`, never hand-edited       |

## Quick start

```python
import asyncio
from quantumbpm import QuantumBPM, Vars, ZitadelTokenProvider

async def main():
    provider = ZitadelTokenProvider(
        "./service-account.json",         # Zitadel JSON Key file
        "https://auth.quantumbpm.com",    # issuer
        "your-zitadel-project-id",        # audience scope
    )

    async with QuantumBPM(
        base_url="https://api.quantumbpm.com",
        project_id="00000000-0000-0000-0000-000000000000",
        token_provider=provider,
    ) as client:
        result = await client.dmn.evaluate(
            "loan-eligibility",
            Vars().set("requestedAmt", 1000).set("creditScore", 720),
        )
        print(result)

asyncio.run(main())
```

The async context manager (`async with`) fetches a bearer token on entry, so an authentication problem shows up right away. Every API call also asks the token provider for a token first, so calling methods without the context manager works too, and a long-running worker keeps sending a valid token.

## Authentication

The SDK awaits `TokenProvider.get_token()` before every API call and sends the result as the bearer token. Two implementations ship out of the box.

### Zitadel service account

```python
from quantumbpm import ZitadelTokenProvider

provider = ZitadelTokenProvider(
    "./service-account.json",         # path to JSON Key file
    "https://auth.quantumbpm.com",    # issuer URL
    "your-zitadel-project-id",        # adds the audience scope
    ssl_ca_cert="/path/to/ca.crt",    # optional, for self-signed CAs
)
```

The provider caches the token in memory and fetches a new one once the cached token is within 60 seconds of expiry, so the lookup before each call is cheap.

### Static bearer token

For Enterprise deployments that issue long-lived API keys, or in tests where a token is acquired out of band:

```python
from quantumbpm import StaticTokenProvider

provider = StaticTokenProvider("eyJhbGciOi...")
```

### Bring your own

Implement the Protocol:

```python
from quantumbpm import TokenProvider

class MyProvider:
    async def get_token(self) -> str:
        return await fetch_my_token()

provider: TokenProvider = MyProvider()
```

`get_token` runs before every API call, so cache the token when fetching one is expensive.

## DMN evaluation

The `client.dmn` sub-client offers four async methods.

### Evaluate a stored definition

```python
result = await client.dmn.evaluate(
    "loan-eligibility",
    Vars().set("requestedAmt", 5000).set("creditScore", 720),
)
```

Returns `dict[str, EvaluationResult]` keyed by decision name. Each result has `value`, `hit_rules`, `error`, and `type`.

Pin a version, restrict the evaluated decisions, or attach decision services:

```python
result = await client.dmn.evaluate(
    "loan-eligibility",
    vars,
    version=3,
    decisions=["eligibility", "rate"],
)
```

### Evaluate by platform UUID

When you already hold a database-version pointer:

```python
result = await client.dmn.evaluate_by_id(definition_uuid, vars)
```

### Ad-hoc XML evaluation

For "evaluate while editing" flows that don't store the XML:

```python
result = await client.dmn.evaluate_design(
    dmn_xml,
    vars,
    additional_xmls=[imported_xml1, imported_xml2],
    decisions=["eligibility"],
)
```

### Batch ad-hoc evaluation

```python
rows = [
    Vars().set("requestedAmt", 1000),
    Vars().set("requestedAmt", 5000),
    Vars().set("requestedAmt", 25000),
]
batch = await client.dmn.evaluate_design_batch(dmn_xml, rows)
```

## BPMN processes

`client.bpmn` covers the full BPMN runtime surface.

### Deploy and start

```python
draft = await client.bpmn.create_resource("loan-process", bpmn_xml)
await client.bpmn.deploy_resource(draft.id)

# Re-fetch to get the populated process-definition list.
deployed = await client.bpmn.get_resource(draft.id)
process_def = deployed.processes[0]

workflow_id = await client.bpmn.start_instance(
    process_def.id,
    Vars().set("applicantID", "u-123").set("requestedAmt", 25000),
)
```

### Inspect runtime state

```python
state = await client.bpmn.get_instance(workflow_id)
print(state.status, state.active_scopes)

vars = await client.bpmn.get_instance_variables(workflow_id)

children = await client.bpmn.get_instance_children(workflow_id)
```

### Send messages and signals

```python
await client.bpmn.publish_message(
    "loan-approved",
    Vars().set("approvedAmt", 24000),
    correlation_keys={"applicantID": "u-123"},
    ttl="PT5M",
)

await client.bpmn.publish_signal("system-maintenance")
```

### User tasks

```python
page = await client.bpmn.list_user_tasks(
    assignee="alice@example.com",
    status="CREATED",
)

await client.bpmn.complete_user_task(execution_key, Vars().set("approved", True))

# Or fail with a BPMN error code (matches boundary error events):
await client.bpmn.throw_user_task_error(execution_key, "REVIEW_REJECTED")
```

## External job workers

Workers handle service tasks asynchronously. Register a handler per task type with a decorator, then call `run`. The runtime owns long-polling, lock heartbeats, dispatch, and outcome mapping.

### Minimal worker

```python
import asyncio
from quantumbpm import QuantumBPM, Vars, BpmnError
from quantumbpm.workers import Job

async def main():
    async with QuantumBPM(...) as client:
        worker = client.new_worker(client_id="billing-svc")
        stop = asyncio.Event()

        @worker.handler("send-email")
        async def handle(job: Job) -> Vars:
            recipient = job.vars.lookup("recipient")
            subject = job.vars.lookup("subject")
            await emailer.send(recipient, subject)
            return Vars().set("messageID", "msg-123")  # → Complete

        await worker.run(stop)  # blocks until stop is set

asyncio.run(main())
```

`run(stop)` resolves once the `asyncio.Event` is set and running handlers have finished, or the shutdown timeout has passed. See [Heartbeats, lost locks, and shutdown](#heartbeats-lost-locks-and-shutdown).

### Concurrency, polling, and locks

```python
@worker.handler(
    "send-email",
    max_jobs=10,            # up to 10 in flight per task type
    poll_timeout="5s",      # long-poll wait per request, at most 5s
    lock_duration="2m",     # exclusive lock per job
)
async def handle(job: Job) -> Vars:
    ...
```

Concurrency is per task type. Different task types run independently.

`poll_timeout` and `lock_duration` take Go duration strings such as `30s`, `1m30s`, `12.5s` or `500ms`. `handler` and `handle` raise `ValueError` for a value that does not parse. One poll request waits at most 5 seconds on the server, and a larger `poll_timeout` is capped to that. When a poll returns no job, the worker polls again right away.

The server clamps every lock to between 10 seconds and 5 minutes by default (`BPMN_WORKER_LEASE_MIN` and `BPMN_WORKER_LEASE_MAX`). Each polled job carries `job.raw.lock_expires_at`, the expiry of the lock the server granted. While the handler runs, the runtime heartbeats at half of the granted lock, or at half of `lock_duration` when that is shorter. A `lock_duration` of `11m` gets a 5 minute lock and a heartbeat every 2.5 minutes.

### Heartbeats, lost locks, and shutdown

A polled job is locked to this worker. Each heartbeat extends the lock by `lock_duration`, clamped the same way, and the server caps one attempt at 30 minutes by default (`BPMN_JOB_MAX_HOLD`). When the lock runs out, the server charges the job a retry and offers it again, or raises an incident when no retries are left. So abandoning a running job always costs a retry.

When a heartbeat answers that this worker no longer holds the job (HTTP 409 or 404), the runtime cancels the handler with `asyncio.CancelledError` and reports nothing. A complete or error report rejected with 409 is discarded by the server and logged as a warning. Let `CancelledError` propagate out of handlers.

Setting the `stop` event shuts the worker down gracefully:

- No new poll starts. A poll already in flight cannot be cancelled. It returns within 5 seconds, and jobs it returns before the shutdown timeout still run.
- Running handlers keep heartbeating, and their results are reported.
- Handlers still running `shutdown_timeout` seconds (default `20.0`) after `stop` is set are cancelled and not reported. Their locks run out on the server. Then `run` returns.

Wire `stop` to SIGTERM so a stop signal drains the worker:

```python
import asyncio
import signal
from quantumbpm import QuantumBPM, Vars
from quantumbpm.workers import Job

async def main():
    async with QuantumBPM(...) as client:
        worker = client.new_worker(client_id="billing-svc", shutdown_timeout=20.0)

        @worker.handler("send-email")
        async def handle(job: Job) -> Vars:
            ...

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop.set)
        loop.add_signal_handler(signal.SIGINT, stop.set)
        await worker.run(stop)

asyncio.run(main())
```

On Kubernetes, set `terminationGracePeriodSeconds` above the shutdown timeout, or the pod is killed mid-drain. A poll in flight at stop can also hold process exit for up to 5 seconds, so leave room for that too.

### Throwing typed BPMN errors

Raise a `BpmnError` to fail the job with a code that boundary error events on the originating service task can catch:

```python
@worker.handler("charge-card")
async def handle(job: Job) -> Vars:
    try:
        tx_id = await charge(job.vars.to_dict())
        return Vars().set("transactionID", tx_id)
    except InsufficientFundsError:
        raise BpmnError("INSUFFICIENT_FUNDS", Vars().set("availableBalance", 12.0))
```

Any other exception is reported as `WORKER_ERROR`, which the server treats as a retryable failure that decrements the job's retry budget. The exception message goes into the `error` variable, cut to `max_error_message_bytes` bytes of UTF-8 (a `new_worker` option, default 2048).

### Typed handlers (Pydantic)

Type-annotate the `Job` parameter with a Pydantic model - the runtime validates the job's input variables before invoking the handler. The decoded value lands in `job.typed`.

```python
from pydantic import BaseModel
from quantumbpm.workers import Job

class EmailJob(BaseModel):
    recipient: str
    subject: str

@worker.handler("send-email")
async def handle(job: Job[EmailJob]) -> Vars:
    await emailer.send(job.typed.recipient, job.typed.subject)
    return Vars().set("messageID", "msg-123")
```

Decode failures become `WORKER_ERROR` automatically, with the validation message in the variables.

## Variables

`Vars` is a thin wrapper around `dict[str, Any]` shared by DMN, BPMN, and workers.

### Construction

```python
v = Vars().set("amount", 100).set("name", "Alice")
v = Vars.from_dict({"amount": 100, "name": "Alice"})
```

### Typed access

```python
amount = v.get("amount", float)
flag   = v.get("approved", bool)

class Loan(BaseModel):
    requestedAmt: float
    approved: bool

loan = v.as_type(Loan)
```

`Vars.get(name, type_)` and `Vars.as_type(type_)` accept Pydantic models, dataclasses, and primitives - Pydantic's `TypeAdapter` does the validation.

## Escape hatch

The `client.raw` property exposes the underlying generated `ApiClient` for endpoints not yet wrapped (instance migration, modification, ad-hoc triggers, batch job complete/error, etc.):

```python
from quantumbpm.api.bpmn_api import BpmnApi

await client.authenticate()
api = BpmnApi(client.raw)
result = api.migrate_bpmn_instance(client.project_id, workflow_id, body)
```

Calls through `client.raw` send the token of the last SDK call. `await client.authenticate()` puts a current token on it first.

## License

MIT License - see [LICENSE](LICENSE) for details.
