#!/usr/bin/env python3
"""Reconcile the full V1 domain through complete typed-row hashes and Gold results."""
import argparse
import json
from pathlib import Path
from lakehouse_platform.domain_validation import qualify_domain
from lakehouse_platform.demo import write

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain-dir', type=Path, required=True)
    parser.add_argument('--outputs', type=Path, required=True)
    parser.add_argument('--project-profile', required=True)
    parser.add_argument('--region', default='eu-west-3')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists(): raise FileExistsError('Keep the original acceptance report')
    import boto3
    values = {key: row['value'] for key, row in json.loads(args.outputs.read_text()).items()}
    session = boto3.Session(profile_name=args.project_profile, region_name=args.region)
    report = qualify_domain(session.client('athena'), values['glue_database_name'], values['athena_workgroup_name'], args.domain_dir)
    write(args.report, report)
    if not report['passed']: raise SystemExit('Full-domain reconciliation failed')
    print('Complete V1 accepted rows, references and Gold products reconciled')
