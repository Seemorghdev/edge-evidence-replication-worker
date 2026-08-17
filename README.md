# replication-worker

`replication-worker` is the standalone, bounded replication-convergence product
exported from `Seemorghdev/edge-evidence-platform` for Project03.

This repository is **generated**, not independently authored. Canonical source
changes happen in the private monorepo and are projected from one exact Git object.
The canonical source commit for this generated tree is `b896509469ba520875b5b61d331baf4cb50213a4`.

## Project03 role

Project03 keeps capture, processing, and replication authority separate. This worker
owns only deterministic convergence of already-finalized immutable evidence objects
to one already-bound replica target:

1. read finalized object identity from the existing SQLite/filesystem authority;
2. validate the exact target identity and adapter binding;
3. clean only worker-owned transient partials;
4. adopt exact existing bytes or perform an immutable create;
5. independently read back and verify the target bytes;
6. record the verified state in canonical SQLite authority.

It does not capture or process media, provision storage, create credentials, change
IAM, choose a target, schedule future work, run as a daemon, or create a second source
of truth.

## Authority and state transitions

SQLite and immutable source/target bytes remain authoritative. The worker uses one
provider-neutral convergence contract across mounted NFSv4 and optional GCS
composition.

```text
finalized canonical source
→ discovered replica object
→ PENDING
→ exact adoption or immutable create
→ independent read-back verification
→ VERIFIED
```

The worker fails closed on target-identity mismatch, destination collision,
generation-race, corrupt source/read-back, unsafe paths, unavailable targets, and
unsupported exclusive publication. A collision is never overwritten. Only a proven
worker-owned transient partial may be removed during recovery. An exact rerun creates
no new replica work and does not rewrite verified bytes.

## Install and run once

Python 3.12 or newer is required. The base installation has no runtime Python
dependencies and does not install the Google Cloud SDK.

```text
python -m pip install -e '.[dev]'
replication-worker run \
  --database PATH \
  --spool-root PATH \
  --target-config replication.toml
replication-worker verify \
  --database PATH \
  --target-config replication.toml
```

A mounted-NFSv4 target configuration contains only non-secret identity and location:

```toml
adapter_kind = "mounted_nfs_v4"
target_id = "nas-a"
target_root = "/srv/edge-evidence-replica"
```

The legacy `--target-root` plus `--target-id` CLI remains available for existing NFS
operations. It routes through the same proven deterministic NFS worker facade.

### Optional GCS adapter

```text
python -m pip install -e '.[dev,gcs]'
```

```toml
adapter_kind = "gcs"
target_id = "gcs-dev"
bucket_name = "example-evidence-bucket"
prefix = "replicas/dev"
```

GCS authentication uses provider-standard external Application Default Credentials.
Tokens, service-account keys, credential files, and other secret material are not
configuration fields and do not belong in this repository. The exported fake-provider
tests prove immutable create, exact adoption, collision handling, generation-race
protection, and path-neutral provider errors without network or credential use.

Azure is not implemented or advertised.

## Standalone acceptance demo

```text
python demo/run_demo.py
```

The demo creates only temporary local SQLite, spool, and target directories. It
migrates fresh authority through schema version 10 and invokes the real exported CLI
to prove:

- one exact adoption of pre-existing immutable bytes;
- one immutable create after worker-owned transient cleanup;
- independent read-back verification;
- an exact rerun with no new publication, adoption, or registration;
- a separate destination collision that fails closed without overwriting bytes;
- zero unresolved work in the successful authority instance.

The compact receipt uses proof class `standalone_synthetic_replication`. The local
demo substitutes a temporary filesystem for the already-proven mounted-NFSv4 adapter
contract; it does not claim physical storage deployment, cloud operations, or
production proving. Temporary paths and machine-specific details are excluded from
the receipt.

## Bounded-agent contract surface

The generated package includes `packages.agent_contracts`, the accepted
framework-neutral contract library for typed observations, deterministic
classifications, bounded proposals, mutation-policy decisions, verification
outcomes, and structured receipts.

The exported replication example is model-free and read-only. It constructs a typed
pending-backlog observation, derives the deterministic classification and proposal,
and produces a `read-only-complete` receipt. It does not execute `replication-worker`,
contact a provider, provision storage, change IAM or credentials, mutate SQLite, or
create a second authority surface.

```text
python -m pytest -q tests/test_agent_contracts.py
```

Creating provider resources and changing IAM/credential state remain outside this
Wave C product. Typed future escalation and verification outcomes are data contracts
only; no executor, provider API client, or mutation workflow is included in this
product.

## Packaging boundary

The export contains the real CLI, provider-neutral core, mounted-NFSv4 adapter,
optional GCS adapter, schema-v10 migration closure, and focused standalone tests. It
excludes deployment roots, infrastructure, credentials, retained evidence, private
topology, and unrelated services.

The base package must remain importable without `google-cloud-storage`. The exact
optional `gcs` extra is the only exported cloud dependency surface.

## Provenance and reproducibility

See `EXPORT_PROVENANCE.json` and `DEPENDENCY_BOUNDARY.md`. Every projected canonical
file records its upstream Git blob identity. Two candidates generated from the same
canonical commit and descriptor must have identical paths, modes, bytes, provenance,
and content-tree digest.

Generated candidates must be tested in disposable copies so installation, bytecode,
and test caches cannot mutate publication bytes. Public publication is a separate
authorized action and is not implied by a passing private candidate.

## Contributions

Issues and discussion may happen here, but fixes are upstream-first. See
`CONTRIBUTING.md`.

## License

Publication license: **MIT**.
