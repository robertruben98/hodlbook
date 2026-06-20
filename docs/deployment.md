# Deployment

hodlbook ships as a slim, non-root container plus a `hodlbook` admin CLI. The
API never constructs a boto3 client itself: the client is resolved from the
environment, so the same image runs against DynamoDB Local or AWS.

## Environment variables

| Variable                     | Purpose                                              | Example                  |
| ---------------------------- | ---------------------------------------------------- | ------------------------ |
| `HODLBOOK_DYNAMODB_ENDPOINT` | DynamoDB endpoint URL. Omit to target real AWS.      | `http://dynamodb:8000`   |
| `AWS_REGION`                 | AWS region (falls back to `AWS_DEFAULT_REGION`).     | `us-east-1`              |
| `AWS_DEFAULT_REGION`         | Region fallback when `AWS_REGION` is unset.          | `us-east-1`              |
| `AWS_ACCESS_KEY_ID`          | AWS credentials. Use dummy values for DynamoDB Local.| `local`                  |
| `AWS_SECRET_ACCESS_KEY`      | AWS credentials. Use dummy values for DynamoDB Local.| `local`                  |

When no region is provided the CLI defaults to `us-east-1`.

## Local stack: `docker compose up`

`docker-compose.yml` defines two services:

* `dynamodb` — `amazon/dynamodb-local` on port `8000`.
* `api` — built from `Dockerfile`, served by `uvicorn --factory
  hodlbook.cli:app_factory`, published on host port `8080`, pointed at the
  `dynamodb` service via `HODLBOOK_DYNAMODB_ENDPOINT` with dummy local AWS creds.

```sh
docker compose up --build
```

The API comes up at <http://localhost:8080>. DynamoDB Local runs `-inMemory`,
so its data is discarded when the container stops.

## Table provisioning

The image does not auto-create the table. Provision it once after the stack is
up by running the CLI inside the `api` container (it inherits the same env):

```sh
docker compose exec api hodlbook create-table
```

`create-table` is idempotent — a second run reports the table already exists.

Optionally seed a demo portfolio with a couple of trades:

```sh
docker compose exec api hodlbook seed-demo --user-id demo --portfolio-id main --cash 100000
```

## CLI reference

The `hodlbook` console-script is installed with the package. Every subcommand
accepts `--region` and `--endpoint-url` (both fall back to the environment):

| Command          | Description                                                        |
| ---------------- | ------------------------------------------------------------------ |
| `create-table`   | Provision the single `hodlbook` DynamoDB table (idempotent).       |
| `seed-demo`      | Create a demo portfolio plus two trades (`bitcoin`, `ethereum`).   |
| `issue-api-key`  | Placeholder — the API-key admin flow lands with M9 (auth).         |
| `refresh-prices` | Placeholder — prices are refreshed lazily on read for now.         |

## Deploying to AWS

Run the same image with real AWS credentials and no `HODLBOOK_DYNAMODB_ENDPOINT`
(so boto3 targets the real DynamoDB service), then provision the table once:

```sh
AWS_REGION=us-east-1 hodlbook create-table
```
