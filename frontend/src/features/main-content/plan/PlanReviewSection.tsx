import { useState } from 'react';
import { apiFetch, errorMessage } from '../../../api/client';
import { IMPACT_SCOPE_LABELS, RISK_LABELS } from '../../../constants';
import type { CodeTask } from '../../../types';
import type { NeedsContext, PlanView } from '../../../state/selectors';
import { FeedbackPanel } from './FeedbackPanel';

interface PlanReviewSectionProps {
  task: CodeTask;
  planView: PlanView;
  needsContext: NeedsContext | null;
  onTaskUpdate: (task: CodeTask) => void;
  /** 决策会产生 PLAN_APPROVED / PLAN_REJECTED 等事件，交由上层同步事件日志 */
  onSettled: () => void;
}

/**
 * 方案审查：方案区 → 自动审查区 → 操作区（三态，D3：待决策 / 已驳回待反馈 / 已确认）。
 * 状态全部来自 props（task.status + task.plan_decision），组件内只保留交互瞬时态。
 */
export function PlanReviewSection({ task, planView, needsContext, onTaskUpdate, onSettled }: PlanReviewSectionProps) {
  const { plan, review } = planView;
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [approvingWithReason, setApprovingWithReason] = useState(false);
  const [reason, setReason] = useState('');

  if (!plan && !needsContext) return null;

  const autoReviewPassed = review ? review.approved && review.findings.length === 0 : false;

  async function decide(action: 'approve' | 'reject', body: Record<string, unknown>) {
    setSubmitting(true);
    setError(null);
    try {
      const updated = await apiFetch<CodeTask>(`/api/tasks/${task.id}/plan/${action}`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      onTaskUpdate(updated);
      onSettled();
      setRejecting(false);
      setApprovingWithReason(false);
      setReason('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function submitReanalysis(feedback: string) {
    const updated = await apiFetch<CodeTask>(`/api/tasks/${task.id}/plan/reanalyze`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    });
    onTaskUpdate(updated);
    onSettled();
  }

  function renderActions() {
    if (task.status === 'needs_context') {
      return (
        <FeedbackPanel
          title="上下文不足，需要补充信息"
          findings={needsContext?.findings ?? []}
          missingContext={needsContext?.missingContext ?? []}
          onSubmit={submitReanalysis}
        />
      );
    }

    if (task.plan_decision === 'rejected') {
      return (
        <FeedbackPanel
          title="方案已驳回，补充理由后重新分析"
          findings={review?.findings ?? []}
          hint="提交反馈会触发整条流水线重跑，并生成新的方案。"
          onSubmit={submitReanalysis}
        />
      );
    }

    if (task.plan_decision === 'approved') {
      return <p className="notice notice-ok">方案已确认，等待补丁验证（PATCH_VALIDATED）</p>;
    }

    if (rejecting) {
      return (
        <div className="plan-reject">
          <label className="sr-only" htmlFor="reject-reason">驳回理由（必填）</label>
          <textarea id="reject-reason" placeholder="驳回理由（必填）" value={reason} onChange={(e) => setReason(e.target.value)} required />
          <div className="button-row">
            <button className="btn-round-danger" disabled={submitting || !reason.trim()} onClick={() => decide('reject', { reason: reason.trim() })}>
              {submitting ? '提交中…' : '确认驳回'}
            </button>
            <button type="button" disabled={submitting} onClick={() => { setRejecting(false); setReason(''); }}>取消</button>
          </div>
        </div>
      );
    }

    if (approvingWithReason) {
      return (
        <div className="plan-approve-reason">
          <label className="sr-only" htmlFor="override-reason">放行理由（必填）</label>
          <textarea id="override-reason" placeholder="放行理由（必填）" value={reason} onChange={(e) => setReason(e.target.value)} required />
          <div className="button-row">
            <button className="btn-round-primary" disabled={submitting || !reason.trim()} onClick={() => decide('approve', { reason: reason.trim() })}>
              {submitting ? '提交中…' : '确认批准'}
            </button>
            <button type="button" disabled={submitting} onClick={() => { setApprovingWithReason(false); setReason(''); }}>取消</button>
          </div>
        </div>
      );
    }

    return (
      <div className="button-row">
        <button className="btn-round-primary" disabled={submitting} onClick={() => !autoReviewPassed ? setApprovingWithReason(true) : decide('approve', {})}>
          {submitting ? '提交中…' : '批准方案'}
        </button>
        <button className="btn-round" disabled={submitting} onClick={() => setRejecting(true)}>驳回方案</button>
      </div>
    );
  }

  return (
    <div className="review-panel">
      {plan && (
        <div className="plan-block">
          <p className="plan-summary">{plan.summary}</p>
          <div className="badge-row">
            <span className={`badge risk-${plan.risk_level}`}>{RISK_LABELS[plan.risk_level] ?? plan.risk_level}</span>
            <span className="badge">{IMPACT_SCOPE_LABELS[plan.impact_scope] ?? plan.impact_scope}</span>
            <span className="badge">改动 {plan.proposed_changes.length} 处</span>
          </div>

          <h4>拟修改</h4>
          <ul className="change-list">
            {plan.proposed_changes.map((change, index) => (
              <li key={`${change.path}-${index}`}>
                <code>{change.path}</code>
                {change.symbol && <span className="muted"> · {change.symbol}</span>}
                <p className="muted">{change.reason}</p>
              </li>
            ))}
          </ul>

          {plan.test_plan.length > 0 && (
            <>
              <h4>验证计划</h4>
              <ol className="test-plan">
                {plan.test_plan.map((item, index) => <li key={`test-${index}`}>{item}</li>)}
              </ol>
            </>
          )}

          {plan.missing_context.length > 0 && (
            <>
              <h4>缺失上下文</h4>
              <ul className="finding-list">{plan.missing_context.map((item, index) => <li key={`ctx-${index}`}>{item}</li>)}</ul>
            </>
          )}
        </div>
      )}

      {review && (
        <div className="review-block">
          <h4>自动审查</h4>
          {autoReviewPassed ? (
            <p className="notice notice-ok">自动审查通过</p>
          ) : (
            <>
              <span className="badge risk-medium">发现 {review.findings.length} 项问题</span>
              <ul className="finding-list">
                {review.findings.map((item, index) => <li key={`review-${index}`}>{item}</li>)}
              </ul>
            </>
          )}
        </div>
      )}

      {error && <p className="form-error" role="alert">{error}</p>}
      {renderActions()}
    </div>
  );
}
