# Scaling the scheduling runtime

## What changed

When `DATABASE_URL` is configured, PostgreSQL owns the mutable state that was
previously trapped inside one Python process:

- current structured patient request;
- committed turns, extraction usage and diagnostic output;
- the active server-authored offer and its hidden values;
- rejected alternatives and the latest engine result;
- confirmed bookings and booking attempts;
- immutable catalog snapshots and published agent configurations.

The workers still cache the catalog in memory because it is small and immutable.
Every conversation records the catalog hash it used. Availability remains a mock
generator for this challenge; a real deployment replaces it with an EHR or practice
management adapter while preserving the same booking contract.

## Local setup

Install dependencies, start PostgreSQL, and copy the connection setting:

```bash
make install
make db-up
cp backend/.env.example backend/.env
```

Set this in `backend/.env`:

```dotenv
DATABASE_URL=postgresql://prosper:prosper_local_only@localhost:5433/prosper
```

Then create the schema and start the product:

```bash
make db-migrate
make db-status
make api
```

Run `make run` and `make frontend` in their own terminals as before. The `/health`
response must say `"storage": "postgresql"` and `"database": "connected"`.

## Supabase deployment

Create one Supabase project in the region closest to the Python voice/API workers.
In the project dashboard, click **Connect** and copy two URLs:

1. **Transaction pooler** for `DATABASE_URL` when the backend is serverless or
   auto-scaling. It normally uses port `6543`.
2. **Direct connection** for `MIGRATION_DATABASE_URL`. If the deployment cannot
   reach Supabase over IPv6, use the session pooler on port `5432` for migrations.

Configure every backend worker with the same secrets:

```dotenv
DATABASE_URL=<Supabase transaction-pooler URL>
MIGRATION_DATABASE_URL=<Supabase direct or session-pooler URL>
DATABASE_POOL_SIZE=10
TURN_CLAIM_SECONDS=120
CLINIC_ID=<stable clinic identifier>
```

Run migrations once as a release command before starting new workers:

```bash
backend/.venv/bin/python backend/manage_db.py migrate
```

The migration runner prefers `MIGRATION_DATABASE_URL`. If a Supabase direct IPv6
endpoint is unreachable from the machine, it falls back to `DATABASE_URL`. Using
the Supabase session pooler for `MIGRATION_DATABASE_URL` avoids that fallback.

Do not run migrations independently in every worker. Do not put either database
URL in the frontend: only the Python backend may connect to PostgreSQL. This design
does not use Supabase Auth, the Data API, or frontend database access.

Start with two backend workers behind a load balancer. They are stateless with
respect to conversations, so no sticky sessions are required for the scheduling
API. The WebRTC connection itself remains attached to one voice worker for the life
of that call, which is normal.

## Operational checks before increasing traffic

1. Set automated backups and point-in-time recovery on the managed database.
2. Add alerts for database connection saturation, API errors, turn latency and
   booking conflicts.
3. Keep the total of every worker's `DATABASE_POOL_SIZE` below the provider's
   connection limit. Prefer the provider's transaction pooler.
4. Load test conversations, not only HTTP health checks. Each turn takes a short
   database-backed processing claim, releases its connection during the LLM call,
   and commits only if it still owns that claim.
5. Send analytics and long-term evaluation work to a queue when it becomes heavy;
   do not add it to the live voice path.
6. Keep audio out of PostgreSQL. If recordings become a product requirement, put
   encrypted objects in object storage with explicit consent and retention rules.
7. Add staff authentication before exposing agent or catalog editing APIs. Patient
   callers can continue using opaque conversation IDs without accounts.

For a challenge/demo project, a normal Supabase project is sufficient. Before real
patient PHI is stored, use a separate production organization, sign Supabase's BAA,
enable the HIPAA add-on and High Compliance, then enable SSL enforcement, network
restrictions, connection logging, MFA, and point-in-time recovery. A project name
containing “healthcare” does not make the database HIPAA compliant by itself.

## What not to add yet

Redis, Kafka, Kubernetes, database partitioning and microservices are not required
to reach the next meaningful scale step. Add Redis only when measurements show a
need for hot ephemeral caching or rate limiting. PostgreSQL constraints—not a Redis
lock—remain the final protection against duplicate bookings.
