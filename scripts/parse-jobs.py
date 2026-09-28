#!/usr/bin/env python3
"""Extract raw schema.org JobPosting objects from saved HTML; no LIVE claims."""
import argparse
import json
from pathlib import Path
from monitor import job_postings

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html', type=Path)
    args = parser.parse_args()
    print(json.dumps(job_postings(args.html.read_text(encoding='utf-8')), ensure_ascii=False, indent=2))
