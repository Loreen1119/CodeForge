import { TASK_STATUS_LABELS } from '../../constants';
import type { CodeTask, Repository } from '../../types';

interface WorkspaceSidebarProps {
  repositories: Repository[];
  tasks: CodeTask[];
  currentTaskId: number | null;
  loadError: string | null;
  onSelectTask: (task: CodeTask) => void;
}

/**
 * 左栏＝仓库 + 任务列表。
 * 任务列表是「刷新不丢任务」的关键：不再只认最后一次 TASK_CREATED 的那一个。
 */
export function WorkspaceSidebar({ repositories, tasks, currentTaskId, loadError, onSelectTask }: WorkspaceSidebarProps) {
  return (
    <aside className="left-sidebar" id="workspace-sidebar" aria-label="仓库与任务">
      <div className="sidebar-block">
        <h2 className="sidebar-title">仓库 <span className="badge">{repositories.length}</span></h2>
        {repositories.length === 0 && <p className="muted">尚未配置仓库</p>}
        {repositories.map((repo) => (
          <div className="repo-item" key={repo.id}>
            <strong>{repo.name}</strong>
            <small title={repo.local_path}>{repo.local_path}</small>
          </div>
        ))}
      </div>

      <div className="sidebar-block">
        <h2 className="sidebar-title">任务 <span className="badge">{tasks.length}</span></h2>
        {tasks.length === 0 && <p className="muted">尚未创建任务</p>}
        <ul className="task-list">
          {tasks.map((task) => (
            <li key={task.id}>
              <button
                type="button"
                className="task-item"
                aria-current={task.id === currentTaskId}
                onClick={() => onSelectTask(task)}
              >
                <span className="task-item-id">#{task.id}</span>
                <span className="task-item-title">{task.issue_title}</span>
                <span className={`status status-${task.status}`} title={task.status}>
                  {TASK_STATUS_LABELS[task.status] ?? task.status}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {loadError && <p className="form-error" role="alert">加载失败：{loadError}</p>}
      <p className="sidebar-hint">后台或外部脚本产生的事件会自动同步到主区，无需手动回放。</p>
    </aside>
  );
}
