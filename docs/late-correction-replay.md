# Governed late data, corrections and processing replays

This campaign extends the [integrated identity chain](integrated-chain.md) with late cumulative indexes, higher-version corrections, unchanged replay and replay of the original older source. Every source publication uses the dedicated ingestion role; the normal managed jobs use the transformation role; Gold is reconciled using actual temporary analyst credentials. LF governance is enabled before the late/correction/replay stages.

## Deterministic history

Generate a fresh fixture with `python scripts/generate_late_chain_fixture.py --output-dir artifacts/late-chain/batches`. The initial source is the same three-contract weekend/hour-boundary fixture as the integrated campaign. Its nine indexes produce six intervals. For each meter, the late batch adds a previously missing reading at 23:00 UTC between existing 22:00 and 06:00 readings, with cumulative value 5,000 Wh. The correction changes the existing 22:00 index from 3,000 to 4,000 Wh using `source_version=2`. Both batches preserve the final cumulative index.

| Phase | Distinct indexes | Intervals | Raw energy Wh | Exact unrounded total cost EUR |
| --- | ---: | ---: | ---: | --- |
| Initial | 9 | 6 | 30,000 | 47/10 |
| Late | 12 | 9 | 30,000 | 167/35 |
| Correction | 12 | 9 | 30,000 | 341/70 |
| Either replay | 12 | 9 | 30,000 | 341/70 |

The late point redistributes consumption across midnight and tariffs. The correction changes precisely its interval endpoint and its successor for all three meters, six interval rows, while conserving integer Wh. Unit assertions verify the rational totals independently of the pricing calculator. Persisted Gold rounds each priced segment to twelve decimals before summing: the expected late/corrected product energy is `30.000000000003` kWh, and the costs are `4.771428571428` / `4.871428571428` EUR. These tiny quantization residues do not change the exact 30,000 Wh in Silver. Full deployed/local comparisons retain the existing `1e-9` per-group tolerance and publish actual fingerprints/errors.

## Source and processing identities

Run the initial batch with `scripts/run_integrated_batch.py --load-references` as in the integrated procedure. Enable and seal analyst access with the existing guarded helper, retaining the ingestion flag/profile in private variables. Restore original LF account settings before further processing. Run full Silver and real analyst qualification on the initial state, then on the cumulative histories `[initial, late]` and `[initial, late, correction]` after their three normal Silver/pricing/aggregation jobs.

Use distinct Bronze IDs for initial, late and correction. Every batch's bytes and manifest remain immutable. For replay, reuse that original Bronze ID and source directory so upload validates/reuses stored bytes. Set a **new** `--gold-batch-id` so the pricing/aggregation chain must evaluate a fresh Gold generation rather than returning a previously completed run:

```bash
python scripts/run_integrated_batch.py \
  --outputs artifacts/late-chain/outputs.json \
  --batch-dir artifacts/late-chain/batches/correction \
  --batch-id late-chain-correction \
  --gold-batch-id late-chain-unchanged-replay \
  --project-profile PROJECT_OPERATOR_PROFILE \
  --artifact-dir artifacts/late-chain/unchanged-replay
```

For the final older-source replay, use the original initial directory/ID, a new Gold ID, and `--load-references`. This repasses all four reference flows and the older index versions through the complete chain. Source version 1 must not replace corrected version 2, and no new late key may be lost. The complete campaign plans 23 managed runs: seven initial, three late, three correction, three unchanged replay and seven older-source replay. The explicit launcher waits 30 seconds before reusing a completed reference job to reduce immediate capacity-release collisions; native queuing remains enabled and does not guarantee availability or success. The pause can be configured with `--reference-settle-seconds`.

## Proof of unchanged processing

Before and after each replay, capture the hashes of existing Iceberg metadata locations:

```bash
python scripts/qualify_processing_replay.py capture \
  --outputs artifacts/late-chain/outputs.json \
  --project-profile PROJECT_OPERATOR_PROFILE \
  --report artifacts/late-chain/before-metadata.json
```

The command requires actual Iceberg metadata locations in the exact project warehouse, not missing values treated as stable. It covers all eleven data tables: indexes, intervals, four reference histories, quarantine, priced intervals and three Gold products. Only hashes are written. Work/control/stage tables are excluded because a new Gold generation legitimately records new checkpoint metadata.

Re-run full Silver and real analyst comparisons against the complete corrected history after each replay. Then use `qualify_processing_replay.py compare` with `--silver-before`, `--silver-after`, `--analyst-before`, `--analyst-after`, `--metadata-before`, `--metadata-after` and a fresh `--report`. It refuses incomplete, unsuccessful or reused-result evidence, and checks complete content fingerprints, row counts, versions and every contracted data-table metadata identity. Equal counts alone cannot pass. A changed fingerprint or data-table metadata location returns failure. Raw job journals separately prove the fresh Gold IDs and every successful managed run.

Repeat the ingestion access refusals on the same governed environment, require zero managed changes in the enabled-feature Terraform plan, then run pushed-revision guarded cleanup and independent post-destruction checks. Review/sanitize public evidence. Keep account/resource pointers, profiles, source query rows, state, plans, LF settings and raw journals private.

## Cleanup window

If a campaign crosses the existing daily cleanup time, save its complete configuration and defer that same enabled schedule only for a bounded window before provisioning. Keep a safety cleanup active and a durable restoration backstop. After controlled destruction, restore and independently compare the original schedule configuration and verify Terraform convergence for both roots. Never leave a disabled schedule or silently accept schedule drift as a clean deployment.
