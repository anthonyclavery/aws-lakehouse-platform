# Demonstration workflow

## Local-first validation and controlled AWS demonstration

```mermaid
flowchart TB
    Start["Review documentation and local changes"]
    Local["Run local deterministic validation<br/>contracts, business rules, reference scenarios"]
    Plan["Review Terraform plan<br/>before cloud creation"]
    Apply["Provision short-lived AWS environment"]
    Bootstrap["Generate and upload<br/>initial synthetic batches"]
    Reference["Run reference-data Glue job"]
    Index["Run index-processing Glue job"]
    Aggregate["Run gold-aggregation Glue job"]
    Query["Query Athena gold products<br/>kWh and synthetic energy cost"]
    Incremental["Upload normal incremental batch"]
    Reprocess["Run bounded incremental processing"]
    Exception["Upload one controlled exception<br/>late index, correction, rollover, or replacement"]
    Verify["Verify recalculation, quarantine,<br/>lineage, and Lake Formation access"]
    Benchmark["Run one measured benchmark<br/>with result reuse disabled"]
    Review["Review evidence for public safety"]
    Destroy["Destroy billable demo resources<br/>and verify cleanup"]

    Start --> Local
    Local --> Plan
    Plan --> Apply
    Apply --> Bootstrap
    Bootstrap --> Reference
    Reference --> Index
    Index --> Aggregate
    Aggregate --> Query
    Query --> Incremental
    Incremental --> Reprocess
    Reprocess --> Query
    Query --> Exception
    Exception --> Reprocess
    Reprocess --> Verify
    Verify --> Benchmark
    Benchmark --> Review
    Review --> Destroy
```

The demonstration is deliberately staged. Local correctness is proven before AWS use. The cloud segment demonstrates one initial load, one normal incremental batch, and one exception path before a controlled benchmark and destruction.

## Exception path

```mermaid
flowchart LR
    Batch["Late or corrected batch"]
    Audit["Batch audit and version check"]
    Merge["Iceberg MERGE INTO"]
    Scope["Identify affected intervals<br/>and temporal context"]
    Recalculate["Recalculate bounded silver and gold data"]
    Observe["Publish batch metrics,<br/>quality status, and lineage"]

    Batch --> Audit
    Audit --> Merge
    Merge --> Scope
    Scope --> Recalculate
    Recalculate --> Observe
```

An unchanged replay stops at the version check. A higher source version or a newly arrived historical index follows the recalculation path.
