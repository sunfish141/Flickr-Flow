"""Compare trusted classifier predictions across the web and desktop runtimes."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import json, numpy as np, joblib
from wildfire_data.core.model_artifacts import public_artifact
from pathlib import Path
path,_=public_artifact(Path('artifacts/public-csv/run_manifest.json'),'frontier.joblib')
model=joblib.load(path)['model']
rng=np.random.default_rng(1741)
rows=rng.uniform(0,400,(256,len(model.columns)))
rows[0,:]=0; rows[1,:]=1; rows[2,:]=np.nan
rows[3::3,::4]=np.nan
print(json.dumps({'numpy':np.__version__,'probabilities':model.predict_proba(rows).tolist()}))
'''


def main():
    import numpy as np
    reference = ROOT / '.venv-web' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
    runs = [json.loads(subprocess.check_output([str(executable), '-c', PROBE], cwd=ROOT, env=env, text=True))
            for executable in (reference, sys.executable)]
    difference = float(np.max(np.abs(np.asarray(runs[0]['probabilities']) - np.asarray(runs[1]['probabilities']))))
    assert difference <= 1e-12, f'Prediction drift: {difference}'
    report = {'purpose': 'Numerical compatibility only, not predictive validation',
              'reference_numpy': runs[0]['numpy'], 'desktop_numpy': runs[1]['numpy'],
              'probe_rows': 256, 'maximum_absolute_probability_difference': difference, 'tolerance': 1e-12}
    target = ROOT / 'artifacts/desktop-smoke/model-parity.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
