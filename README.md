# Robe Service: Communication Email Gmail

[![License](https://img.shields.io/github/license/robe-land/robe-svc-communication-email-gmail)](LICENSE)
[![CI](https://github.com/robe-land/robe-svc-communication-email-gmail/actions/workflows/ci.yml/badge.svg)](https://github.com/robe-land/robe-svc-communication-email-gmail/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue)](pyproject.toml)
[![Top language](https://img.shields.io/github/languages/top/robe-land/robe-svc-communication-email-gmail)](https://github.com/robe-land/robe-svc-communication-email-gmail)
[![Language count](https://img.shields.io/github/languages/count/robe-land/robe-svc-communication-email-gmail)](https://github.com/robe-land/robe-svc-communication-email-gmail)
[![Last commit](https://img.shields.io/github/last-commit/robe-land/robe-svc-communication-email-gmail)](https://github.com/robe-land/robe-svc-communication-email-gmail/commits/develop)

This independently deployable service receives email retrieval requests and
publishes normalized email retrieval events. Its first implementation
increment uses synthetic Gmail-shaped fixtures and the local Pub/Sub
emulator; it does not access Gmail or require OAuth.

> Maintained by [Robe Land](https://github.com/robe-land).
>
> Original work is licensed under [LICENSE](LICENSE). Third-party
> materials retain their applicable licenses and copyright notices; see
> the notices accompanying those materials and the repository's
> third-party attribution index, where applicable.

## Development

Create a Python virtual environment and install the test extra:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

Run local verification with `./scripts/verify`. This checks source compilation
and the unit suite; the end-to-end test is a separate, explicitly invoked
local integration check.

## Repository composition and ownership

This repository is independently deployable and has no runtime dependency on
the Platform source tree or Mailflow. It connects to the Platform-owned
`robe-event-platform-dev` Docker network and reaches the Pub/Sub emulator at
`pubsub:8085`. The Platform's base `compose.yaml` contains only the emulator;
its demo publisher and Item/Audit consumers remain in the separate demo
composition.

The service also exposes `/.well-known/robe-service.json` for the local
Workbench. This implements the Platform-owned
[descriptor contract](https://github.com/robe-land/robe-platform-event/blob/develop/docs/contracts/workbench-service-descriptor.v1.schema.json)
using the service's existing Pub/Sub configuration. Its topic and
subscription relationships are declarations, not confirmation of live
resources; the Workbench discovers emulator resources and checks `/healthz`
separately. The health endpoint establishes HTTP liveness only.
Its optional synthetic template advertises the retrieval-request topic,
returns a fresh request with synthetic fixture identifiers, and exposes the
service-owned input schema from the installed contract. The Workbench may
validate and explicitly publish that request to the local emulator; neither
discovery nor template retrieval publishes a message. Broker acceptance
does not prove that the retrieval service processed it.

The service owns its retrieval-request topic, push subscription, retrieved
event topic, dead-letter topic, and dead-letter pull subscription. Its
`compose.yaml` starts the HTTP service and an idempotent resource provisioner.
The provisioner verifies that pre-existing subscriptions have the expected
topic and push endpoint rather than silently accepting incompatible resources.
It configures a 30-second push acknowledgment deadline, bounded retries, and a
dead-letter policy with a five-attempt limit. When a request subscription
already exists, the provisioner also verifies its acknowledgment deadline,
minimum and maximum retry backoff, dead-letter topic, and attempt limit.
These are configured policies, not verified runtime guarantees. The local
synthetic round trip manually republishes a request; it does not verify broker
redelivery timing, retry backoff, delivery-attempt counts, or dead-letter
forwarding. The emulator's support for delivery-attempt forwarding must not
be treated as proof of production IAM or dead-letter behavior.

## Service contracts

The service owns both JSON contracts in [`contracts/`](contracts/):

- `email-retrieval-requested.v1.schema.json` is the input envelope, with
  `communication.email.retrieval.requested` and a provider-neutral
  `email_identity` containing provider, opaque source-account ID, source
  message ID, and optional source thread ID.
- `email-retrieved.v1.schema.json` is the output envelope,
  `communication.email.retrieved`. Its payload retains that same
  provider-neutral identity alongside normalized sender, subject, receive
  time, body text, and normalization-warning fields.

Both envelopes follow the logical fields in the Platform's accepted
[messaging and reliability ADR](https://github.com/robe-land/robe-platform-event/blob/develop/docs/decisions/0002-messaging-and-reliability.md).
The email business identity is the combination of provider, source-account
ID, and source-message ID; the optional source-thread ID supports conversation
correlation. These fields are distinct from the retrieved event's `event_id`
and from workflow `correlation_id`/`causation_id`. A deterministic event ID is
derived from the request event ID and the email business identity. Redelivery
of the same request therefore republishes byte-identical content with the same
event ID, while separate requests for the same email retain the same business
identity and represent distinct retrieval events. This contract supports
future persistence keyed by email business identity without adding persistence
to this increment.

The synthetic slice copies the request's `occurred_at` into the retrieved
event. It is not a measured Gmail retrieval time; copying it keeps occurrence
time stable across redelivery. Durable occurrence-time semantics are deferred
to live retrieval design, and no persistence is introduced for this
limitation.

The Gmail-specific fixture lookup and normalizer are private service
implementation details. They prefer plain text in multipart alternatives,
exclude attached parts, avoid logging message payloads, and report stable
warning codes for unavailable or malformed content. The body remains
untrusted text and must be escaped by downstream display contexts. No live
Gmail access, OAuth, scheduling, persistence, or tagging is included.

## Local synthetic round trip

Prerequisites: Docker Compose, Python 3.12+, the Platform checkout, and its
`robe-event-platform-dev` network. From the Platform repository, start only
the emulator (this does not require the Platform `.env` or start demos):

```sh
docker compose up -d pubsub
```

From this repository, build and start the service; the provisioner runs after
the HTTP health check:

```sh
docker compose up --build -d
```

The service is available at `http://127.0.0.1:18080`. Run the separately
scoped end-to-end test from this repository's virtual environment:

```sh
.venv/bin/python -m pytest tests/integration -q
```

It publishes a synthetic retrieval request, receives the normalized event
through a temporary pull subscription, republishes the same request to model
at-least-once redelivery, verifies stable event identity and content, then
deletes only its temporary subscription. It does not publish to the
demonstration topic or access the database. Leave the local emulator and
service running for interactive review; stop them with their own Compose
projects only when the environment is no longer needed.
