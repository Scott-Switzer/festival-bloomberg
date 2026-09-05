/** Safe projection of the job-owned manifest over the launch acknowledgement. */
export function withJobManifest(status: Record<string, any>, manifest: Record<string, any>): Record<string, any> {
  if (manifest.job_id !== status.job_id || manifest.job_type !== status.job_type) return status;
  const published = manifest.publication_state === 'PUBLISHED';
  const failed = manifest.status === 'FAILED' || Boolean(manifest.error_code);
  return {
    ...status,
    status: published ? 'COMPLETED' : failed ? 'FAILED' : 'RUNNING',
    code_commit: typeof manifest.code_commit === 'string' ? manifest.code_commit : status.code_commit,
    completed_batches: manifest.completed_batches ?? 0,
    total_batches: manifest.total_batches ?? 0,
    bytes_read: manifest.r2_read_bytes ?? 0,
    bytes_written: manifest.r2_write_bytes ?? 0,
    runtime_seconds: manifest.runtime_seconds ?? 0,
    publication_state: manifest.publication_state,
    last_safe_error_code: failed ? (manifest.error_code === 'PUBLICATION_FAILED' ? 'PUBLICATION_FAILED' : 'JOB_EXEC_FAILED') : undefined,
  };
}
