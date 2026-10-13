# ADR 0005: Use local-first validation and on-demand AWS demonstrations

## Status

Accepted.

## Context

The project is public and portfolio-oriented. It must demonstrate cloud capability without requiring permanently running or costly infrastructure.

## Decision

Contracts, generation, and business-rule tests are validated locally before cloud execution. AWS resources are provisioned with Terraform only for controlled demonstrations or benchmarks, then destroyed when no longer needed. No continuous stream processor or public endpoint belongs to V1.

## Consequences

The repository must document setup, cost assumptions, observed cost, and destruction separately. Demonstration results are published only after real execution and review.
