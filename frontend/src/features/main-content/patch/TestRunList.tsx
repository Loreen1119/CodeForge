import type { TestRun } from '../../../types';

const STATUS_LABELS: Record<string, string> = {
  passed: '通过',
  failed: '失败',
  error: '执行错误',
  timeout: '超时',
  running: '执行中',
};

export function TestRunList({ runs }: { runs: TestRun[] }) {
  if (runs.length === 0) return <p className="muted">尚未执行测试</p>;

  return (
    <ul className="test-run-list">
      {runs.map((run) => (
        <li key={run.id}>
          <details className="test-run" open={run.status !== 'passed'}>
            <summary>
              <span className={`badge status-${run.status}`}>{STATUS_LABELS[run.status] ?? run.status}</span>
              <code>{run.command}</code>
              {run.exit_code !== null && <span className="muted">exit {run.exit_code}</span>}
              {run.timed_out && <span className="badge risk-medium">超时</span>}
            </summary>
            {run.environment_error && <p className="form-error">环境错误：{run.environment_error}</p>}
            <pre className="test-output">{run.output || '（无输出）'}</pre>
          </details>
        </li>
      ))}
    </ul>
  );
}
