import type { ConnectionStatus, ImpactScope, RiskLevel } from '../types';

/** 与 backend/constants.py 的 REQUIRED_TASK_STATUSES 逐字对齐（16 态） */
export const TASK_STATUSES = [
  'pending',
  'running',
  'needs_context',
  'awaiting_plan_approval',
  'generating_patch',
  'awaiting_patch_generation_retry',
  'awaiting_patch_approval',
  'applying',
  'awaiting_test',
  'testing',
  'succeeded',
  'test_failed',
  'test_timeout',
  'environment_failed',
  'failed',
  'cancelled',
] as const;

/** 后端实际会发出的事件（2026-09-14 核对 main.py + task_service.py） */
export const TASK_EVENT_TYPES = [
  'TASK_CREATED',
  'TASK_STARTED',
  'REQUIREMENT_ANALYZED',
  'CODE_SEARCHED',
  'PLAN_DRAFTED',
  'PLAN_REVIEWED',
  'NEEDS_CONTEXT',
  'TASK_READY',
  'PLAN_APPROVED',
  'PLAN_REJECTED',
  'PLAN_REANALYSIS_REQUESTED',
  'PATCH_GENERATION_STARTED',
  'PATCH_GENERATED',
  'PATCH_GENERATION_FAILED',
  'PATCH_VALIDATED',
  'PATCH_APPROVED',
  'PATCH_REJECTED',
  'PATCH_APPLIED',
  'PATCH_APPLY_FAILED',
  'TEST_RUN_COMPLETED',
  'TASK_CANCELLED',
  'TASK_RECOVERED',
  'TASK_FAILED',
] as const;

/**
 * 收到这些事件即认为本轮结束、可以关闭 SSE。
 * 对应后端 constants.py 的 SSE_CLOSE_STATES = WAIT_HUMAN ∪ TERMINAL。
 */
export const TERMINAL_EVENT_TYPES: readonly string[] = [
  'NEEDS_CONTEXT',            // → needs_context
  'TASK_READY',               // → awaiting_plan_approval
  'PATCH_GENERATED',          // → awaiting_patch_approval
  'PATCH_GENERATION_FAILED',  // → awaiting_patch_generation_retry
  'TASK_FAILED',              // → failed
  'TASK_CANCELLED',           // → cancelled
  'TASK_RECOVERED',           // → failed（服务重启回收僵尸任务）
];

export const EVENT_LABELS: Record<string, string> = {
  TASK_CREATED: '任务已创建',
  TASK_STARTED: '开始分析',
  REQUIREMENT_ANALYZED: '需求已解析',
  CODE_SEARCHED: '代码检索完成',
  PLAN_DRAFTED: '方案已生成',
  PLAN_REVIEWED: '自动审查完成',
  NEEDS_CONTEXT: '上下文不足，需补充',
  TASK_READY: '方案待确认',
  PLAN_APPROVED: '方案已确认',
  PLAN_REJECTED: '方案已驳回',
  PLAN_REANALYSIS_REQUESTED: '已请求重新分析',
  PATCH_GENERATION_STARTED: '正在生成补丁',
  PATCH_GENERATED: '补丁已生成，待确认',
  PATCH_GENERATION_FAILED: '补丁生成失败，待决定',
  PATCH_VALIDATED: '补丁校验完成',
  PATCH_APPROVED: '补丁已批准',
  PATCH_REJECTED: '补丁已驳回',
  PATCH_APPLIED: '补丁已应用',
  PATCH_APPLY_FAILED: '补丁应用失败',
  TEST_RUN_COMPLETED: '测试执行完成',
  TASK_CANCELLED: '任务已取消',
  TASK_RECOVERED: '服务重启，任务已回收',
  TASK_FAILED: '任务失败',
};

/** 状态徽标的中文文案；原始英文状态仍保留在 title 里便于排查 */
export const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '待启动',
  running: '执行中',
  needs_context: '需补充上下文',
  awaiting_plan_approval: '等待方案确认',
  generating_patch: '正在生成补丁',
  awaiting_patch_generation_retry: '补丁生成失败，待决定',
  awaiting_patch_approval: '等待补丁确认',
  applying: '正在应用补丁',
  awaiting_test: '等待执行测试',
  testing: '测试执行中',
  succeeded: '已完成',
  test_failed: '测试未通过',
  test_timeout: '测试超时',
  environment_failed: '环境异常',
  failed: '失败',
  cancelled: '已取消',
};

/**
 * 任务停在这些状态时事件流已经关闭，增量只能靠客户端轮询兜住
 * （对应后端 WAIT_HUMAN，5 个人工等待态）。
 */
export const HUMAN_WAIT_STATUSES: readonly string[] = [
  'needs_context',
  'awaiting_plan_approval',
  'awaiting_patch_generation_retry',
  'awaiting_patch_approval',
  'awaiting_test',
];

/** 与 backend/constants.py 的 TERMINAL 对齐：任务进入这些状态后不再需要轮询 */
export const TERMINAL_STATUSES: readonly string[] = [
  'succeeded',
  'test_failed',
  'test_timeout',
  'environment_failed',
  'failed',
  'cancelled',
];

/** 事件流连接状态的文案（状态条与右栏日志面板共用一份） */
export const CONNECTION_LABELS: Record<ConnectionStatus, string> = {
  idle: '等待任务',
  connecting: '正在连接事件流…',
  connected: '任务执行中…',
  error: '连接中断，正在重连…',
  disconnected: '任务已结束',
};

export const IMPACT_SCOPE_LABELS: Record<ImpactScope, string> = {
  single_file: '单文件',
  multiple_files: '多文件',
  unknown: '范围未知',
};

export const RISK_LABELS: Record<RiskLevel, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
};
