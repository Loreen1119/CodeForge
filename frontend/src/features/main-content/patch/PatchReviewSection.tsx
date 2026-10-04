import { useState } from 'react';
import { apiFetch, errorMessage } from '../../../api/client';
import type { Patch } from '../../../types';
import { DiffView } from './DiffView';

interface PatchReviewSectionProps {
  taskId: number;
  patch: Patch;
  onPatchUpdate: (patch: Patch) => void;
  /** 批准/应用会产生 PATCH_APPROVED / PATCH_APPLIED 事件，交由上层同步事件日志 */
  onSettled: () => void;
}

/** 补丁审查：变更概览 + diff 着色 + 批准 / 应用。测试是独立一步，见 TestPanel。 */
export function PatchReviewSection({ taskId, patch, onPatchUpdate, onSettled }: PatchReviewSectionProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const applied = patch.apply_status === 'applied';

  async function patchAction(action: 'approve' | 'apply') {
    setBusy(action);
    setError(null);
    try {
      const updated = await apiFetch<Patch>(`/api/tasks/${taskId}/patches/${patch.id}/${action}`, {
        method: 'POST',
        body: '{}',
      });
      onPatchUpdate(updated);
      onSettled();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="patch-panel">
      <p className="muted">
        {patch.changed_files.join('、')} · 变更 {patch.changed_lines} 行 · 风险 {patch.risk_level}
        {patch.validation_status && ` · 校验 ${patch.validation_status}`}
      </p>
      {patch.validation_status && patch.validation_status !== 'valid' && (
        <p className="form-error">校验未通过（{patch.validation_status}）</p>
      )}
      {patch.security_findings && patch.security_findings.length > 0 && (
        <ul className="finding-list">{patch.security_findings.map((item, index) => <li key={index}>{item}</li>)}</ul>
      )}

      <DiffView diff={patch.diff} />

      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="button-row">
        <button
          className="btn-round-primary"
          onClick={() => patchAction('approve')}
          disabled={busy !== null || patch.decision_status !== 'pending'}
        >
          {busy === 'approve' ? '提交中…' : '批准'}
        </button>
        <button
          className="btn-round"
          onClick={() => patchAction('apply')}
          disabled={busy !== null || patch.decision_status !== 'approved' || applied}
        >
          {busy === 'apply' ? '应用中…' : applied ? '已应用' : '应用'}
        </button>
      </div>
      {patch.decision_status === 'pending' && <p className="muted">先批准补丁，「应用」才会解锁。</p>}
      {patch.decision_status === 'approved' && !applied && <p className="muted">补丁已批准，再点「应用」写入隔离 Worktree。</p>}
      {applied && <p className="notice notice-ok">补丁已应用到隔离 Worktree，可以去「白名单测试」跑命令了。</p>}
    </div>
  );
}
