# V1 Architecture

## Data platform and processing flow

```mermaid
flowchart TB
    Generator["Synthetic data generator<br/>deterministic UUIDs and batches"]
    Manifest["Batch manifest<br/>files, checksums, seed, scenario"]

    subgraph AWS["AWS account"]
        direction TB

        subgraph S3["Amazon S3"]
            Landing["Bronze landing zone<br/>immutable source-shaped batches"]
            Warehouse["Iceberg warehouse<br/>silver and gold tables"]
            Results["Athena results and<br/>reviewed benchmark artifacts"]
        end

        Catalog["AWS Glue Data Catalog<br/>Iceberg metadata"]

        subgraph Glue["AWS Glue and PySpark"]
            Reference["Reference-data job<br/>site, customer, contract, tariff history"]
            Indexes["Index-processing job<br/>validation and consumption intervals"]
            Gold["Gold-aggregation job<br/>tariff allocation and products"]
            Compact["On-demand maintenance<br/>Iceberg compaction"]
        end

        subgraph Iceberg["Apache Iceberg tables"]
            Silver["Silver<br/>validated indexes, temporal history,<br/>intervals, quarantine, audit"]
            GoldTables["Gold<br/>daily site energy,<br/>daily customer energy, monthly energy"]
        end

        Athena["Amazon Athena<br/>analyst queries and benchmarks"]
        LakeFormation["AWS Lake Formation<br/>ingestion, transformation,<br/>and analyst permissions"]
        CloudWatch["Amazon CloudWatch<br/>Glue logs and batch metrics"]
    end

    Terraform["Terraform<br/>provision and destruction"]
    Analyst["Analyst role"]

    Generator --> Landing
    Manifest --> Landing
    Landing --> Reference
    Landing --> Indexes
    Reference --> Silver
    Indexes --> Silver
    Silver --> Gold
    Gold --> GoldTables
    GoldTables --> Athena
    Compact --> GoldTables
    Catalog --- Warehouse
    Silver --- Warehouse
    GoldTables --- Warehouse
    Athena --> Results
    LakeFormation -.-> Reference
    LakeFormation -.-> Indexes
    LakeFormation -.-> Gold
    LakeFormation -.-> Analyst
    Analyst --> Athena
    Reference --> CloudWatch
    Indexes --> CloudWatch
    Gold --> CloudWatch
    Terraform -.-> AWS
```

The diagram describes the intended V1 deployment. The generator is local and produces only synthetic data. Bronze retains immutable landing batches, silver holds validated operational state, and gold exposes analyst-ready products through Athena.

## Access and operational boundaries

```mermaid
flowchart LR
    Producer["Local synthetic-data producer"]
    Ingestion["Ingestion role<br/>write landing only"]
    Transform["Transformation role<br/>read landing, write Iceberg"]
    Analyst["Analyst role<br/>read approved gold only"]

    subgraph S3["Amazon S3"]
        Landing["Bronze landing"]
        Warehouse["Iceberg warehouse"]
        Results["Athena results"]
    end

    subgraph DataPlane["AWS data plane"]
        Glue["Glue and PySpark"]
        LF["Lake Formation"]
        Athena["Athena"]
    end

    Producer --> Ingestion
    Ingestion --> Landing
    Landing --> Glue
    Transform --> Glue
    Glue --> Warehouse
    LF -.-> Transform
    LF -.-> Analyst
    Analyst --> Athena
    Athena --> Warehouse
    Athena --> Results
```

The analyst does not read landing files or operational silver tables. No static AWS credentials, account identifiers, or real data belong in the repository or rendered evidence.
