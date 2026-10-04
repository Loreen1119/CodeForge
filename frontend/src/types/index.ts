export type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error';
export interface Repository { id: number; name: string; local_path: string; default_branch: string; allowed_source_roots: string[]; allowed_test_commands: string[]; created_at: string; updated_at: string; }
export interface CodeTask { id: number; repository_id: number; issue_title: string; issue_body: string; status: string; current_agent?: string | null; attempt_number: number; plan_decision?: string | null; error_message?: string | null; template_id?: number | null; created_at: string; started_at?: string | null; finished_at?: string | null; }

// ---- 任务模板（字段与 backend/schemas.py 的 TemplateParams 逐项对齐）----
export type PatchProvider = 'auto' | 'fake' | 'deepseek';
export interface TemplateParams {
  task_type: 'bug_fix' | 'function_optimization' | 'validation' | 'unknown' | null;
  keywords: string[];
  acceptance_criteria: string[];
  risk_keywords: string[];
  max_keywords: number;
  max_acceptance_criteria: number;
  anchor_paths: string[];
  anchor_symbols: string[];
  search_aliases: Record<string, string>;
  search_keyword_budget: number;
  per_keyword_max_results: number;
  search_result_limit: number;
  max_proposed_changes: number;
  test_plan: string[];
  patch_provider: PatchProvider;
  patch_model: string | null;
  patch_max_retries: number;
  max_changed_lines: number;
  test_command: string | null;
  test_timeout_seconds: number;
}
export interface TaskTemplate {
  id: number;
  name: string;
  description: string;
  repository_id: number | null;
  issue_pattern: string;
  params: TemplateParams;
  source_task_id: number | null;
  use_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

// ---- 方案（PLAN_DRAFTED.plan / PLAN_REVIEWED.review）----
export interface ProposedChange { path: string; symbol?: string; reason: string; }
export type ImpactScope = 'single_file' | 'multiple_files' | 'unknown';
export type RiskLevel = 'low' | 'medium' | 'high';
export interface ChangePlanDTO {
  summary: string;
  proposed_changes: ProposedChange[];
  impact_scope: ImpactScope;
  test_plan: string[];
  risk_level: RiskLevel;
  missing_context: string[];
}
export interface PlanReviewDTO {
  approved: boolean;
  findings: string[];
  review_focus: string[];
  risk_level: RiskLevel;
}

export interface Patch { id:number; task_id:number; diff:string; changed_files:string[]; changed_lines:number; risk_level:string; approved:boolean; apply_status:string; decision_status:string; attempt_number?:number; security_findings?:string[]; validation_status?:string; }
export interface TestRun { id:number; task_id:number; patch_id:number; command:string; status:string; output:string; exit_code:number|null; timed_out:boolean; environment_error:string|null; }
export interface TaskEvent { event_seq: number; event_type: string; agent_name?: string | null; message?: string | null; payload: Record<string, unknown>; created_at: string; }
export interface LogEntry { id: number; type: 'system' | 'failed' | 'passed' | 'raw'; timestamp: string; phase: string; message: string; detail?: string; status?: 'ok' | 'err' | 'pending'; }
