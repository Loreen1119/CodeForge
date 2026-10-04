import { useEffect, useState } from 'react';
import { apiFetch, errorMessage } from '../../../api/client';
import { CONNECTION_LABELS, TASK_STATUS_LABELS } from '../../../constants';
import type { NeedsContext, PlanView } from '../../../state/selectors';
import type { CodeTask, ConnectionStatus, Patch, TaskEvent, TaskTemplate, TestRun } from '../../../types';
import { distillTemplateBaseName, nextAvailableTemplateName } from '../../../utils/templateNaming';
import { PlanReviewSection } from '../plan/PlanReviewSection';
import { PatchReviewSection } from '../patch/PatchReviewSection';
import { TestPanel } from '../patch/TestPanel';
import { EventTimeline } from './EventTimeline';

type StepKey = 'plan' | 'patch' | 'test';
type StepState = 'done' | 'current' | 'todo';

const STEPS: { key: StepKey; label: string }[] = [
  { key: 'plan', label: '方案审查' },
  { key: 'patch', label: '补丁审查' },
  { key: 'test', label: '白名单测试' },
];

const STEP_STATE_TEXT: Record<StepState, string> = {
  done: '（已完成）',
  current: '（当前步骤）',
  todo: '（未开始）',
};

interface TaskWorkspaceProps {
  task: CodeTask;
  planView: PlanView;
  needsContext: NeedsContext | null;
  failure: string | null;
  patch: Patch | null;
  runs: TestRun[];
  allowedCommands: string[];
  events: TaskEvent[];
  connectionStatus: ConnectionStatus;
  streamError: string | null;
  onTaskUpdate: (task: CodeTask) => void;
  onPatchUpdate: (patch: Patch) => void;
  onRunCreated: (run: TestRun) => void;
  onSettled: () => void;
}

/**
 * 中栏工作区：状态条 → 步骤条 → 单区内容 → 可折叠时间线。
 * 关键点：一次只渲染一个步骤，页面高度不再随流程推进而无限增长。
 */
export function TaskWorkspace({
  task,
  planView,
  needsContext,
  failure,
  patch,
  runs,
  allowedCommands,
  events,
  connectionStatus,
  streamError,
  onTaskUpdate,
  onPatchUpdate,
  onRunCreated,
  onSettled,
}: TaskWorkspaceProps) {
  const planDone = task.plan_decision === 'approved';
  const applied = patch?.apply_status === 'applied';
  const testDone = runs.some((run) => run.status === 'passed');

  // 重分析进行中：status=pending 且 attempt>0。覆盖三个步骤的渲染，避免展示旧方案/旧补丁。
  // plan 10.1：防止对着 superseded 旧方案做决策（get_current_plan 只查 reviewed/approved，
  // 旧 plan 此时查不到，approve 会 409「当前没有可确认的 Plan」—— 用户看到一次莫名其妙的失败）。
  const isReanalyzing = task.status === 'pending' && task.attempt_number > 0;

  // 流程推进到哪一步，就默认把哪一步推到台前；用户手动点过的选择优先。
  // ② 的收尾动作是「应用」而不是「批准」：批准后就跳 ③，会把用户丢在一个「运行测试」还是灰的步骤上，
  // 还得自己退回去点应用。所以只有 applied 之后才推进到 ③。
  const suggested: StepKey = !patch ? (planDone ? 'patch' : 'plan') : applied ? 'test' : 'patch';
  const [selected, setSelected] = useState<StepKey | null>(null);
  useEffect(() => {
    setSelected(null);
  }, [suggested, task.id]);
  const step = selected ?? suggested;

  function stepState(key: StepKey): StepState {
    if (key === 'plan') return planDone ? 'done' : 'current';
    // 「批准」不等于完成：应用成功（写入 Worktree）才算这一步做完
    if (key === 'patch') return applied ? 'done' : patch ? 'current' : 'todo';
    return testDone ? 'done' : applied ? 'current' : 'todo';
  }

  const connectionText = CONNECTION_LABELS[connectionStatus];

  const [distilling, setDistilling] = useState(false);
  const [templateNotice, setTemplateNotice] = useState<string | null>(null);
  useEffect(() => {
    setTemplateNotice(null);
  }, [task.id]);

  /**
   * 把当前这次任务沉淀成模板：取任务上的参数快照，之后同类 Issue 可以直接调用。
   * 名字在本地让开（模板名在库里是 UNIQUE），避免"点第二次就报错"这种假故障。
   */
  async function handleDistill() {
    setDistilling(true);
    setTemplateNotice(null);
    try {
      const list = await apiFetch<TaskTemplate[]>('/api/templates');
      const name = nextAvailableTemplateName(distillTemplateBaseName(task.issue_title), list.map((item) => item.name));
      const created = await apiFetch<TaskTemplate>(
        `/api/templates/from-task/${task.id}?name=${encodeURIComponent(name)}`,
        { method: 'POST' },
      );
      setTemplateNotice(`已保存模板：${created.name}`);
    } catch (err) {
      setTemplateNotice(`沉淀模板失败：${errorMessage(err)}`);
    } finally {
      setDistilling(false);
    }
  }

  return (
    <div className="workspace">
      <div className="task-statusbar">
        <div className="task-statusbar-row">
          <h2>任务 #{task.id}</h2>
          <span className={`status status-${task.status}`} title={task.status}>
            {TASK_STATUS_LABELS[task.status] ?? task.status}
          </span>
          <span className="task-conn">
            <span
              className={`status-dot ${streamError || connectionStatus === 'error' ? 'error' : connectionStatus === 'connected' || connectionStatus === 'connecting' ? 'active' : ''}`}
              aria-hidden="true"
            />
            {connectionText}
          </span>
        </div>
        <p className="task-issue">{task.issue_title}</p>
        <div className="task-template-row">
          <button type="button" className="btn-round" disabled={distilling} onClick={handleDistill}>
            {distilling ? '沉淀中…' : '沉淀为模板'}
          </button>
          {templateNotice && <span className="muted" role="status">{templateNotice}</span>}
        </div>
        {failure && <p className="form-error" role="alert">任务失败：{failure}</p>}
        {streamError && <p className="form-error" role="alert">{streamError}</p>}
      </div>

      <div className="step-bar" role="tablist" aria-label="任务阶段">
        {STEPS.map((item, index) => {
          const state = stepState(item.key);
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={step === item.key}
              className={`step-item ${state}${step === item.key ? ' selected' : ''}`}
              onClick={() => setSelected(item.key)}
            >
              <span className="step-index">{index + 1}</span>
              <span className="step-label">
                {item.label}
                <span className="sr-only">{STEP_STATE_TEXT[state]}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="step-panel" role="tabpanel" aria-label={STEPS.find((item) => item.key === step)?.label}>
        {step === 'plan' && (isReanalyzing ? (
          <p className="muted step-empty">正在重新分析…（新方案到达后会自动出现在这里，无需刷新）</p>
        ) : (planView.plan || needsContext ? (
          <PlanReviewSection
            task={task}
            planView={planView}
            needsContext={needsContext}
            onTaskUpdate={onTaskUpdate}
            onSettled={onSettled}
          />
        ) : (
          <p className="muted step-empty">任务正在运行，方案生成后会自动出现在这里，无需刷新。</p>
        )))}

        {step === 'patch' && (isReanalyzing ? (
          <p className="muted step-empty">等待重新分析完成后查看新补丁。</p>
        ) : (patch ? (
          <PatchReviewSection
            taskId={task.id}
            patch={patch}
            onPatchUpdate={onPatchUpdate}
            onSettled={onSettled}
          />
        ) : (
          <p className="muted step-empty">
            还没有补丁。方案确认后会<strong>自动</strong>生成补丁（状态先到「正在生成补丁」），生成完成即出现在这里；
            也可以用外部工具提交（<code>POST /api/tasks/{task.id}/patches/validate</code>）。
          </p>
        )))}

        {step === 'test' && (isReanalyzing ? (
          <p className="muted step-empty">等待重新分析完成后运行测试。</p>
        ) : (patch ? (
          <TestPanel
            taskId={task.id}
            patchId={patch.id}
            applied={applied}
            runs={runs}
            allowedCommands={allowedCommands}
            onRunCreated={onRunCreated}
            onSettled={onSettled}
          />
        ) : (
          <p className="muted step-empty">还没有可测试的补丁。先在「补丁审查」里批准并应用补丁。</p>
        )))}
      </div>

      <EventTimeline events={events} />
    </div>
  );
}
