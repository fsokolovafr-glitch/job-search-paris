"""Scheduled pipeline: import new reviewed snapshots, monitor, regenerate views."""
import json
import os
import subprocess
import sys
from pathlib import Path
from history_store import HistoryStore
from pipeline import export_all, import_snapshot, writer_lock

ROOT = Path(__file__).resolve().parents[1]


def main():
    action = os.environ.get('PIPELINE_ACTION', 'monitor')
    if action not in ('monitor', 'sync'):
        raise ValueError('Unknown action')
    with writer_lock(ROOT), HistoryStore(ROOT / 'data/job-search.db') as store:
        snapshots = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((ROOT / 'sources').glob('artifact-*.json'))]
        for snapshot in sorted(snapshots, key=lambda s: s['snapshot_at']):
            import_snapshot(store, snapshot)
        export_all(store, ROOT)
    if action == 'monitor':
        subprocess.run([sys.executable, str(ROOT / 'scripts/check-jobs.py'), '--mode', 'live', '--discovery', 'auto'], check=True)
    summary = json.loads((ROOT / 'data/pipeline-summary.json').read_text())
    text = '# Pipeline result\n\n```json\n' + json.dumps(summary, ensure_ascii=False, indent=2) + '\n```\n'
    if summary['latest_monitoring'].get('status') == 'partial':
        text += '\nPartial coverage: read the monitoring report. UNKNOWN is not CLOSED.\n'
        print('::warning::Partial monitoring coverage; inspect report for missing discovery key or blocked pages.')
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(text)


if __name__ == '__main__':
    main()
