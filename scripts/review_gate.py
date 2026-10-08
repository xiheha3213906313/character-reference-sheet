"""Validate current review records and fixed versions; never judge image semantics."""
import argparse
import json
from pathlib import Path
import sys

from check_delivery import ROLES
from review_v2 import check_v2 as check, read


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--baseline', action='store_true', help='仅核查首张批准，须与--stage联用，不批准下游')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--stage', choices=ROLES)
    group.add_argument('--before', choices=ROLES)
    args = parser.parse_args()
    try:
        result = check(read(args.record), (args.root or args.record.parent).resolve(),
                       args.stage, args.before, baseline=args.baseline)
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        result = {'scope': 'review_records_and_versions', 'model_visual_checks': False,
                  'record_integrity_valid': False, 'recorded_approval_valid': False, 'errors': [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['record_integrity_valid'] else 1


if __name__ == '__main__':
    sys.exit(main())
