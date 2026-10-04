from agent_graph import analyze_requirement, run_code_search_node, run_plan_node, run_plan_review_node, run_requirement_node


def test_chinese_issue_uses_english_candidates_and_path_tokens(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "RequirementInput.tsx").write_text("export const validateInput = () => true\n", encoding="utf-8")
    state = {"issue_title":"输入长度校验", "issue_body":"请检查 frontend/src/RequirementInput.tsx 的表单配置", "requirement":analyze_requirement("输入长度校验", "请检查 frontend/src/RequirementInput.tsx 的表单配置")}
    result = run_code_search_node(state, str(tmp_path), ["src"])
    assert result["code_context"]
    assert result["search_trace"][0]["strategy"] == "english_candidates"


def test_requirement_analysis_extracts_validation_brief():
    analysis = analyze_requirement("Add input validation", "Validate the age before save. Reject negative values.")
    assert analysis.task_type == "validation"
    assert "validate" in analysis.keywords
    assert len(analysis.acceptance_criteria) == 1
    assert analysis.risk_level == "low"


def test_requirement_analysis_marks_security_context():
    analysis = analyze_requirement("Fix auth", "fix")
    assert analysis.task_type == "bug_fix"
    assert analysis.risk_level == "high"
    assert analysis.risk_notes
    assert not analysis.missing_context


def test_requirement_node_adds_structured_result_to_state():
    state = run_requirement_node({"task_id": 1, "repository_id": 2, "issue_title": "Optimize parser", "issue_body": "Improve parsing performance."})
    assert state["requirement"].task_type == "function_optimization"


def test_search_and_plan_nodes_create_a_conservative_single_file_plan(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "validator.py").write_text("def validate_age(value):\n    return value > 0\n", encoding="utf-8")
    state = run_requirement_node({"task_id": 1, "repository_id": 2, "issue_title": "Add validation", "issue_body": "Validate age input."})
    state = run_code_search_node(state, str(tmp_path), ["src"])
    state = run_plan_node(state)
    assert state["code_context"][0].path == "src/validator.py"
    assert state["change_plan"].impact_scope == "single_file"
    assert state["change_plan"].proposed_changes[0].path == "src/validator.py"


def test_plan_node_marks_empty_context_as_needing_human_input():
    state = run_requirement_node({"task_id": 1, "repository_id": 2, "issue_title": "Fix bug", "issue_body": "Fix parser."})
    state["code_context"] = []
    state = run_plan_node(state)
    assert state["change_plan"].impact_scope == "unknown"
    assert state["change_plan"].missing_context


def test_plan_review_flags_unknown_scope():
    state = run_requirement_node({"task_id": 1, "repository_id": 2, "issue_title": "Fix bug", "issue_body": "Fix parser."})
    state["code_context"] = []
    state = run_plan_node(state)
    state = run_plan_review_node(state)
    assert state["plan_review"].approved is False
    assert state["plan_review"].findings


def test_plan_carries_reviewer_feedback_into_reanalysis_output(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate():\n    return True\n", encoding="utf-8")
    state = run_requirement_node({"task_id": 1, "repository_id": 2, "issue_title": "Add validation", "issue_body": "Validate input.", "reviewer_feedback": "Keep the scope to this file."})
    state = run_code_search_node(state, str(tmp_path), ["src"])
    state = run_plan_node(state)
    assert state["change_plan"].reviewer_feedback == "Keep the scope to this file."


def test_short_issue_with_false_positive_fixtures_blocks_without_feedback(tmp_path):
    """Signal #3: 'fix bug' + debug_helper.py, prefix_util.py, config.py → no search hits → needs_context"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "debug_helper.py").write_text("def debug_info():\n    return 'debug'\n", encoding="utf-8")
    (tmp_path / "src" / "prefix_util.py").write_text("def prefix_of(s):\n    return s[:1]\n", encoding="utf-8")
    (tmp_path / "src" / "config.py").write_text("SETTINGS = {}\n", encoding="utf-8")
    state = run_requirement_node({"task_id": 1, "repository_id": 2, "issue_title": "fix", "issue_body": "bug"})
    state = run_code_search_node(state, str(tmp_path), ["src"])
    assert state["code_context"] == [], f"Expected no search hits, got {[c.path for c in state['code_context']]}"
    state = run_plan_node(state)
    state = run_plan_review_node(state)
    assert state["plan_review"].approved is False, "Should not approve when search returns nothing"


def test_feedback_with_path_token_unblocks_search(tmp_path):
    """Signal #5: 'fix bug' + feedback 'only src/validator.py' → search hit via path_tokens → awaiting_approval"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "debug_helper.py").write_text("def debug_info():\n    return 'debug'\n", encoding="utf-8")
    (tmp_path / "src" / "prefix_util.py").write_text("def prefix_of(s):\n    return s[:1]\n", encoding="utf-8")
    (tmp_path / "src" / "config.py").write_text("SETTINGS = {}\n", encoding="utf-8")
    (tmp_path / "src" / "validator.py").write_text("def validate(x):\n    return x > 0\n", encoding="utf-8")
    state = run_requirement_node({"task_id": 1, "repository_id": 2, "issue_title": "fix", "issue_body": "bug", "reviewer_feedback": "只考虑 src/validator.py"})
    state = run_code_search_node(state, str(tmp_path), ["src"])
    assert state["code_context"], f"Expected search hits after feedback, got {state['code_context']}"
    assert any(c.path == "src/validator.py" for c in state["code_context"]), "Should find src/validator.py via path_tokens"
    state = run_plan_node(state)
    state = run_plan_review_node(state)
    assert state["plan_review"].approved is True, "Should approve when search succeeds"
