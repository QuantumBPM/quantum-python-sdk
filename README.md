# QuantumBPM Python SDK

Official Python SDK for the [QuantumBPM](https://quantumbpm.com) platform — DMN evaluation, BPMN process orchestration, and external job workers.

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
| `quantumbpm.workers`      | External job worker runtime — long-poll, lock heartbeat, dispatch             |
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

The async context manager (`async with`) acquires a fresh bearer token on entry. Skipping the context manager and calling methods directly works too — the SDK refreshes tokens on demand.

## Authentication

The `TokenProvider` Protocol returns a bearer token on each request. Two implementations ship out of the box.

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

The provider caches tokens in-memory until shortly before expiry.

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

`run(stop)` resolves when the `asyncio.Event` is set, after in-flight handlers settle.

### Concurrency, polling, and locks

```python
@worker.handler(
    "send-email",
    max_jobs=10,            # up to 10 in flight per task type
    poll_timeout="45s",     # long-poll wait
    lock_duration="2m",     # exclusive lock per job
)
async def handle(job: Job) -> Vars:
    ...
```

Concurrency is per task type. Different task types run independently. The runtime auto-renews the lock at half the lock-duration interval while the handler runs.

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

Any other exception is reported as `WORKER_ERROR`, which the server treats as a retryable failure that decrements the job's retry budget.

### Typed handlers (Pydantic)

Type-annotate the `Job` parameter with a Pydantic model — the runtime validates the job's input variables before invoking the handler. The decoded value lands in `job.typed`.

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

`Vars.get(name, type_)` and `Vars.as_type(type_)` accept Pydantic models, dataclasses, and primitives — Pydantic's `TypeAdapter` does the validation.

## Escape hatch

The `client.raw` property exposes the underlying generated `ApiClient` for endpoints not yet wrapped (instance migration, modification, ad-hoc triggers, batch job complete/error, etc.):

```python
from quantumbpm.api.bpmn_api import BpmnApi

api = BpmnApi(client.raw)
result = api.migrate_bpmn_instance(client.project_id, workflow_id, body)
```

## License

MIT License — see [LICENSE](LICENSE) for details.
