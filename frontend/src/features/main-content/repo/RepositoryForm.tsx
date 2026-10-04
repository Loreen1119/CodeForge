import { useState } from 'react';
import type { FormEvent } from 'react';
import { apiFetch, errorMessage } from '../../../api/client';
import type { Repository } from '../../../types';

const EMPTY_FORM = {
  name: '',
  local_path: '',
  default_branch: 'main',
  allowed_source_roots: 'src',
  allowed_test_commands: '',
};

function splitList(value: string, separator: string): string[] {
  return value.split(separator).map((item) => item.trim()).filter(Boolean);
}

interface RepositoryFormProps {
  onCreated: (repository: Repository) => void;
}

export function RepositoryForm({ onCreated }: RepositoryFormProps) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await apiFetch<Repository>('/api/repositories', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name.trim(),
          local_path: form.local_path.trim(),
          default_branch: form.default_branch.trim() || 'main',
          allowed_source_roots: splitList(form.allowed_source_roots, ','),
          allowed_test_commands: splitList(form.allowed_test_commands, '\n'),
        }),
      });
      onCreated(created);
      setForm(EMPTY_FORM);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      {error && <p className="form-error" role="alert">{error}</p>}
      <label className="sr-only" htmlFor="repo-name">仓库名称</label>
      <input id="repo-name" placeholder="仓库名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
      <label className="sr-only" htmlFor="repo-path">本地路径</label>
      <input id="repo-path" placeholder="本地路径" value={form.local_path} onChange={(e) => setForm({ ...form, local_path: e.target.value })} required />
      <label className="sr-only" htmlFor="repo-branch">默认分支</label>
      <input id="repo-branch" placeholder="默认分支" value={form.default_branch} onChange={(e) => setForm({ ...form, default_branch: e.target.value })} />
      <label className="sr-only" htmlFor="repo-roots">源码目录（逗号分隔）</label>
      <input id="repo-roots" placeholder="源码目录（逗号分隔）" value={form.allowed_source_roots} onChange={(e) => setForm({ ...form, allowed_source_roots: e.target.value })} required />
      <label className="sr-only" htmlFor="repo-commands">白名单测试命令（每行一条）</label>
      <textarea id="repo-commands" placeholder="白名单测试命令（每行一条）" value={form.allowed_test_commands} onChange={(e) => setForm({ ...form, allowed_test_commands: e.target.value })} />
      <button type="submit" disabled={submitting}>{submitting ? '保存中…' : '保存仓库'}</button>
    </form>
  );
}
