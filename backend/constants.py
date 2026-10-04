"""State constants and groupings for the state machine."""

# 16 task states
PENDING = 'pending'
RUNNING = 'running'
NEEDS_CONTEXT = 'needs_context'
AWAITING_PLAN_APPROVAL = 'awaiting_plan_approval'
GENERATING_PATCH = 'generating_patch'
AWAITING_PATCH_GENERATION_RETRY = 'awaiting_patch_generation_retry'
AWAITING_PATCH_APPROVAL = 'awaiting_patch_approval'
APPLYING = 'applying'
AWAITING_TEST = 'awaiting_test'
TESTING = 'testing'
SUCCEEDED = 'succeeded'
TEST_FAILED = 'test_failed'
TEST_TIMEOUT = 'test_timeout'
ENVIRONMENT_FAILED = 'environment_failed'
FAILED = 'failed'
CANCELLED = 'cancelled'

# Ordered list of all task states (used for CHECK constraint)
REQUIRED_TASK_STATUSES = [
    PENDING, RUNNING, NEEDS_CONTEXT, AWAITING_PLAN_APPROVAL,
    GENERATING_PATCH, AWAITING_PATCH_GENERATION_RETRY, AWAITING_PATCH_APPROVAL,
    APPLYING, AWAITING_TEST, TESTING, SUCCEEDED, TEST_FAILED, TEST_TIMEOUT,
    ENVIRONMENT_FAILED, FAILED, CANCELLED
]

# SSE grouping: states where the stream should stay open (async work in progress)
IN_FLIGHT = {PENDING, RUNNING, GENERATING_PATCH, APPLYING, TESTING}

# SSE grouping: states where human action is needed (stream should close)
WAIT_HUMAN = {NEEDS_CONTEXT, AWAITING_PLAN_APPROVAL, AWAITING_PATCH_GENERATION_RETRY,
              AWAITING_PATCH_APPROVAL, AWAITING_TEST}

# SSE grouping: terminal states (stream should close)
TERMINAL = {SUCCEEDED, TEST_FAILED, TEST_TIMEOUT, ENVIRONMENT_FAILED, FAILED, CANCELLED}

# Verify no overlap and complete coverage
assert len(IN_FLIGHT & WAIT_HUMAN) == 0, "IN_FLIGHT and WAIT_HUMAN should not overlap"
assert len(IN_FLIGHT & TERMINAL) == 0, "IN_FLIGHT and TERMINAL should not overlap"
assert len(WAIT_HUMAN & TERMINAL) == 0, "WAIT_HUMAN and TERMINAL should not overlap"
assert IN_FLIGHT | WAIT_HUMAN | TERMINAL == set(REQUIRED_TASK_STATUSES), \
    "SSE groups must cover all states exactly once"

# For SSE: stream closes when entering these states
SSE_CLOSE_STATES = WAIT_HUMAN | TERMINAL

# Plan statuses
PLAN_DRAFT = 'draft'
PLAN_REVIEWED = 'reviewed'
PLAN_APPROVED = 'approved'
PLAN_REJECTED = 'rejected'
PLAN_SUPERSEDED = 'superseded'

REQUIRED_PLAN_STATUSES = [PLAN_DRAFT, PLAN_REVIEWED, PLAN_APPROVED, PLAN_REJECTED, PLAN_SUPERSEDED]

# Patch apply statuses
PATCH_NOT_APPLIED = 'not_applied'
PATCH_APPLIED = 'applied'
PATCH_APPLY_FAILED = 'apply_failed'
PATCH_SYNTAX_FAILED = 'syntax_failed'

REQUIRED_PATCH_APPLY_STATUSES = [PATCH_NOT_APPLIED, PATCH_APPLIED, PATCH_APPLY_FAILED, PATCH_SYNTAX_FAILED]

# Patch apply failure reasons
APPLY_FAILURE_WORKTREE_CREATE_FAILED = 'worktree_create_failed'
APPLY_FAILURE_WORKTREE_EXISTS = 'worktree_exists'
APPLY_FAILURE_GIT_APPLY_FAILED = 'git_apply_failed'
APPLY_FAILURE_SYNTAX_FAILED = 'syntax_failed'
APPLY_FAILURE_BASE_COMMIT_CHANGED = 'base_commit_changed'

# Patch decision statuses
PATCH_DECISION_PENDING = 'pending'
PATCH_DECISION_APPROVED = 'approved'
PATCH_DECISION_REJECTED = 'rejected'
PATCH_DECISION_INVALID = 'invalid'
PATCH_DECISION_SUPERSEDED = 'superseded'

REQUIRED_PATCH_DECISION_STATUSES = [
    PATCH_DECISION_PENDING, PATCH_DECISION_APPROVED, PATCH_DECISION_REJECTED,
    PATCH_DECISION_INVALID, PATCH_DECISION_SUPERSEDED
]

# Plan decision
PLAN_DECISION_APPROVED = 'approved'
PLAN_DECISION_REJECTED = 'rejected'

# Test run statuses
TEST_RUN_PASSED = 'passed'
TEST_RUN_FAILED = 'failed'
TEST_RUN_TIMED_OUT = 'timed_out'
TEST_RUN_ENVIRONMENT_ERROR = 'environment_error'
TEST_RUN_INTERRUPTED = 'interrupted'

REQUIRED_TEST_RUN_STATUSES = [
    TEST_RUN_PASSED, TEST_RUN_FAILED, TEST_RUN_TIMED_OUT,
    TEST_RUN_ENVIRONMENT_ERROR, TEST_RUN_INTERRUPTED
]

# Validation statuses
VALIDATION_VALID = 'valid'
VALIDATION_INVALID = 'invalid'
