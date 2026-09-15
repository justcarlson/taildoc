"""Reject empty, skipped, failed, or incomplete required test lanes."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def check_report(path: Path, minimum: int = 1) -> int:
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    if len(cases) < minimum:
        raise ValueError(f'Expected at least {minimum} tests; found {len(cases)}')
    if any(case.find(tag) is not None for case in cases
           for tag in ('failure', 'error', 'skipped')):
        raise ValueError('Required tests failed, errored, or skipped')
    return len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--minimum', type=int, default=1)
    args = parser.parse_args()
    print(f'{check_report(args.report, args.minimum)} required tests passed')
