# Data relationships

## Business entities and temporal relationships

```mermaid
erDiagram
    CUSTOMER ||--o{ CUSTOMER_SITE : "holds over time"
    SITE ||--o{ CUSTOMER_SITE : "has holder history"

    SITE ||--o{ SITE_METER : "has meter history"
    METER ||--o{ SITE_METER : "is assigned through history"

    SITE ||--o{ SITE_CONTRACT : "has contract history"
    CONTRACT ||--|| SITE_CONTRACT : "defines"
    CONTRACT_TYPE ||--o{ CONTRACT : "classifies"
    CONTRACT_TYPE ||--o{ TARIFF_RULE : "is priced by"

    METER ||--o{ CONSUMPTION_INDEX : "emits"
    CONSUMPTION_INDEX ||--o{ CONSUMPTION_INTERVAL : "bounds"
    SITE ||--o{ DAILY_SITE_ENERGY : "produces"
    CUSTOMER ||--o{ DAILY_CUSTOMER_ENERGY : "consumes"

    CUSTOMER {
        uuid customer_id PK
    }
    SITE {
        uuid site_id PK
    }
    METER {
        uuid meter_id PK
    }
    CUSTOMER_SITE {
        uuid customer_id FK
        uuid site_id FK
        timestamp valid_from
        timestamp valid_to
    }
    SITE_METER {
        uuid site_id FK
        uuid meter_id FK
        timestamp valid_from
        timestamp valid_to
    }
    CONTRACT {
        uuid contract_id PK
        string contract_type
    }
    SITE_CONTRACT {
        uuid contract_id FK
        uuid site_id FK
        timestamp valid_from
        timestamp valid_to
    }
    CONTRACT_TYPE {
        string contract_type PK
    }
    TARIFF_RULE {
        uuid tariff_rule_id PK
        string contract_type FK
        decimal price_eur_per_kwh
    }
    CONSUMPTION_INDEX {
        uuid meter_id FK
        timestamp reading_at
        bigint meter_index_wh
        int source_version
    }
    CONSUMPTION_INTERVAL {
        uuid meter_id FK
        timestamp interval_start
        timestamp interval_end
        bigint consumption_wh
    }
    DAILY_SITE_ENERGY {
        uuid site_id FK
        date energy_date
        decimal consumption_kwh
        decimal energy_cost_eur
    }
    DAILY_CUSTOMER_ENERGY {
        uuid customer_id FK
        date energy_date
        decimal consumption_kwh
        decimal energy_cost_eur
    }
```

`CUSTOMER_SITE`, `SITE_METER`, and `SITE_CONTRACT` are temporal relationship flows. They preserve history through `[valid_from, valid_to)` intervals rather than overwriting prior state.

At a given instant, a site has zero or one active primary holder, exactly one active meter, and exactly one active contract. Over time, a customer can hold many sites and a site can be associated with many historical holders, meters, or contracts.

## Consumption derivation and allocation

```mermaid
flowchart LR
    Previous["Previous valid index<br/>meter_index_wh"]
    Current["Current valid index<br/>meter_index_wh"]
    Rollover["Declared rollover<br/>when applicable"]
    Interval["Consumption interval<br/>consumption_wh"]
    Context["Temporal context<br/>site, customer, contract"]
    Tariff["Applicable tariff rule<br/>EUR per kWh"]
    Gold["Gold products<br/>kWh and energy cost"]

    Previous --> Interval
    Current --> Interval
    Rollover --> Interval
    Interval --> Context
    Context --> Tariff
    Tariff --> Gold
```

An installation baseline index begins a new meter history but produces no consumption. A correction or late index can change the adjacent intervals and only the corresponding gold products are recalculated.
