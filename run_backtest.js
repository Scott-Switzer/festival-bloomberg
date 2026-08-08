import { spawnSync } from 'child_process';
const res = spawnSync('python3', ['/workspace/user/portfolio-engine/scripts/backtest_runner.py'], { encoding: 'utf8' });
console.log('STDOUT:', res.stdout);
console.log('STDERR:', res.stderr);
console.log('EXIT CODE:', res.status);
