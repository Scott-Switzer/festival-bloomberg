import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('../../apps/terminal/mvp/app.js', import.meta.url), 'utf8');
const renderSource = source.slice(source.indexOf('function fmtDelta('), source.indexOf('function renderSentiment('));
function render(change: Record<string, unknown>, value: number | null = 10) {
  const box = { innerHTML: '' };
  const context = { document: { getElementById: () => box }, esc: String, fmtDate: String,
    money: String, tape: { items: [{ factor_name: 'subscriber_count', value }] }, changes: [change] };
  runInNewContext(`${renderSource}\nrenderTape(tape, changes);`, context);
  return box.innerHTML;
}
describe('buyer factor comparability', () => {
  it('shows an incomparable reason and suppresses all deltas even if supplied', () => {
    const html = render({factor_name:'subscriber_count',comparability:'NOT_COMPARABLE',comparability_reason:'geography missing',delta:999,delta_pct:88});
    expect(html).toContain('NOT_COMPARABLE');
    expect(html).toContain('geography missing');
    expect(html).not.toContain('+999');
    expect(html).not.toContain('88.0%');
  });
  it('shows comparable changes and preserves unknown versus observed zero', () => {
    const change={factor_name:'subscriber_count',comparability:'COMPARABLE',delta:10,delta_pct:25};
    expect(render(change)).toContain('+25.0%');
    expect(render(change,null)).toContain('UNKNOWN');
    expect(render(change,0)).not.toContain('<td>UNKNOWN</td>');
    expect(render(change,0)).toContain('<td>0 ');
  });
});


describe('home navigation during pending requests', () => {
  for (const reject of [false, true]) {
    it(`ignores a stale home request when it ${reject ? 'rejects' : 'resolves'}`, async () => {
      let settle!: (value: unknown) => void;
      const pending = new Promise((resolve, fail) => { settle = reject ? fail : resolve; });
      const context = { routeVersion: 1, view: {innerHTML: ''}, setNav: () => {}, api: () => pending,
        document: {getElementById: () => { throw new Error('stale DOM access'); }} };
      const home = source.slice(source.indexOf('async function renderHome()'), source.indexOf('function demoCard('));
      const result = runInNewContext(`${home}\nrenderHome();`, context);
      context.routeVersion = 2;
      settle(reject ? new Error('request failed') : {counts: {}});
      await expect(result).resolves.toBeUndefined();
    });
  }
});


describe('public demo capabilities', () => {
  it('does not request an unavailable private API or invent an empty dataset', async () => {
    let calls = 0;
    const apiSource = source.slice(source.indexOf('async function api('), source.indexOf('const esc ='));
    const context = { document: {querySelector: () => ({dataset: {unavailableApiPrefixes: JSON.stringify(['/api/shortlist','/api/monitor','/api/pace'])}})},
      terminalApiPath: String, fetch: async () => { calls += 1; return {ok: true, json: async () => ({observed: true})}; } };
    for (const path of ['/api/shortlist','/api/monitor?limit=1','/api/pace/event/x']) {
      await expect(runInNewContext(`${apiSource}\napi(${JSON.stringify(path)});`, context)).rejects.toThrow('unavailable in this public demo');
    }
    expect(calls).toBe(0);
    await expect(runInNewContext(`${apiSource}\napi('/api/search?q=artist');`, context)).resolves.toEqual({observed:true});
    expect(calls).toBe(1);
  });
});
