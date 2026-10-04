import { useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../../../api/client';
import type { TestRun } from '../../../types';
import { TestRunList } from './TestRunList';

interface TestPanelProps {
  taskId: number;
  patchId: number;
  /** 补丁是否已应用到隔离 Worktree；未应用时后端会拒绝执行测试 */
  applied: boolean;
  runs: TestRun[];
  allowedCommands: string[];
  onRunCreated: (run: TestRun) => void;
  /** 测试会产生 TEST_RUN_COMPLETED 事件，交由上层同步事件日志 */
  onSettled: () => void;
}

export function TestPanel({ taskId, patchId, applied, runs, allowedCommands, onRunCreated, onSettled }: TestPanelProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [command, setCommand] = useState(allowedCommands[0] ?? '');

  // 仓库白名单可能晚于补丁到达，保证默认选中第一条可执行命令
  useEffect(() => {
    if (allowedCommands.length > 0 && !allowedCommands.includes(command)) {
      setCommand(allowedCommands[0]);
    }
  }, [allowedCommands, command]);

  async function runTest() {
    if (!command) return;
    setBusy(true);
    setError(null);
    try {
      const run = await apiFetch<TestRun>(`/api/tasks/${taskId}/patches/${patchId}/tests`, {
        method: 'POST',
        body: JSON.stringify({ command }),
      });
      onRunCreated(run);
      onSettled();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="test-panel">
      <p className="muted">只允许执行仓库白名单里的命令，且在补丁应用后的隔离 Worktree 中运行。</p>
      {allowedCommands.length === 0 && <p className="form-error">该仓库未配置白名单测试命令，请先补上再运行测试。</p>}
      {allowedCommands.length > 1 && (
        <>
          <label className="sr-only" htmlFor="test-command">测试命令</label>
          <select id="test-command" value={command} onChange={(e) => setCommand(e.target.value)}>
            {allowedCommands.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </>
      )}
      {allowedCommands.length === 1 && <p className="muted">将执行：<code>{allowedCommands[0]}</code></p>}

      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="button-row">
        <button className="btn-round-primary" onClick={runTest} disabled={busy || !command || !applied}>
          {busy ? '执行中…' : '运行测试'}
        </button>
      </div>
      {!applied && <p className="muted">补丁应用成功后才可以运行测试。</p>}

      <TestRunList runs={runs} />
    </div>
  );
}
