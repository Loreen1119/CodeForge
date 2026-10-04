import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { AgentLogPanel } from './features/sidebar-right/AgentLogPanel';
import { WorkspaceSidebar } from './features/sidebar-left/WorkspaceSidebar';
import { AppTopbar } from './features/topbar/AppTopbar';
import { Modal } from './components/Modal';
import { TaskWorkspace } from './features/main-content/task/TaskWorkspace';
import { RepositoryForm } from './features/main-content/repo/RepositoryForm';
import { TaskForm } from './features/main-content/task/TaskForm';
import { useTaskStream } from './features/task/useTaskStream';
import { apiFetch, errorMessage } from './api/client';
import { appReducer, initialAppState } from './state/appReducer';
import { getFailureMessage, getLatestPatchId, getNeedsContext, getPlanView } from './state/selectors';
import { TERMINAL_STATUSES } from './constants';
import type { CodeTask, Patch, Repository, TaskEvent, TestRun } from './types';

/**
 * 事件同步间隔。
 * 事件流只在任务运行期有效（收到终态事件即关闭），之后由后台或外部脚本产生的事件
 * （PLAN_APPROVED / PATCH_VALIDATED / PATCH_APPLIED / TEST_RUN_COMPLETED）只能靠客户端补拉。
 */
const RESYNC_INTERVAL_MS = 5000;

export default function App() {
  const [state, dispatch] = useReducer(appReducer, initialAppState);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [tasks, setTasks] = useState<CodeTask[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [patch, setPatch] = useState<Patch | null>(null);
  const [runs, setRuns] = useState<TestRun[]>([]);
  // 窄屏（<760px）主区与日志区二选一，桌面端两者同屏（由 CSS 控制）
  const [mobileView, setMobileView] = useState<'task' | 'log'>('task');
  const [unseenLogs, setUnseenLogs] = useState(0);
  // 窄屏（<1000px）左栏收成抽屉
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [dialog, setDialog] = useState<'repo' | 'task' | null>(null);

  // 日志在后台增长时累计未读，切到日志视图即清零
  const seenLogCount = useRef(0);
  useEffect(() => {
    if (mobileView === 'log') setUnseenLogs(0);
  }, [mobileView]);
  useEffect(() => {
    const added = state.logs.length - seenLogCount.current;
    seenLogCount.current = state.logs.length;
    if (added > 0 && mobileView !== 'log') setUnseenLogs((count) => count + added);
  }, [state.logs.length, mobileView]);

  const task = state.task;
  const taskId = task ? task.id : null;
  const taskStatus = task ? task.status : null;
  const streamError = state.streamError;

  useTaskStream(taskId, task ? task.attempt_number : 0, dispatch);

  // 首屏：仓库 + 任务列表。任务列表非空时自动选中最近一个 —— 刷新不再把任务弄丢
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await apiFetch<Repository[]>('/api/repositories');
        if (!cancelled) setRepositories(list);
      } catch (err) {
        if (!cancelled) setLoadError(errorMessage(err));
      }
    })();
    (async () => {
      try {
        const list = await apiFetch<CodeTask[]>('/api/tasks');
        if (cancelled) return;
        const sorted = [...list].sort((a, b) => b.id - a.id);
        setTasks(sorted);
        if (sorted.length > 0) dispatch({ type: 'TASK_SELECTED', payload: sorted[0] });
      } catch (err) {
        if (!cancelled) setLoadError(errorMessage(err));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // 当前任务的最新快照回流到列表，省掉一次列表请求
  useEffect(() => {
    if (!task) return;
    setTasks((list) => list.map((item) => (item.id === task.id ? { ...item, ...task } : item)));
  }, [task]);

  const syncEvents = useCallback(async () => {
    if (taskId === null) return;
    try {
      // 事件流只在任务"运行期"有效；之后由后台异步推进的步骤（生成补丁 / 应用 / 测试）
      // 产生的事件 payload 不带 status，光靠事件推不动状态徽标 —— 所以顺带拉一次任务快照。
      const [events, latest] = await Promise.all([
        apiFetch<TaskEvent[]>(`/api/tasks/${taskId}/events`),
        apiFetch<CodeTask>(`/api/tasks/${taskId}`),
      ]);
      dispatch({ type: 'REPLAY', payload: events });
      dispatch({ type: 'TASK_UPDATED', payload: latest });
    } catch {
      // 连接类提示已由事件流与状态条负责，这里静默重试
    }
  }, [taskId]);

  // 只要任务还没进终态就低频同步：既兜住"等人操作"的增量，也兜住后台异步推进
  // （生成补丁 / 应用 / 测试）—— 后者的事件 payload 不带 status，必须靠任务快照拉回状态。
  useEffect(() => {
    if (taskId === null) return;
    const needsPolling = streamError !== null || (taskStatus !== null && !TERMINAL_STATUSES.includes(taskStatus));
    if (!needsPolling) return;
    void syncEvents();
    const timer = window.setInterval(() => { void syncEvents(); }, RESYNC_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [taskId, taskStatus, streamError, syncEvents]);

  // 补丁就绪（PATCH_GENERATED 或 PATCH_VALIDATED）后拉取补丁与测试记录
  const patchId = getLatestPatchId(state.events);
  useEffect(() => {
    if (taskId === null || patchId === null) return;
    let cancelled = false;
    (async () => {
      try {
        const [latestPatch, testRuns] = await Promise.all([
          apiFetch<Patch>(`/api/tasks/${taskId}/patches/${patchId}`),
          apiFetch<TestRun[]>(`/api/tasks/${taskId}/test-runs`),
        ]);
        if (cancelled) return;
        setPatch(latestPatch);
        setRuns(testRuns);
      } catch {
        // 事件流里已有提示，这里不重复打扰
      }
    })();
    return () => { cancelled = true; };
  }, [taskId, patchId]);

  // 切任务时先清掉上一个任务的补丁/测试，等同步结果回来再填
  useEffect(() => {
    setPatch(null);
    setRuns([]);
  }, [taskId]);

  function handleTaskCreated(created: CodeTask) {
    setTasks((list) => [created, ...list.filter((item) => item.id !== created.id)]);
    setDialog(null);
    dispatch({ type: 'TASK_CREATED', payload: created });
  }

  function handleRepoCreated(repo: Repository) {
    setRepositories((list) => [repo, ...list.filter((item) => item.id !== repo.id)]);
    setDialog(null);
  }

  function handleSelectTask(next: CodeTask) {
    setSidebarOpen(false);
    if (next.id === taskId) return;
    dispatch({ type: 'TASK_SELECTED', payload: next });
  }

  const planView = getPlanView(state.events);
  const needsContext = getNeedsContext(state.events);
  const failure = getFailureMessage(state.events);
  const allowedCommands = useMemo(
    () => (task ? repositories.find((item) => item.id === task.repository_id)?.allowed_test_commands ?? [] : []),
    [task?.repository_id, repositories],
  );

  return (
    <div className="app-container" data-mobile-view={mobileView} data-sidebar={sidebarOpen ? 'open' : 'closed'}>
      <a className="skip-link" href="#main-content">跳到主内容</a>

      <AppTopbar
        sidebarOpen={sidebarOpen}
        canCreateTask={repositories.length > 0}
        onToggleSidebar={() => setSidebarOpen((open) => !open)}
        onOpenRepoForm={() => setDialog('repo')}
        onOpenTaskForm={() => setDialog('task')}
      />

      <nav className="mobile-tabs" aria-label="视图切换">
        <button type="button" aria-pressed={mobileView === 'task'} onClick={() => setMobileView('task')}>任务</button>
        <button type="button" aria-pressed={mobileView === 'log'} onClick={() => setMobileView('log')}>
          执行日志{unseenLogs > 0 && <span className="tab-badge">{unseenLogs}</span>}
        </button>
      </nav>

      <WorkspaceSidebar
        repositories={repositories}
        tasks={tasks}
        currentTaskId={taskId}
        loadError={loadError}
        onSelectTask={handleSelectTask}
      />
      {sidebarOpen && (
        <button type="button" className="sidebar-backdrop" aria-label="关闭工作区面板" onClick={() => setSidebarOpen(false)} />
      )}

      <main className="main-content" id="main-content">
        <div className="workspace-inner">
          {task ? (
            <TaskWorkspace
              task={task}
              planView={planView}
              needsContext={needsContext}
              failure={failure}
              patch={patch}
              runs={runs}
              allowedCommands={allowedCommands}
              events={state.events}
              connectionStatus={state.connectionStatus}
              streamError={state.streamError}
              onTaskUpdate={(updated) => dispatch({ type: 'TASK_UPDATED', payload: updated })}
              onPatchUpdate={setPatch}
              onRunCreated={(run) => setRuns((list) => [...list, run])}
              onSettled={syncEvents}
            />
          ) : (
            <section className="empty-workspace">
              <h2>还没有选中的任务</h2>
              <p className="muted">从左侧挑一个历史任务，或者新建一个代码任务开始。</p>
              <div className="button-row">
                <button type="button" className="btn-round-primary" disabled={repositories.length === 0} onClick={() => setDialog('task')}>新建任务</button>
                <button type="button" className="btn-round" onClick={() => setDialog('repo')}>配置仓库</button>
              </div>
            </section>
          )}
        </div>
      </main>

      <aside className="right-sidebar">
        <AgentLogPanel logs={state.logs} connectionStatus={state.connectionStatus} collapsed={false} notice={state.streamError} />
      </aside>

      {dialog === 'repo' && (
        <Modal title="配置本地仓库" onClose={() => setDialog(null)}>
          <RepositoryForm onCreated={handleRepoCreated} />
        </Modal>
      )}
      {dialog === 'task' && (
        <Modal title="创建代码任务" onClose={() => setDialog(null)}>
          <TaskForm repositories={repositories} defaultRepositoryId={task?.repository_id ?? repositories[0]?.id ?? null} onCreated={handleTaskCreated} />
        </Modal>
      )}
    </div>
  );
}
