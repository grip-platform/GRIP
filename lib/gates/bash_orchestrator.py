"""Bash gate pipeline -- extracted from context-gate-hook.py (#933).

Runs all Bash-specific security gates as a pipeline. Returns the first
deny result, or None if all gates pass.

Gates: E (deploy), G (steering), F (SSH), 2 (destructive git), 2a (push),
T (infra test), H/H2 (stash), O (branch), I (lock), 3 (secrets),
A (env vars), B (database), 5 (tool sub), 6 (bash lint).
"""

from .bash_gates import strip_heredocs_and_strings
from .security_gates import bash_text_for_env_guard


def _run_security_gates(stripped, *, check_deploy_gate_fn,
                        check_steering_fn, check_ssh_blocking_fn, deny_fn,
                        ask_fn=None):
    """Gates E, G, F: deploy, steering, SSH.

    A gate may return an ASK-prefixed reason (``security_gates.is_ask``) meaning
    "put it to the operator" rather than "block". It routes to ``ask_fn`` when the
    caller supplied one; a caller that did not is an older consumer, and the ask
    then falls CLOSED to ``deny_fn`` -- never open. V>>--<<V 2026-09-07.
    """
    from .security_gates import is_ask
    for fn in (check_deploy_gate_fn, check_steering_fn, check_ssh_blocking_fn):
        reason = fn(stripped)
        if reason:
            if is_ask(reason) and ask_fn is not None:
                return ask_fn(reason)
            return deny_fn(reason)
    return None


def _run_destructive_git(stripped, command, sid, tname, tinput, tpath, *,
                         is_destructive_git_fn, is_force_push_fn,
                         is_autonomous_bypass_fn, check_approval_fn,
                         clear_approval_fn, log_hook_fn, log_gate_metric_fn,
                         deny_and_log_fn, allow_fn, ACTION_FORCE_PUSH):
    """Gate 2: destructive git (force push, hard reset)."""
    destructive = is_destructive_git_fn(stripped)
    if not destructive:
        return None
    is_force = is_force_push_fn(stripped)
    gate = "destructive-git-force" if is_force else "destructive-git-nonforce"
    if is_autonomous_bypass_fn(gate):
        return allow_fn()
    if check_approval_fn(ACTION_FORCE_PUSH):
        log_hook_fn("ALLOW", f"Destructive git approved: {destructive}")
        clear_approval_fn(ACTION_FORCE_PUSH)
        log_gate_metric_fn("destructive-git", "allow", command)
        return allow_fn()
    log_hook_fn("BLOCK", f"Destructive git: {destructive}")
    return deny_and_log_fn(
        f"[GUARDRAIL: DESTRUCTIVE GIT] {destructive}. "
        f"Cannot be undone. Ask user for explicit approval first.",
        sid, tname, tinput, tpath)


def _run_git_flow(stripped, *, check_git_push_gate_fn, check_infra_test_gate_fn,
                  check_stash_pop_safety_fn, check_stash_drop_learning_fn,
                  check_branch_switch_fn, check_git_index_lock_fn, deny_fn):
    """Gates 2a, T, H, H2, O, I: push, test, stash, branch, lock."""
    push = check_git_push_gate_fn(stripped)
    if push:
        return deny_fn(push[1])
    for fn in (check_infra_test_gate_fn, check_stash_pop_safety_fn,
               check_stash_drop_learning_fn, check_branch_switch_fn,
               check_git_index_lock_fn):
        reason = fn(stripped)
        if reason:
            return deny_fn(reason)
    return None


def _run_secrets_gate(command, sid, tname, tinput, tpath, *,
                      contains_sensitive_file_fn, is_autonomous_bypass_fn,
                      log_hook_fn, deny_and_log_fn):
    """Gate 3: secrets detection. Safety floor -- no bypass set may skip it.

    Both halves of the previous docstring ("Bypassed in autonomous/RSI mode
    (git-crypt)") were false as of 2026-08-18 and are corrected here rather
    than deleted, so nobody re-widens the gate on the premise that was removed.

    Nothing bypasses it: "sensitive_file" is gone from AUTONOMOUS_L1/L2_BYPASS
    (hooks/autonomous_mode.py) and from YOLO_L1/L2/QUALITY_BYPASS
    (lib/gates/yolo_mode.py), so is_autonomous_bypass_fn("sensitive_file") is
    now False under every marker and every level. The
    is_autonomous_bypass_fn call below is retained deliberately: it is the
    live assertion of that, and it goes back to bypassing the moment anyone
    re-adds the id to a set.

    The git-crypt premise was measured false, not merely doubted. git-crypt
    encrypts by PATH (.gitattributes globs such as drafts/**), this gate
    matches by FILENAME; `git check-attr filter` over every entry in
    SENSITIVE_FILE_PATTERNS at realistic staging paths returned 0 of 22
    encrypted, against live controls (drafts/x.pem -> git-crypt).

    Falsifier for both halves:
    tests/test_secrets_gate_never_bypassed.py.
    """
    sensitive = contains_sensitive_file_fn(command)
    if sensitive and not is_autonomous_bypass_fn("sensitive_file"):
        log_hook_fn("BLOCK", f"Secrets detection: {sensitive}")
        return deny_and_log_fn(
            f"[GUARDRAIL: SECRETS DETECTION] Command may commit '{sensitive}'. "
            f"Never commit secrets. Remove sensitive files from staging first.",
            sid, tname, tinput, tpath)
    return None


def _run_quality_gates(stripped, command, *, check_env_var_exposure_fn,
                       check_database_exposure_fn, check_bash_tools_fn,
                       check_bash_lint_fn, log_hook_fn, deny_fn):
    """Gates A, B, 5, 6: env vars, database, tool substitution, lint.

    Lint gets the RAW command, as the secrets gate does, and strips per pattern:
    zsh expands parameters inside double quotes, which `stripped` has removed.
    The env-var guard gets its own view of the raw command instead: presence
    checks removed (GRIP#9911), and what the shell expands kept (GRIP#9916).
    """
    env_text = bash_text_for_env_guard(command)
    for fn, text, label in ((check_env_var_exposure_fn, env_text, "Env var exposure"),
                            (check_database_exposure_fn, stripped, "Database exposure"),
                            (check_bash_tools_fn, stripped, "Tool substitution"),
                            (check_bash_lint_fn, command, "Bash lint")):
        reason = fn(text)
        if reason:
            log_hook_fn("BLOCK", label)
            return deny_fn(reason)
    return None


def run_bash_gates(command, session_id, tool_name, tool_input, transcript_path,
                   *, is_fast_path,
                   check_deploy_gate_fn, check_steering_fn, check_ssh_blocking_fn,
                   is_destructive_git_fn, is_force_push_fn, is_autonomous_bypass_fn,
                   check_approval_fn, clear_approval_fn, check_git_push_gate_fn,
                   check_infra_test_gate_fn, check_stash_pop_safety_fn,
                   check_stash_drop_learning_fn, check_branch_switch_fn,
                   ask_fn=None,
                   check_git_index_lock_fn, contains_sensitive_file_fn,
                   check_env_var_exposure_fn, check_database_exposure_fn,
                   check_bash_tools_fn, check_bash_lint_fn,
                   log_hook_fn, log_gate_metric_fn, deny_fn, deny_and_log_fn,
                   allow_fn, ACTION_FORCE_PUSH):
    """Run all Bash-specific gates. Returns deny or None."""
    if is_fast_path:
        return None
    stripped = strip_heredocs_and_strings(command)
    s, t, ti, tp = session_id, tool_name, tool_input, transcript_path
    return (
        _run_security_gates(stripped, check_deploy_gate_fn=check_deploy_gate_fn,
                            check_steering_fn=check_steering_fn,
                            check_ssh_blocking_fn=check_ssh_blocking_fn, deny_fn=deny_fn,
                            ask_fn=ask_fn)
        or _run_destructive_git(stripped, command, s, t, ti, tp,
                                is_destructive_git_fn=is_destructive_git_fn,
                                is_force_push_fn=is_force_push_fn,
                                is_autonomous_bypass_fn=is_autonomous_bypass_fn,
                                check_approval_fn=check_approval_fn,
                                clear_approval_fn=clear_approval_fn,
                                log_hook_fn=log_hook_fn, log_gate_metric_fn=log_gate_metric_fn,
                                deny_and_log_fn=deny_and_log_fn, allow_fn=allow_fn,
                                ACTION_FORCE_PUSH=ACTION_FORCE_PUSH)
        or _run_git_flow(stripped, check_git_push_gate_fn=check_git_push_gate_fn,
                         check_infra_test_gate_fn=check_infra_test_gate_fn,
                         check_stash_pop_safety_fn=check_stash_pop_safety_fn,
                         check_stash_drop_learning_fn=check_stash_drop_learning_fn,
                         check_branch_switch_fn=check_branch_switch_fn,
                         check_git_index_lock_fn=check_git_index_lock_fn, deny_fn=deny_fn)
        or _run_secrets_gate(command, s, t, ti, tp,
                             contains_sensitive_file_fn=contains_sensitive_file_fn,
                             is_autonomous_bypass_fn=is_autonomous_bypass_fn,
                             log_hook_fn=log_hook_fn, deny_and_log_fn=deny_and_log_fn)
        or _run_quality_gates(stripped, command,
                              check_env_var_exposure_fn=check_env_var_exposure_fn,
                              check_database_exposure_fn=check_database_exposure_fn,
                              check_bash_tools_fn=check_bash_tools_fn,
                              check_bash_lint_fn=check_bash_lint_fn,
                              log_hook_fn=log_hook_fn, deny_fn=deny_fn)
    )
