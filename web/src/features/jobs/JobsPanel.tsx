// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect } from 'react';
import { dispatch } from '../../commands';
import { isTerminal } from '../../api/types';
import { useStore } from '../../state/store';

const POLL_MS = 1000;

/** Jobs: progress, cancel and the budget in force. Active jobs are polled (`job.refresh`). */
export function JobsPanel() {
  const jobs = useStore((s) => s.jobs);
  const budget = useStore((s) => s.jobBudget);
  const active = jobs.filter((j) => !isTerminal(j.state)).map((j) => j.id).join(',');
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => active.split(',').forEach((jobId) => void dispatch({ type: 'job.refresh', jobId })), POLL_MS);
    return () => clearInterval(t);
  }, [active]);
  const unlimited = budget.max_runtime_s == null && budget.max_memory_gb == null;
  return (
    <section aria-label="Jobs">
      <h2>Jobs</h2>
      <p className="muted" data-testid="budget">
        Budget: {unlimited ? 'none set' : `${budget.max_runtime_s != null ? `${budget.max_runtime_s} s` : 'any time'}, ${budget.max_memory_gb != null ? `${budget.max_memory_gb} GB` : 'any memory'}`}
      </p>
      {jobs.length === 0 && <p className="muted">No jobs yet.</p>}
      <ul className="rows jobs">
        {jobs.map((j) => (
          <li key={j.id} data-testid="job-row">
            <div className="grow">
              <div>
                {j.analysis} <span className={`badge job-${j.state}`} data-testid="job-state">{j.state}</span>
              </div>
              <progress max={1} value={j.progress} aria-label={`Progress ${j.analysis}`} />
              <div className="muted">{j.tasks_done}/{j.tasks_total} tasks{j.tasks_cached ? `, ${j.tasks_cached} cached` : ''}{j.message ? ` - ${j.message}` : ''}</div>
              {j.error && <div className="bad">{j.error.code}: {j.error.message}</div>}
              {j.cancel_reason && <div className="muted">cancelled: {j.cancel_reason}</div>}
            </div>
            {!isTerminal(j.state) && <button onClick={() => void dispatch({ type: 'job.cancel', jobId: j.id })}>Cancel</button>}
            {j.state === 'succeeded' && j.result_id && (
              <button onClick={() => void dispatch({ type: 'result.show', resultId: j.result_id! })}>Show</button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
