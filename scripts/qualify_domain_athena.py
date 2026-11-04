#!/usr/bin/env python3
"""Reconcile the full V1 domain through complete typed-row hashes and Gold results."""
import argparse
import json
from pathlib import Path
from lakehouse_platform.domain_validation import qualify_domain
from lakehouse_platform.demo import write
from lakehouse_platform.analyst_queries import validate_analyst_session
from lakehouse_platform.integrated_pipeline import build_integrated_invocations

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
    build_integrated_invocations(values, 'domain-audit', load_references=False)
    session = boto3.Session(profile_name=args.project_profile, region_name=args.region)
    identity = session.client('sts').get_caller_identity()
    account = identity['Account']
    if not identity['Arn'].startswith(f'arn:aws:sts::{account}:assumed-role/aws-lakehouse-platform-demo-terraform-operator/'):
        raise ValueError('Use the exact project operator for the operational audit')
    role = values['analyst_access']['role_arn']
    if role != f'arn:aws:iam::{account}:role/{values["glue_database_name"].replace("_", "-")}-analyst':
        raise ValueError('Use the exact same-demo analyst role')
    credentials = session.client('sts').assume_role(RoleArn=role, RoleSessionName='lakehouse-domain-audit', DurationSeconds=900)['Credentials']
    parameters = {'aws_access_key_id': credentials['AccessKeyId'], 'aws_secret_access_key': credentials['SecretAccessKey'],
                  'aws_session_token': credentials['SessionToken'], 'region_name': args.region}
    analyst = boto3.Session(**parameters)
    analyst_group = validate_analyst_session(analyst, values, account)
    report = qualify_domain(session.client('athena'), values['glue_database_name'], values['athena_workgroup_name'], args.domain_dir,
        analyst_athena=analyst.client('athena'), analyst_workgroup=analyst_group)
    report['actual_analyst_credentials_verified'] = True
    write(args.report, report)
    if not report['passed']: raise SystemExit('Full-domain reconciliation failed')
    print('Complete V1 accepted rows, references and Gold products reconciled')
