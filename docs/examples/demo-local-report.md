# Smart-meter demonstration

Run: **demo-preview**. Mode: **local**. Status: **completed**.

Synthetic fixture: three customers/sites, BASE, WEEKEND and PEAK_OFF_PEAK. Dates use Europe/Paris.

Local mode computes expected business results; it does not measure AWS jobs, permissions or Iceberg snapshots.

| Phase | Indexes | Intervals | Raw Wh | Gold kWh | Energy cost EUR |
| --- | ---: | ---: | ---: | ---: | ---: |
| initial | 9 | 6 | 30000 | 30.000000 | 4.700000 |
| late | 12 | 9 | 30000 | 30.000000 | 4.771429 |
| correction | 12 | 9 | 30000 | 30.000000 | 4.871429 |
| unchanged-replay | 12 | 9 | 30000 | 30.000000 | 4.871429 |
| older-replay | 12 | 9 | 30000 | 30.000000 | 4.871429 |

| Phase | BASE EUR | WEEKEND EUR | PEAK_OFF_PEAK EUR |
| --- | ---: | ---: | ---: |
| initial | 2.000000 | 1.300000 | 1.400000 |
| late | 2.000000 | 1.400000 | 1.371429 |
| correction | 2.000000 | 1.400000 | 1.471429 |
| unchanged-replay | 2.000000 | 1.400000 | 1.471429 |
| older-replay | 2.000000 | 1.400000 | 1.471429 |

The late reading splits an existing interval. The correction changes its endpoint and successor while conserving 30,000 Wh.
Gold quantizes each priced segment to twelve decimals; the 0.000000000003 kWh residue is rounding, not additional raw energy.
Tables display six decimal places; the companion JSON retains exact measured decimal values.
Both replays must retain corrected versions and complete product contents.

## Checks

initial: AWS_access_and_metadata: not measured locally.

late: AWS_access_and_metadata: not measured locally.

correction: AWS_access_and_metadata: not measured locally.

unchanged-replay: AWS_access_and_metadata: not measured locally.

older-replay: AWS_access_and_metadata: not measured locally.

Costs cover synthetic energy only, excluding subscription charges and taxes. AWS service charges are not measured by this report.
Data-plane cleanup is a separate explicit command; this report does not certify destruction.
