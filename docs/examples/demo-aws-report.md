# Smart-meter demonstration

Run: **acceptance-complete**. Mode: **aws**. Status: **completed**.

Synthetic fixture: three customers/sites, BASE, WEEKEND and PEAK_OFF_PEAK. Dates use Europe/Paris.

AWS values are read using actual temporary analyst credentials and reconciled against the complete local history.

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

initial: Gold reconciliation: True; Silver reconciliation: True; analyst refusals and SELECT restoration: True.

late: Gold reconciliation: True; Silver reconciliation: True; analyst refusals and SELECT restoration: True.

correction: Gold reconciliation: True; Silver reconciliation: True; analyst refusals and SELECT restoration: True.

unchanged-replay: Gold reconciliation: True; Silver reconciliation: True; analyst refusals and SELECT restoration: True; eleven data metadata identities stable: True.

older-replay: Gold reconciliation: True; Silver reconciliation: True; analyst refusals and SELECT restoration: True; eleven data metadata identities stable: True.

Costs cover synthetic energy only, excluding subscription charges and taxes. AWS service charges are not measured by this report.
Data-plane cleanup is a separate explicit command; this report does not certify destruction.
