# ADR 0003: Preserve temporal relationships and one active meter per site

## Status

Accepted.

## Context

A site has one active meter at any given time, but a meter can be replaced. Customer ownership and contracts also evolve over time.

## Decision

`site_meter`, `customer_site`, and `site_contract` use UTC validity intervals following `[valid_from, valid_to)`. A site can have one active meter, one active primary holder, and one active contract at a time. Meter replacement closes the previous meter relationship, opens the new relationship, and creates an installation baseline index.

## Consequences

The platform can reconstruct historical ownership and tariff context. It must validate temporal overlap and recalculate only the affected time range when a relationship is corrected.
