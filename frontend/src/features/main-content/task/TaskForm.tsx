import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { apiFetch, errorMessage } from '../../../api/client';
import type { CodeTask, Repository, TaskTemplate } from '../../../types';

interface TaskFormProps {
  repositories: Repository[];
  /** 弹层打开时的默认仓库：通常用当前选中任务的仓库，或列表第一个 */
  defaultRepositoryId?: number | null;
  onCreated: (task: CodeTask) => void;
}

export function TaskForm({ repositories, defaultRepositoryId, onCreated }: TaskFormProps) {
  const initialRepoId = defaultRepositoryId != null && repositories.some((r) => r.id === defaultRepositoryId)
    ? String(defaultRepositoryId)
    : (repositories[0]?.id != null ? String(repositories[0].id) : '');
  const [repositoryId, setRepositoryId] = useState(initialRepoId);
  const [issue, setIssue] = useState({ title: '', body: '' });
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [templateId, setTemplateId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 模板列表拿不到不该挡住建任务：失败就当"当前没有可用模板"，照常走默认链路。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await apiFetch<TaskTemplate[]>('/api/templates');
        if (!cancelled) setTemplates(list);
      } catch {
        if (!cancelled) setTemplates([]);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // 只给通用模板与当前仓库的模板：绑定到别的仓库的模板会被后端 422 挡下，
  // 与其让用户选了再报错，不如一开始就不列出来。
  const usableTemplates = templates.filter(
    (item) => item.repository_id === null || String(item.repository_id) === repositoryId,
  );
  const effectiveTemplateId = usableTemplates.some((item) => String(item.id) === templateId) ? templateId : '';

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const task = await apiFetch<CodeTask>('/api/tasks', {
        method: 'POST',
        body: JSON.stringify({
          repository_id: Number(repositoryId),
          issue_title: issue.title.trim(),
          issue_body: issue.body.trim(),
          ...(effectiveTemplateId ? { template_id: Number(effectiveTemplateId) } : {}),
        }),
      });
      onCreated(task);
      setIssue({ title: '', body: '' });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      {error && <p className="form-error" role="alert">{error}</p>}
      <label className="sr-only" htmlFor="task-repo">选择仓库</label>
      <select id="task-repo" value={repositoryId} onChange={(e) => setRepositoryId(e.target.value)} required disabled={!repositories.length}>
        <option value="" disabled>选择仓库</option>
        {repositories.map((repo) => (
          <option key={repo.id} value={repo.id}>{repo.name}</option>
        ))}
      </select>
      {usableTemplates.length > 0 && (
        <>
          <label className="sr-only" htmlFor="task-template">任务模板</label>
          <select id="task-template" value={effectiveTemplateId} onChange={(e) => setTemplateId(e.target.value)}>
            <option value="">不使用模板（走默认链路参数）</option>
            {usableTemplates.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}{item.use_count > 0 ? `（已调用 ${item.use_count} 次）` : ''}
              </option>
            ))}
          </select>
        </>
      )}
      <label className="sr-only" htmlFor="task-title">Issue 标题</label>
      <input id="task-title" placeholder="Issue 标题" value={issue.title} onChange={(e) => setIssue({ ...issue, title: e.target.value })} required />
      <label className="sr-only" htmlFor="task-body">Issue 描述</label>
      <textarea id="task-body" placeholder="Issue 描述" value={issue.body} onChange={(e) => setIssue({ ...issue, body: e.target.value })} required />
      <button type="submit" disabled={submitting || !repositories.length || !repositoryId}>
        {submitting ? '创建中…' : '创建任务'}
      </button>
    </form>
  );
}
