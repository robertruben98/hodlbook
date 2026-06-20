# Deployment

hodlbook runs against any DynamoDB endpoint via an injected boto3 client — there
is no hidden global state and the app never constructs boto3 itself. The same
[`create_app`](api-reference.md) factory serves DynamoDB Local, AWS, and `moto`
tests; only the injected client changes.

## Install

```bash
pip install -e .
```

For the HTTP server you will also want an ASGI server such as `uvicorn`.

## Provision the table

The table is provisioned once via [`create_table`](api-reference.md). In
production prefer infrastructure-as-code (CDK / Terraform / CloudFormation) that
creates the same single table with its `GSI1` and `GSI2` indexes; use
`create_table` for local development and tests.

```python
import boto3
from hodlbook import create_table

create_table(boto3.client("dynamodb"))   # one-time
```

The table name is exported as [`TABLE_NAME`](api-reference.md).

## Run the API

[`create_app`](api-reference.md) returns an ASGI application. Inject the client
configured for your target environment:

```python
# app.py
import boto3
from hodlbook import create_app

app = create_app(boto3.client("dynamodb"))
```

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

## DynamoDB Local

For local development, point the injected client at a DynamoDB Local container:

```python
import boto3
client = boto3.client(
    "dynamodb",
    endpoint_url="http://localhost:8000",
    region_name="us-east-1",
    aws_access_key_id="local",
    aws_secret_access_key="local",
)
```

## AWS

In AWS, construct the client with your normal credential chain and region — the
rest of the wiring is identical:

```python
import boto3
client = boto3.client("dynamodb", region_name="us-east-1")
```

Grant the role running the app least-privilege access to the single hodlbook
table and its indexes (read/write item operations plus `TransactWriteItems` for
atomic trades, and `Query` on the GSIs).

## Observability

Wire pydynantic's operation hook to trace DynamoDB calls. Pass
[`logging_hook`](api-reference.md) (or your own) when building the table so
each operation is logged with latency and consumed-capacity context — without
hodlbook forcing a logging dependency on callers.
