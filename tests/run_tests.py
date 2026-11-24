"""
Run every test: the profile tests (plain scripts) and the UI test (pytest, if installed).
    python tests/run_tests.py
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKIPPED = 3  # a test's exit status when the Extron driver it runs against isn't in tests/drivers/
failed = []
skipped = []
for path in sorted(glob.glob(os.path.join(HERE, 'test_*.py'))):
    name = os.path.basename(path)
    if name == 'test_ui.py':
        try:
            import pytest  # noqa: F401
        except ImportError:
            print('== {}: skipped (pip install pytest pytest-asyncio)'.format(name))
            continue
        cmd = [sys.executable, '-m', 'pytest', '-q', path]
    else:
        cmd = [sys.executable, path]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    if result.returncode == SKIPPED:
        skipped.append(name)
        reason = next((l[len('SKIP: '):] for l in result.stdout.splitlines() if l.startswith('SKIP: ')), '')
        print('== {}: skipped, {}'.format(name, reason))
        continue
    last = (result.stdout.strip().splitlines() or ['(no output)'])[-1]
    print('== {}: {}'.format(name, last))
    if result.returncode:
        failed.append(name)
        print(result.stdout[-3000:], result.stderr[-3000:])
if skipped:
    print('\n{} skipped: their Extron drivers aren\'t in tests/drivers/ (README, Tests)'.format(len(skipped)))
print('\nALL PASSED' if not failed else '\nFAILED: ' + ', '.join(failed))
sys.exit(1 if failed else 0)
