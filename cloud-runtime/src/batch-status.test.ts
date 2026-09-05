import { describe, it, expect } from 'vitest';
import { withJobManifest } from './batch-status';
const launch={job_id:'refresh_1',job_type:'artist_factor_tape_build_v1',status:'RUNNING',completed_batches:0};
describe('durable production job status',()=>{
  it('reconstructs progress and completion after the launch isolate is gone',()=>{
    const manifest={...launch,status:'PUBLISHED',publication_state:'PUBLISHED',completed_batches:40,total_batches:40,code_commit:'a'.repeat(40),r2_read_bytes:123};
    expect(withJobManifest(launch,manifest)).toMatchObject({status:'COMPLETED',completed_batches:40,code_commit:'a'.repeat(40),bytes_read:123});
  });
  it('reports an unpublished stale writer as failed without exposing raw errors',()=>{
    const result=withJobManifest(launch,{...launch,status:'VERIFIED',publication_state:'VERIFIED',error_code:'PUBLICATION_FAILED',error:'private diagnostic',traceback:'private'});
    expect(result.status).toBe('FAILED');expect(result).not.toHaveProperty('error');expect(result).not.toHaveProperty('traceback');
  });
  it('rejects a manifest from another logical job',()=>expect(withJobManifest(launch,{...launch,job_id:'other',publication_state:'PUBLISHED'})).toEqual(launch));
});
