import { useState } from 'react';
import type { FormEvent } from 'react';
import { errorMessage } from '../../../api/client';

interface FeedbackPanelProps {
  title: string;
  findings: string[];
  missingContext?: string[];
  hint?: string;
  onSubmit: (feedback: string) => Promise<void>;
}

/** needs_context 与「驳回后待反馈」共用（D4）：形态相同——问题清单 + 反馈输入 + 重新分析 */
export function FeedbackPanel({ title, findings, missingContext = [], hint, onSubmit }: FeedbackPanelProps) {
  const [feedback, setFeedback] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!feedback.trim()) {
      setError('请填写反馈内容后再重新分析');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(feedback.trim());
      setFeedback('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="feedback-panel">
      <h4>{title}</h4>
      {hint && <p className="muted">{hint}</p>}
      {findings.length > 0 && (
        <ul className="finding-list">
          {findings.map((item, index) => <li key={`finding-${index}`}>{item}</li>)}
        </ul>
      )}
      {missingContext.length > 0 && (
        <div className="missing-context">
          <strong>缺少的上下文</strong>
          <ul className="finding-list">
            {missingContext.map((item, index) => <li key={`missing-${index}`}>{item}</li>)}
          </ul>
        </div>
      )}
      <form onSubmit={handleSubmit}>
        {error && <p className="form-error" role="alert">{error}</p>}
        <label className="sr-only" htmlFor="feedback-input">补充上下文 / 驳回理由（必填）</label>
        <textarea
          id="feedback-input"
          placeholder="补充上下文 / 驳回理由（必填）"
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          required
        />
        <button type="submit" className="btn-round-primary" disabled={submitting || !feedback.trim()}>{submitting ? '提交中…' : '提交反馈并重新分析'}</button>
      </form>
    </div>
  );
}
