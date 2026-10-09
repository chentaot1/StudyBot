# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import ast
import compileall
import os
import sys
import re


ROOT = os.path.dirname(os.path.abspath(__file__))


def _compileall_product_paths() -> None:
    """H3-style compile: walk product `.py` files; skip ``venv/``, ``.git/``, ``__pycache__/``."""
    skip_dir = {"venv", ".git", "__pycache__", "node_modules", "backups"}
    bad: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dir and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                ok = compileall.compile_file(path, quiet=1)
            except Exception as e:
                bad.append(f"{path}: {e}")
                continue
            if not ok:
                bad.append(path)
    if bad:
        fail("compileall failed for:\n  " + "\n  ".join(bad[:50]) + ("\n  ..." if len(bad) > 50 else ""))


def fail(msg: str) -> None:
    print(f"SMOKE FAIL: {msg}", file=sys.stderr)
    raise SystemExit(2)

def _attr_chain(node: ast.AST) -> list[str]:
    """Return attribute chain segments for Name/Attribute nodes.

    Example: for `self.bot.db.begin_group_lobby` returns ["self","bot","db","begin_group_lobby"].
    """
    parts: list[str] = []
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        elif isinstance(cur, ast.Name):
            parts.append(cur.id)
            break
        else:
            break
    return list(reversed(parts))


def _find_class(tree: ast.AST, name: str) -> ast.ClassDef | None:
    for n in getattr(tree, "body", []):
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def _find_function(cls: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _function_calls_self_method(func: ast.AST, method: str) -> bool:
    """True if `func` contains `self.<method>(...)`."""
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr == method and isinstance(n.func.value, ast.Name) and n.func.value.id == "self":
                return True
    return False


def _call_is_self_db_get_dm_enabled(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "get_dm_enabled":
        return False
    v = call.func.value
    if not isinstance(v, ast.Attribute) or v.attr != "db":
        return False
    return isinstance(v.value, ast.Name) and v.value.id == "self"


def _call_second_arg_is_schedule_reminders_str(call: ast.Call) -> bool:
    if len(call.args) < 2:
        return False
    arg1 = call.args[1]
    if isinstance(arg1, ast.Constant) and isinstance(arg1.value, str):
        return arg1.value == "schedule_reminders"
    return False


def _schedule_gate_uses_get_dm_enabled_schedule_reminders(gate_fn: ast.AST) -> bool:
    """`_schedule_block_dms_enabled` must read `self.db.get_dm_enabled(..., \"schedule_reminders\")`."""
    for n in ast.walk(gate_fn):
        if isinstance(n, ast.Call) and _call_is_self_db_get_dm_enabled(n) and _call_second_arg_is_schedule_reminders_str(n):
            return True
    return False


def _unwrap_db_worker(node: ast.AST) -> ast.AST:
    """Recognize the exact awaited worker/lambda wrapper without weakening the gate check."""
    if isinstance(node, ast.Await):
        call = node.value
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "run" and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "db_worker" and len(call.args) == 1
                and isinstance(call.args[0], ast.Lambda)):
            return call.args[0].body
    return node


def _for_block_in_blocks_loop_has_gate_then_fetch(for_node: ast.For) -> bool:
    """`for block in blocks:` body must gate with `if not self._schedule_block_dms_enabled(...): continue` before DM/fetch."""
    if not isinstance(for_node.target, ast.Name) or for_node.target.id != "block":
        return False
    if not isinstance(for_node.iter, ast.Name) or for_node.iter.id != "blocks":
        return False
    stmts = for_node.body
    if len(stmts) < 2:
        return False
    gate_if = stmts[1]
    if not isinstance(gate_if, ast.If):
        return False
    t = gate_if.test
    if not isinstance(t, ast.UnaryOp) or not isinstance(t.op, ast.Not):
        return False
    operand = _unwrap_db_worker(t.operand)
    if not isinstance(operand, ast.Call):
        return False
    fn = operand.func
    if not isinstance(fn, ast.Attribute) or fn.attr != "_schedule_block_dms_enabled":
        return False
    if not isinstance(fn.value, ast.Name) or fn.value.id != "self":
        return False
    if not gate_if.body or not isinstance(gate_if.body[0], ast.Continue):
        return False
    # No await get_user_or_fetch before the gate (stmt[0] is uid assign only)
    for s in stmts[:1]:
        for n in ast.walk(s):
            if isinstance(n, ast.Await):
                return False
    return True


def _collect_inner_for_block_in_blocks(func: ast.AST) -> list[ast.For]:
    out: list[ast.For] = []
    for n in ast.walk(func):
        if isinstance(n, ast.For):
            if isinstance(n.target, ast.Name) and n.target.id == "block":
                if isinstance(n.iter, ast.Name) and n.iter.id == "blocks":
                    out.append(n)
    return out


def _check_proc_block_leads_with_schedule_gate(fn: ast.AST) -> bool:
    """`_check_proc_for_block` must return early when `_schedule_block_dms_enabled` is false."""
    body = getattr(fn, "body", [])
    if not body or not isinstance(body[0], ast.If):
        return False
    first = body[0]
    t = first.test
    if not isinstance(t, ast.UnaryOp) or not isinstance(t.op, ast.Not):
        return False
    operand = _unwrap_db_worker(t.operand)
    if not isinstance(operand, ast.Call):
        return False
    fn_attr = operand.func
    if not isinstance(fn_attr, ast.Attribute) or fn_attr.attr != "_schedule_block_dms_enabled":
        return False
    if not isinstance(fn_attr.value, ast.Name) or fn_attr.value.id != "self":
        return False
    return any(isinstance(s, ast.Return) for s in first.body)


def _require_schedule_block_dms_respect_toggle(studybot_cls: ast.ClassDef) -> None:
    """Calendar /schedule DMs must honor the same toggle as /remind (schedule_reminders).

    Strengthen beyond a mere mention: gate must call get_dm_enabled with the correct key;
    producer loops must not fetch users or send before the gate.
    """
    gate = _find_function(studybot_cls, "_schedule_block_dms_enabled")
    if gate is None:
        fail("StudyBot._schedule_block_dms_enabled missing (/schedule block DM toggle gate)")
    if not _schedule_gate_uses_get_dm_enabled_schedule_reminders(gate):
        fail(
            "StudyBot._schedule_block_dms_enabled must call "
            'self.db.get_dm_enabled(..., "schedule_reminders") (must match /settings key)'
        )
    # try/except with fallback False avoids accidental fail-open on DB errors
    if not any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and n.value.value is False for n in ast.walk(gate)):
        fail("StudyBot._schedule_block_dms_enabled must return False on error path (fail-closed for DMs)")

    send_fn = _find_function(studybot_cls, "_send_schedule_reminders")
    if send_fn is None:
        fail("StudyBot._send_schedule_reminders missing")
    inner_fors = _collect_inner_for_block_in_blocks(send_fn)
    if len(inner_fors) != 1 or not _for_block_in_blocks_loop_has_gate_then_fetch(inner_fors[0]):
        fail(
            "StudyBot._send_schedule_reminders: inner `for block in blocks` must do "
            "`if not self._schedule_block_dms_enabled(...): continue` before get_user_or_fetch / send"
        )

    proc_fn = _find_function(studybot_cls, "_check_proc_for_block")
    if proc_fn is None:
        fail("StudyBot._check_proc_for_block missing")
    if not _check_proc_block_leads_with_schedule_gate(proc_fn):
        fail(
            "StudyBot._check_proc_for_block must start with "
            "`if not self._schedule_block_dms_enabled(user_id): return`"
        )

    ready_fn = _find_function(studybot_cls, "on_ready")
    if ready_fn is None:
        fail("StudyBot.on_ready missing")
    catchup_fors = _collect_inner_for_block_in_blocks(ready_fn)
    if len(catchup_fors) != 1 or not _for_block_in_blocks_loop_has_gate_then_fetch(catchup_fors[0]):
        fail(
            "StudyBot.on_ready schedule catch-up: inner `for block in blocks` must gate with "
            "_schedule_block_dms_enabled before get_user_or_fetch / send"
        )


def _find_nested_async_in_function(
    parent: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> ast.AsyncFunctionDef | None:
    for node in parent.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _call_has_keyword_ephemeral_true(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "ephemeral" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _collect_interaction_gate_send_calls(func: ast.AST) -> list[ast.Call]:
    """`interaction.response.send_message` / `interaction.followup.send` inside the tree gate."""
    out: list[ast.Call] = []
    for n in ast.walk(func):
        if not isinstance(n, ast.Await):
            continue
        c = n.value
        if not isinstance(c, ast.Call):
            continue
        chain = _attr_chain(c.func)
        if len(chain) < 3 or chain[-3] != "interaction":
            continue
        if chain[-2] == "response" and chain[-1] == "send_message":
            out.append(c)
        elif chain[-2] == "followup" and chain[-1] == "send":
            out.append(c)
    return out


def _require_tree_interaction_check_denials_are_ephemeral(studybot_cls: ast.ClassDef) -> None:
    """Hypothesis: non-ephemeral gate replies spam public channels / foreign guilds."""
    setup = _find_function(studybot_cls, "setup_hook")
    if setup is None:
        fail("StudyBot.setup_hook missing")
    gate = _find_nested_async_in_function(setup, "_tree_interaction_check")
    if gate is None:
        fail("setup_hook must define nested async def _tree_interaction_check")
    for call in _collect_interaction_gate_send_calls(gate):
        if not _call_has_keyword_ephemeral_true(call):
            fail(
                "_tree_interaction_check: every interaction.response.send_message / followup.send "
                "must pass ephemeral=True (privacy / channel spam)"
            )


def _require_daily_reset_serializes_on_state_lock(studybot_cls: ast.ClassDef) -> None:
    """Hypothesis: overlapping midnight jobs or manual triggers corrupt streaks / daily rows."""
    fn = _find_function(studybot_cls, "_daily_reset")
    if fn is None:
        fail("StudyBot._daily_reset missing")
    ok = False
    for node in fn.body:
        if not isinstance(node, ast.Try):
            continue
        for sub in node.body:
            if not isinstance(sub, ast.AsyncWith):
                continue
            for item in sub.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Attribute) and ctx.attr == "state_lock":
                    if isinstance(ctx.value, ast.Name) and ctx.value.id == "self":
                        for inner in ast.walk(ast.Module(body=sub.body, type_ignores=[])):
                            if isinstance(inner, ast.Call):
                                ch = _attr_chain(inner.func)
                                if ch[-3:] == ["self", "db", "reset_daily_progress"]:
                                    ok = True
    if not ok:
        fail(
            "StudyBot._daily_reset must wrap self.db.reset_daily_progress in async with self.state_lock "
            "(race-safe midnight reset)"
        )


def _require_outbox_pump_singleton_scheduled(studybot_cls: ast.ClassDef) -> None:
    """Hypothesis: two outbox pumps enqueue duplicate Discord sends for the same row."""
    fn = _find_function(studybot_cls, "_schedule_recurring")
    if fn is None:
        fail("StudyBot._schedule_recurring missing")
    found = False
    for node in fn.body:
        if not isinstance(node, ast.Expr):
            continue
        c = node.value
        if not isinstance(c, ast.Call):
            continue
        chain = _attr_chain(c.func)
        if chain[-3:] != ["self", "scheduler", "add_job"]:
            continue
        if not c.args:
            continue
        targ = c.args[0]
        if not (isinstance(targ, ast.Attribute) and targ.attr == "_pump_outbox"):
            continue
        job_id_ok = mx_ok = False
        for kw in c.keywords:
            if kw.arg == "id" and isinstance(kw.value, ast.Constant) and kw.value.value == "outbox_pump":
                job_id_ok = True
            if kw.arg == "max_instances" and isinstance(kw.value, ast.Constant) and kw.value.value == 1:
                mx_ok = True
        if job_id_ok and mx_ok:
            found = True
            break
    if not found:
        fail(
            "StudyBot._schedule_recurring must schedule self._pump_outbox with id='outbox_pump' and max_instances=1"
        )


def _require_hourly_cleanup_includes_bounty_expiry(studybot_cls: ast.ClassDef) -> None:
    """Hypothesis: missing bounty sweep leaves coins/XP in inconsistent state vs product rules."""
    fn = _find_function(studybot_cls, "_hourly_cleanup")
    if fn is None:
        fail("StudyBot._hourly_cleanup missing")
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            ch = _attr_chain(n.func)
            if ch[-3:] == ["self", "db", "expire_bounties_and_refund"]:
                return
    fail("StudyBot._hourly_cleanup must call self.db.expire_bounties_and_refund()")


def _require_discord_token_from_env(bot_src: str) -> None:
    """Hypothesis: token loaded from wrong place → prod outage or secret in repo."""
    if 'os.getenv("DISCORD_TOKEN"' not in bot_src and "os.getenv('DISCORD_TOKEN'" not in bot_src:
        fail("bot.py must read DISCORD_TOKEN via os.getenv(...) at startup")


def _require_no_discord_token_assignment(bot_src: str) -> None:
    for line in bot_src.splitlines():
        head = line.split("#", 1)[0].strip()
        if re.match(r"^DISCORD_TOKEN\s*=", head):
            fail("Do not assign DISCORD_TOKEN in bot.py; use environment / .env")


def _require_graceful_shutdown_wal_checkpoint(bot_src: str) -> None:
    """Hypothesis: kill -9 storms leave WAL huge or readers see partial state on mobile hosts."""
    if "_graceful_shutdown" not in bot_src:
        fail("bot.py should define _graceful_shutdown for clean exit")
    if "wal_checkpoint" not in bot_src:
        fail("graceful shutdown should run PRAGMA wal_checkpoint (SQLite durability on exit)")


def _has_begin_group_guard(func: ast.AST) -> bool:
    """True if function contains `if not self.bot.db.begin_group_lobby(lobby_id): return` (or equivalent)."""
    # We accept two patterns:
    # - direct: if not <call>: return
    # - assigned: ok = <call>; if not ok: return
    begin_call_targets: set[str] = set()

    for n in ast.walk(func):
        # Capture assignments like: ok = self.bot.db.begin_group_lobby(lobby_id)
        value = _unwrap_db_worker(n.value) if isinstance(n, ast.Assign) else None
        if isinstance(n, ast.Assign) and isinstance(value, ast.Call):
            chain = _attr_chain(value.func)
            if chain[-4:] == ["self", "bot", "db", "begin_group_lobby"] or chain[-3:] == ["bot", "db", "begin_group_lobby"]:
                if value.args and isinstance(value.args[0], ast.Name) and value.args[0].id == "lobby_id":
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            begin_call_targets.add(t.id)

    for n in ast.walk(func):
        if not isinstance(n, ast.If):
            continue
        test = n.test

        # Pattern A: if not self.bot.db.begin_group_lobby(lobby_id): return
        operand = _unwrap_db_worker(test.operand) if isinstance(test, ast.UnaryOp) else None
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(operand, ast.Call):
            chain = _attr_chain(operand.func)
            if chain[-4:] == ["self", "bot", "db", "begin_group_lobby"] or chain[-3:] == ["bot", "db", "begin_group_lobby"]:
                if operand.args and isinstance(operand.args[0], ast.Name) and operand.args[0].id == "lobby_id":
                    if any(isinstance(b, ast.Return) for b in n.body):
                        return True

        # Pattern B: if not ok: return (where ok was assigned begin_group_lobby(lobby_id))
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Name):
            if test.operand.id in begin_call_targets and any(isinstance(b, ast.Return) for b in n.body):
                return True

    return False


def _interaction_response_call_names(func: ast.AST) -> list[str]:
    """Return names of interaction.response.* methods called inside a function."""
    called: list[str] = []
    for n in ast.walk(func):
        if not isinstance(n, ast.Await):
            continue
        call = n.value
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Attribute):
            continue
        chain = _attr_chain(call.func)
        # accept "interaction.response.defer" / "interaction.response.send_message" etc.
        if len(chain) >= 3 and chain[-2] == "response" and chain[-3] == "interaction":
            called.append(chain[-1])
    return called


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent: dict[ast.AST, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parent[c] = p
    return parent


def _is_guarded_response_then_return(func: ast.AST, await_node: ast.Await, parent_map: dict[ast.AST, ast.AST]) -> bool:
    """True if this awaited interaction.response.* call is inside an if-branch that returns."""
    cur: ast.AST | None = await_node
    while cur is not None:
        par = parent_map.get(cur)
        if par is None:
            return False
        # If the await is in an If.body or If.orelse and that block contains a Return, treat it as guarded.
        if isinstance(par, ast.If):
            in_body = cur in par.body or any(cur is x for x in par.body)
            in_else = cur in par.orelse or any(cur is x for x in par.orelse)
            block = par.body if in_body else (par.orelse if in_else else None)
            if block is not None:
                if any(isinstance(x, ast.Return) for x in ast.walk(ast.Module(body=block, type_ignores=[]))):
                    return True
        cur = par
    return False


def _nearest_enclosing_if(await_node: ast.Await, parent_map: dict[ast.AST, ast.AST]) -> ast.If | None:
    cur: ast.AST | None = await_node
    while cur is not None:
        par = parent_map.get(cur)
        if par is None:
            return None
        if isinstance(par, ast.If):
            return par
        cur = par
    return None


def _is_discord_ui_button_method(func: ast.AST) -> bool:
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for d in getattr(func, "decorator_list", []):
        # Look for @discord.ui.button(...)
        if isinstance(d, ast.Call):
            chain = _attr_chain(d.func)
        else:
            chain = _attr_chain(d)
        if chain[-3:] == ["discord", "ui", "button"]:
            return True
    return False


def _scan_view_button_handlers_for_double_response(py_path: str, *, class_name_hint: str | None = None) -> None:
    """Fail if any discord.ui.View button handler awaits interaction.response.* multiple times."""
    if not os.path.exists(py_path):
        return
    with open(py_path, "r", encoding="utf-8") as f:
        src = f.read()
    t = ast.parse(src, filename=py_path)
    parents = _build_parent_map(t)
    for n in ast.walk(t):
        if not isinstance(n, ast.ClassDef):
            continue
        if class_name_hint and class_name_hint not in n.name:
            continue
        # Only look at classes that appear to be Views.
        bases = [".".join(_attr_chain(b)) for b in n.bases]
        is_viewish = any(b.endswith("discord.ui.View") or b.endswith("ui.View") or b == "discord.ui.View" or b == "View" for b in bases) or n.name.endswith("View")
        if not is_viewish:
            continue
        for m in n.body:
            if not _is_discord_ui_button_method(m):
                continue
            # Collect awaited interaction.response.* calls with their await nodes.
            awaited: list[tuple[str, ast.Await]] = []
            for an in ast.walk(m):
                if not isinstance(an, ast.Await):
                    continue
                call = an.value
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                chain = _attr_chain(call.func)
                if len(chain) >= 3 and chain[-2] == "response" and chain[-3] == "interaction":
                    awaited.append((chain[-1], an))

            if len(awaited) <= 1:
                continue

            # Allow the common safe pattern: early-branch response + return, then main-path defer/edit.
            unguarded_nodes = [
                (name, an) for (name, an) in awaited
                if not _is_guarded_response_then_return(m, an, parents)
            ]
            if len(unguarded_nodes) > 1:
                # Also allow mutually-exclusive branches within the same enclosing if/else block.
                enclosing_ifs = {id(_nearest_enclosing_if(an, parents)) for (_, an) in unguarded_nodes}
                enclosing_ifs.discard(id(None))
                if len(enclosing_ifs) == 1:
                    continue
                fail(
                    f"{os.path.relpath(py_path, ROOT)}:{n.name}.{getattr(m,'name','?')} "
                    f"has multiple unguarded interaction.response.* awaits: {[name for (name, _) in unguarded_nodes]}"
                )


def _require_start_session_allowed_member(py_path: str) -> None:
    """Fail if a `.db.start_session(` call in this file lacks `allowed_member=` (best-effort heuristic)."""
    if not os.path.exists(py_path):
        return
    with open(py_path, "r", encoding="utf-8") as f:
        src = f.read()
    # Only enforce for cog code, where we control call sites.
    for m in re.finditer(r"\.db\.start_session\s*\(", src):
        window = src[m.start(): m.start() + 350]
        # Allow legacy calls if explicitly group sessions are created with is_group=True AND source_guild_id is present;
        # but we still prefer allowed_member everywhere for stable UI gating.
        if "allowed_member" not in window:
            fail(f"{os.path.relpath(py_path, ROOT)} has db.start_session(...) without allowed_member= (DM/guild drift risk)")


def _require_no_strftime_a_for_goal_keys(py_path: str) -> None:
    """Fail if code uses strftime('%a') to index goal_mon/.. columns (locale-dependent)."""
    if not os.path.exists(py_path):
        return
    with open(py_path, "r", encoding="utf-8") as f:
        src = f.read()
    # Strftime('%a') is allowed for display text. The thing we must prevent is using it to form goal_ column names.
    if re.search(r"strftime\(\s*[\"']%a[\"']\s*\)", src):
        bad = re.search(r"goal_\{?\s*day_abbr\s*\}?", src) or re.search(r"f[\"']goal_\{day_abbr\}[\"']", src)
        if bad:
            fail(
                f"{os.path.relpath(py_path, ROOT)} appears to build goal_ keys from strftime('%a') "
                f"(locale-dependent; must use goal_override_key_for_date)."
            )


def _require_weekly_window_days_minus_one(db_src: str) -> None:
    """Enforce the rolling-7-day inclusive window math we standardized on (today - 6)."""
    # get_weekly_study_minutes should use timedelta(days=6)
    if not re.search(r"def\s+get_weekly_study_minutes\s*\(", db_src):
        fail("database.py missing get_weekly_study_minutes")
    if not re.search(r"get_weekly_study_minutes[\s\S]*timedelta\s*\(\s*days\s*=\s*6\s*\)", db_src):
        fail("database.py get_weekly_study_minutes should use timedelta(days=6) for 7-day strip")

    # get_daily_breakdown cutoff should be (days - 1)
    if not re.search(r"def\s+get_daily_breakdown\s*\(", db_src):
        fail("database.py missing get_daily_breakdown")
    if not re.search(r"get_daily_breakdown[\s\S]*timedelta\s*\(\s*days\s*=\s*max\s*\(\s*0\s*,\s*days\s*-\s*1\s*\)\s*\)", db_src):
        fail("database.py get_daily_breakdown cutoff must be today - (days-1) (avoid 8th-day mismatch)")


def _require_weekly_report_consistency(bot_src: str) -> None:
    """Ensure the weekly report uses the same EST 7-day strip for mins/chart/subjects/tags/tasks/sessions."""
    # Must pass est_calendar_days=True for tags/subjects in weekly report.
    if "build_weekly_report_embed" not in bot_src:
        fail("bot.py missing build_weekly_report_embed")
    if not re.search(r"build_weekly_report_embed[\s\S]*get_subject_stats\([^)]*days\s*=\s*7[^)]*est_calendar_days\s*=\s*True", bot_src):
        fail("Weekly report must call get_subject_stats(..., days=7, est_calendar_days=True)")
    if not re.search(r"build_weekly_report_embed[\s\S]*get_tag_stats\([^)]*days\s*=\s*7[^)]*est_calendar_days\s*=\s*True", bot_src):
        fail("Weekly report must call get_tag_stats(..., days=7, est_calendar_days=True)")
    # Must compute week_start_est as today - 6.
    if not re.search(r"week_start_est\s*=\s*\(datetime\.now\(EST\)\.date\(\)\s*-\s*timedelta\(\s*days\s*=\s*6\s*\)\)\.isoformat\(\)", bot_src):
        fail("Weekly report must define week_start_est as (today_est - 6).isoformat()")
    # Must filter sessions by stored_to_est_date >= week_start_est (avoid UTC-string comparisons).
    if "week_sessions" in bot_src and not re.search(r"_stored_to_est_date\s*\(\s*s\[\s*[\"']started_at[\"']\s*\]\s*\)\s*>=\s*week_start_est", bot_src):
        fail("Weekly report sessions filter must use _stored_to_est_date(started_at) >= week_start_est")


def _require_study_controls_refresh_uses_defer_and_edit(cog_src: str) -> None:
    """Validate private webhook editing, DM editing and shared pause/resume handling."""
    tree = ast.parse(cog_src)
    view = _find_class(tree, "StudySessionControlsView")
    cog = _find_class(tree, "Study")
    if view is None or cog is None:
        fail("Study controls and cog must exist")
    refresh = _find_function(view, "refresh_btn")
    if refresh is None or "edit_message" not in _interaction_response_call_names(refresh):
        fail("Refresh must acknowledge the current interaction by editing its card")
    for name in ("pause_btn", "resume_btn"):
        callback = _find_function(view, name)
        if callback is None or not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "_set_paused" for n in ast.walk(callback)):
            fail(f"{name} must use the shared state-checked pause/resume handler")
    handler = _find_function(cog, "_set_paused")
    if handler is None or "defer" not in _interaction_response_call_names(handler) or not _function_calls_self_method(handler, "_edit_live_session_message"):
        fail("Pause/resume must acknowledge promptly and refresh the live card")
    editor = _find_function(cog, "_edit_live_session_message")
    editor_src = ast.get_source_segment(cog_src, editor) if editor else ""
    if not editor_src or "_private_panels" not in editor_src or "get_partial_message" not in editor_src or "fetch_message" in editor_src:
        fail("Private cards must use saved webhook messages, and DM cards must use partial message editing")


def _require_zombie_sweeper_guard(bot_src: str) -> None:
    """The startup sweeper must skip broken-row cleanup while active sessions exist."""
    tree = ast.parse(bot_src)
    cls = _find_class(tree, "StudyBot")
    ready = _find_function(cls, "on_ready") if cls else None
    if ready is None:
        fail("Missing startup recovery")
    assignment = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "active" for t in n.targets) and any(isinstance(c, ast.Call) and _attr_chain(c.func) == ["self", "db", "get_all_active_sessions"] for c in ast.walk(n.value)) for n in ast.walk(ready))
    guard = next((n for n in ast.walk(ready) if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "active"), None)
    if not assignment or guard is None:
        fail("Zombie sweeper must query active sessions and guard on them")
    def contains_call(nodes, method):
        return any(isinstance(c, ast.Call) and _attr_chain(c.func) == ["self", "db", method] for n in nodes for c in ast.walk(n))
    if contains_call(guard.body, "get_zombie_sessions") or contains_call(guard.body, "kill_zombie_session") or not contains_call(guard.orelse, "get_zombie_sessions") or not contains_call(guard.orelse, "kill_zombie_session"):
        fail("Zombie cleanup must occur only in the no-active-sessions branch")


def _require_outbox_dm_disabled_defers_without_attempts(db_src: str, bot_src: str) -> None:
    """Ensure DM-toggle deferrals do not count as send failures (attempts)."""
    if "def defer_outbox_later" not in db_src:
        fail("database.py missing defer_outbox_later (policy deferral without attempts increment)")
    if "defer_outbox_later" not in bot_src:
        fail("bot.py _pump_outbox must use defer_outbox_later for dm disabled deferrals")
    # (We intentionally do not regex-check for "dm disabled" + retry_outbox_later here,
    # because the file may contain other retry paths and our enforcement is the positive
    # presence of defer_outbox_later in _pump_outbox.)


def _parse_dm_toggle_keys_from_profile(profile_src: str) -> set[str]:
    """Parse keys from DM_TOGGLES = { "key": "Label", ... } in cogs/profile.py."""
    m = re.search(r"DM_TOGGLES\s*=\s*\{([\s\S]*?)\n\}\s*\n", profile_src)
    if not m:
        fail("cogs/profile.py: could not parse DM_TOGGLES dict (expected closing brace after entries)")
    block = m.group(1)
    keys = set(re.findall(r'^\s*"([a-z0-9_]+)"\s*:', block, re.MULTILINE))
    if not keys:
        fail("cogs/profile.py: DM_TOGGLES has no keys")
    return keys


def _require_no_unknown_settings_key_strings(bot_src: str, dm_toggle_keys: set[str]) -> None:
    """Prevent settings_key typos in bot.py that aren't exposed in /settings DM toggles."""
    keys = set(re.findall(r"settings_key\s*=\s*[\"']([a-z0-9_]+)[\"']", bot_src))
    unknown = sorted(k for k in keys if k not in dm_toggle_keys)
    if unknown:
        fail(f"bot.py uses unknown settings_key values not in DM_TOGGLES: {unknown}")


def _require_enqueue_outbox_settings_keys_in_dm_toggles(dm_toggle_keys: set[str]) -> None:
    """Every enqueue_outbox(settings_key=...) in product code must match a /settings toggle key."""
    scan_paths: list[str] = [os.path.join(ROOT, "bot.py")]
    cogs_dir = os.path.join(ROOT, "cogs")
    if os.path.isdir(cogs_dir):
        scan_paths.extend(
            os.path.join(cogs_dir, fn) for fn in sorted(os.listdir(cogs_dir)) if fn.endswith(".py")
        )
    services_dir = os.path.join(ROOT, "services")
    if os.path.isdir(services_dir):
        scan_paths.extend(
            os.path.join(services_dir, fn)
            for fn in sorted(os.listdir(services_dir))
            if fn.endswith(".py")
        )
    offenders: list[str] = []
    for path in scan_paths:
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if "enqueue_outbox" not in text and "settings_key" not in text:
            continue
        rel = os.path.relpath(path, ROOT)
        for m in re.finditer(r"settings_key\s*=\s*[\"']([a-z0-9_]+)[\"']", text):
            k = m.group(1)
            if k not in dm_toggle_keys:
                offenders.append(f"{k} ({rel})")
    if offenders:
        fail(
            "enqueue_outbox settings_key must exist in cogs/profile.py DM_TOGGLES: "
            + "; ".join(sorted(set(offenders)))
        )


def _require_temptation_bundle_uses_outbox() -> None:
    """Temptation treat DMs must use outbox + temptation_bundle settings_key (no direct user.send)."""
    path = os.path.join(ROOT, "cogs", "temptation.py")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if re.search(r"\bawait\s+\w+\.send\s*\(", text):
        fail("cogs/temptation.py must not use await *.send (use db.enqueue_outbox)")
    if 'settings_key="temptation_bundle"' not in text:
        fail('cogs/temptation.py must use settings_key="temptation_bundle" for bundle DMs')
    if "dedupe_key=f\"temptation_bundle:" not in text:
        fail("cogs/temptation.py must use dedupe_key prefix temptation_bundle:")


def _require_quest_badge_dm_notifications_use_outbox() -> None:
    """Quest/badge completion DMs must use the outbox (retry + toggle at send time), not silent user.send."""
    quests_path = os.path.join(ROOT, "cogs", "quests.py")
    badges_path = os.path.join(ROOT, "cogs", "badges.py")
    for path, label in ((quests_path, "cogs/quests.py"), (badges_path, "cogs/badges.py")):
        if not os.path.exists(path):
            fail(f"{label} missing")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if re.search(r"\bawait\s+user\.send\s*\(", text):
            fail(f"{label} must not use await user.send (use db.enqueue_outbox for reliable DMs)")
        if "enqueue_outbox" not in text:
            fail(f"{label} must enqueue DM notifications via db.enqueue_outbox")
    with open(quests_path, "r", encoding="utf-8") as f:
        quests_text = f.read()
    if 'settings_key="quest_completion"' not in quests_text:
        fail("cogs/quests.py quest DMs must pass settings_key=\"quest_completion\" (matches /settings toggle)")
    if "dedupe_key=f\"quest_complete:" not in quests_text or "dedupe_key=f\"quest_all_complete_bonus:" not in quests_text:
        fail("cogs/quests.py must use stable dedupe_key prefixes quest_complete: and quest_all_complete_bonus:")
    with open(badges_path, "r", encoding="utf-8") as f:
        badges_text = f.read()
    if 'settings_key="badge_unlocks"' not in badges_text:
        fail("cogs/badges.py badge DM must pass settings_key=\"badge_unlocks\" (matches /settings toggle)")
    if "dedupe_key=f\"badge_unlock:" not in badges_text:
        fail("cogs/badges.py must use dedupe_key prefix badge_unlock: for unlock DMs")


def _require_admin_constants_imports_grouped() -> None:
    """Avoid mid-file constant imports after helpers (merge into top-level imports)."""
    admin_path = os.path.join(ROOT, "cogs", "admin.py")
    if not os.path.exists(admin_path):
        return
    with open(admin_path, "r", encoding="utf-8") as f:
        text = f.read()
    # Any "from constants import" must appear before the first top-level `def ` or `class `.
    m_imp = list(re.finditer(r"(?m)^from constants import .+$", text))
    m_def = re.search(r"(?m)^(def |class )", text)
    if m_imp and m_def:
        last_imp_line = max(m.end() for m in m_imp)
        if last_imp_line > m_def.start():
            fail("cogs/admin.py: move all 'from constants import' lines above the first def/class")


def _require_reminders_commands_default_ephemeral() -> None:
    """Reminders are user-entered text; responses should be ephemeral by default.

    We keep this as a lightweight source check rather than a full interaction AST analysis.
    """
    p = os.path.join(ROOT, "cogs", "reminders.py")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    # Success paths must be ephemeral.
    if "await interaction.response.send_message(embed=embed)" in src:
        fail("cogs/reminders.py: /remind add success must be ephemeral=True (privacy)")
    if "Reminder `#{reminder_id}` deleted." in src and "ephemeral=True" not in src.split("Reminder `#{reminder_id}` deleted.", 1)[1][:120]:
        fail("cogs/reminders.py: /remind delete success must be ephemeral=True (privacy)")


def _require_end_boss_is_claim_idempotent(db_src: str) -> None:
    """end_boss() is used as a 'claim' for raid resolution; it must not allow double-award."""
    # Must use ended_at guard in the UPDATE.
    if "def end_boss" not in db_src:
        fail("database.py missing end_boss")
    if "UPDATE raid_bosses SET killed=?, ended_at=? WHERE id=? AND ended_at IS NULL" not in db_src:
        fail("database.py end_boss must UPDATE with 'AND ended_at IS NULL' (idempotent claim)")
    # Must not return the boss when already ended (callers treat non-None as 'won the claim').
    if 'if boss["ended_at"]:\n                return boss' in db_src:
        fail("database.py end_boss must not `return boss` when already ended (prevents double-award)")


def _require_profile_and_leaderboards_default_ephemeral() -> None:
    """Profile/leaderboard output includes user stats; default should be ephemeral."""
    p = os.path.join(ROOT, "cogs", "profile.py")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    # /profile should be ephemeral by default (stats can be private in guild channels).
    if "async def profile_cmd" in src and "await interaction.response.send_message(embed=embed)\n" in src:
        fail("cogs/profile.py: /profile response must be ephemeral=True by default")
    # /leaderboard outputs per-user stats; keep it ephemeral by default.
    if "async def leaderboard_cmd" in src and "await interaction.response.send_message(embed=embed)\n" in src:
        # This also catches the raid leaderboard path which uses the same send_message form.
        fail("cogs/profile.py: /leaderboard responses must be ephemeral=True by default")


def _require_pomodoro_stop_followups_are_ephemeral() -> None:
    """Button-driven Pomodoro stop is deferred ephemeral; followups must remain ephemeral.

    We target the shared helper `_pomodoro_stop_inner`, which is called from the stop button path
    after `interaction.response.defer(ephemeral=True)`.
    """
    p = os.path.join(ROOT, "cogs", "pomodoro.py")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if "async def _pomodoro_stop_inner" not in src:
        fail("cogs/pomodoro.py missing _pomodoro_stop_inner")
    # Require ephemeral=True on the explicit followups inside _pomodoro_stop_inner only.
    start = src.find("async def _pomodoro_stop_inner")
    if start < 0:
        fail("cogs/pomodoro.py: could not find _pomodoro_stop_inner for smoke scan")
    # Back up to the start of the line (keeps indentation).
    line_start = src.rfind("\n", 0, start)
    if line_start < 0:
        line_start = 0
    # Find next method at the same indentation level.
    next_def = src.find("\n    async def ", start + 1)
    if next_def < 0:
        body = src[line_start:]
    else:
        body = src[line_start:next_def]
    if 'await interaction.followup.send("❌ No active Pomodoro.")' in body:
        fail("cogs/pomodoro.py: _pomodoro_stop_inner no-active followup must be ephemeral=True")
    if "await interaction.followup.send(embed=embed)\n" in body:
        fail("cogs/pomodoro.py: _pomodoro_stop_inner embed followup must be ephemeral=True")


def _require_expire_bounties_refund_is_claim_idempotent(db_src: str) -> None:
    """expire_bounties_and_refund() must be safe under concurrent schedulers.

    In particular, the "mark refunded" UPDATE must include a refunded=0 guard so a second
    instance cannot refund the same bounty twice.
    """
    if "def expire_bounties_and_refund" not in db_src:
        fail("database.py missing expire_bounties_and_refund")
    if "UPDATE bounties" not in db_src or "SET refunded=1" not in db_src:
        fail("database.py expire_bounties_and_refund must UPDATE bounties.refunded=1")
    # Require a guard somewhere near the refund update.
    # (Narrow substring heuristic: keep stable across formatting.)
    if "SET refunded=1" in db_src:
        tail = db_src.split("SET refunded=1", 1)[1][:320]
        if "refunded=0" not in tail and "AND refunded=0" not in tail:
            fail("database.py expire_bounties_and_refund UPDATE must include refunded=0 guard (idempotent claim)")


def _require_raid_leaderboard_default_ephemeral() -> None:
    """Raid leaderboard contains per-user stats; default should be ephemeral."""
    p = os.path.join(ROOT, "cogs", "raid.py")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if "async def raid_lb_cmd" not in src:
        fail("cogs/raid.py missing raid_lb_cmd")
    start = src.find("async def raid_lb_cmd")
    if start < 0:
        fail("cogs/raid.py: could not find raid_lb_cmd for smoke scan")
    line_start = src.rfind("\n", 0, start)
    if line_start < 0:
        line_start = 0
    next_def = src.find("\n    async def ", start + 1)
    body = src[line_start:] if next_def < 0 else src[line_start:next_def]
    if "await interaction.followup.send(embed=embed)\n" in body:
        fail("cogs/raid.py: /raid_leaderboard response must be ephemeral=True by default")


def _require_cleanup_old_data_does_not_delete_unrefunded_bounties(db_src: str) -> None:
    """cleanup_old_data must not delete expired-but-unrefunded bounties (would lose coins)."""
    if "def cleanup_old_data" not in db_src:
        fail("database.py missing cleanup_old_data")
    # Look for the bounties expiry delete statement and require a refunded/used guard.
    if "DELETE FROM bounties WHERE expires_at < ?" not in db_src:
        fail("database.py cleanup_old_data missing bounties expires_at deletion (expected)")
    idx = db_src.find("DELETE FROM bounties WHERE expires_at < ?")
    frag = db_src[idx:idx + 180] if idx >= 0 else ""
    if "refunded=1" not in frag and "used=1" not in frag:
        fail("database.py cleanup_old_data must not delete bounties by expires_at without refunded/used guard")


def _require_use_bounty_is_claim_idempotent(db_src: str) -> None:
    """Bounty payout must not be double-claimable under concurrent reward processing."""
    if "def use_bounty" not in db_src:
        fail("database.py missing use_bounty")
    if "UPDATE bounties SET used=1" not in db_src:
        fail("database.py use_bounty must UPDATE bounties.used=1")
    tail = db_src.split("UPDATE bounties SET used=1", 1)[1][:240]
    if "used=0" not in tail and "AND used=0" not in tail:
        fail("database.py use_bounty UPDATE must include used=0 guard (idempotent claim)")


def _require_inactivity_dm_send_handles_http_exception() -> None:
    """Inactivity DM send must tolerate transient Discord API failures (avoid disabling the monitor)."""
    p = os.path.join(ROOT, "cogs", "study.py")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if "async def _inactivity_loop" not in src:
        fail("cogs/study.py missing _inactivity_loop")
    start = src.find("async def _inactivity_loop")
    if start < 0:
        fail("cogs/study.py: could not find _inactivity_loop for smoke scan")
    line_start = src.rfind("\n", 0, start)
    if line_start < 0:
        line_start = 0
    next_def = src.find("\n    async def ", start + 1)
    body = src[line_start:] if next_def < 0 else src[line_start:next_def]
    send_idx = body.find("msg = await user.send(embed=embed, view=view)")
    if send_idx >= 0:
        # Focus only on the immediate send try/except block.
        frag = body[send_idx:send_idx + 260]
        if "except discord.Forbidden" in frag and "discord.HTTPException" not in frag and "except Exception" not in frag:
            fail("cogs/study.py: inactivity DM send must catch discord.HTTPException (transient failures)")


def _require_cog_extensions_match_cogs_package(setup_src: str) -> None:
    """S1: every ``cogs/*.py`` module (except ``__*``) must appear in the ``cogs = [ ... ]`` list in ``setup_hook``."""
    cogs_dir = os.path.join(ROOT, "cogs")
    if not os.path.isdir(cogs_dir):
        fail("S1: cogs/ directory missing")
    on_disk = {
        name[:-3]
        for name in os.listdir(cogs_dir)
        if name.endswith(".py") and not name.startswith("__")
    }
    m = re.search(r"\bcogs\s*=\s*\[([\s\S]*?)\]\s*\n\s*for\s+cog\s+in\s+cogs", setup_src)
    if not m:
        fail("S1: could not locate `cogs = [ ... ]` / `for cog in cogs` block in setup_hook")
    loaded = set(re.findall(r"[\"']cogs\.([^\"']+)[\"']", m.group(1)))
    missing = sorted(on_disk - loaded)
    extra = sorted(loaded - on_disk)
    if missing:
        fail(f"S1: cogs present on disk but not listed in setup_hook cogs=[...]: {missing}")
    if extra:
        fail(f"S1: setup_hook lists unknown cog modules (typo?): {extra}")


def _require_sqlite_conn_pragmas(db_src: str) -> None:
    """S2: ``Database._conn()`` must keep WAL + foreign keys (+ busy policy)."""
    i = db_src.find("def _conn")
    if i < 0:
        fail("S2: Database._conn not found")
    frag = db_src[i : i + 3500]
    if "journal_mode" not in frag or "WAL" not in frag:
        fail("S2: Database._conn must set WAL journal_mode")
    if "foreign_keys" not in frag or "ON" not in frag:
        fail("S2: Database._conn must enable foreign_keys=ON")
    if "busy_timeout" not in frag:
        fail("S2: Database._conn must set PRAGMA busy_timeout")
    if "sqlite3.connect" not in frag or "timeout" not in frag:
        fail("S2: Database._conn must pass sqlite3.connect(..., timeout=...)")


def _require_outbox_dedupe_unique_index(db_src: str) -> None:
    """S3: partial unique index on ``dedupe_key`` must remain."""
    if "idx_outbox_dedupe" not in db_src:
        fail("S3: missing idx_outbox_dedupe index DDL")
    if "CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_dedupe" not in db_src:
        fail("S3: idx_outbox_dedupe must be created as UNIQUE INDEX")
    if "WHERE dedupe_key IS NOT NULL" not in db_src:
        fail("S3: idx_outbox_dedupe must be partial (WHERE dedupe_key IS NOT NULL)")


class _UserOwnerSendScanner(ast.NodeVisitor):
    """S4: restrict raw ``user.send`` / ``owner.send`` in ``StudyBot`` (narrow DM bypass guard)."""

    def __init__(self) -> None:
        self._fn_stack: list[str] = []
        self.offenders: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._fn_stack.append(node.name)
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Await(self, node: ast.Await) -> None:
        call = node.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "send":
            base = call.func.value
            if isinstance(base, ast.Name) and base.id == "user":
                fn = self._fn_stack[-1] if self._fn_stack else "<module>"
                if fn != "_pump_outbox":
                    self.offenders.append((fn, getattr(node, "lineno", 0)))
            if isinstance(base, ast.Name) and base.id == "owner":
                fn = self._fn_stack[-1] if self._fn_stack else "<module>"
                if fn != "on_ready":
                    self.offenders.append((fn, getattr(node, "lineno", 0)))
        self.generic_visit(node)


def _require_scheduled_dm_send_allowlist(studybot_cls: ast.ClassDef) -> None:
    """S4: ``StudyBot`` must not grow new ``user.send`` paths outside the outbox pump (+ owner boot DM)."""
    scan = _UserOwnerSendScanner()
    for node in studybot_cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan.visit_FunctionDef(node)
    if scan.offenders:
        bad = ", ".join(f"{fn}:{ln}" for fn, ln in scan.offenders[:20])
        fail(f"S4: unexpected direct user.send/owner.send in StudyBot methods: {bad}")


def _require_checkin_settings_default_ephemeral() -> None:
    """Check-in settings are personal preferences; keep responses ephemeral by default."""
    p = os.path.join(ROOT, "cogs", "checkin.py")
    if not os.path.exists(p):
        return
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if "async def checkin_settings" not in src:
        fail("cogs/checkin.py missing checkin_settings")
    start = src.find("async def checkin_settings")
    if start < 0:
        fail("cogs/checkin.py: could not find checkin_settings for smoke scan")
    line_start = src.rfind("\n", 0, start)
    if line_start < 0:
        line_start = 0
    next_def = src.find("\n    async def ", start + 1)
    body = src[line_start:] if next_def < 0 else src[line_start:next_def]
    if "await interaction.response.send_message(embed=embed)\n" in body:
        fail("cogs/checkin.py: /checkin settings must respond with ephemeral=True by default")

def main() -> None:
    _compileall_product_paths()

    profile_path = os.path.join(ROOT, "cogs", "profile.py")
    with open(profile_path, "r", encoding="utf-8") as f:
        profile_src = f.read()
    dm_toggle_keys = _parse_dm_toggle_keys_from_profile(profile_src)

    bot_path = os.path.join(ROOT, "bot.py")
    with open(bot_path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, filename="bot.py")

    studybot = _find_class(tree, "StudyBot")
    if studybot is None:
        fail("StudyBot class not found in bot.py")

    methods = {n.name: n for n in studybot.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for required in ("setup_hook", "on_raw_reaction_add", "on_raw_reaction_remove"):
        if required not in methods:
            fail(f"StudyBot.{required} not found (possible indentation/paste slip)")

    setup_src = ast.get_source_segment(src, methods["setup_hook"]) or ""
    if "load_extension" not in setup_src:
        fail("setup_hook does not contain load_extension (cogs may not load)")
    if "Core cogs failed to load" not in setup_src:
        fail("setup_hook missing core-cog self-check (Study/Stats/Admin)")

    # Ensure no duplicate method defs (common paste/indentation failure mode).
    for name in ("setup_hook", "on_raw_reaction_add", "on_raw_reaction_remove"):
        cnt = sum(
            1 for n in studybot.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        )
        if cnt != 1:
            fail(f"StudyBot.{name} defined {cnt} times")

    # Validate the core extension list is present in setup_hook.
    # (We don't need exact equality—just ensure the must-have cogs are referenced.)
    must_ext = ("cogs.study", "cogs.stats", "cogs.admin")
    for ext in must_ext:
        if ext not in setup_src:
            fail(f"setup_hook missing required extension '{ext}'")

    # DB schema sanity (static): parse database.py for required tables/columns/migrations.
    db_path = os.path.join(ROOT, "database.py")
    with open(db_path, "r", encoding="utf-8") as f:
        db_src = f.read()

    required_tables = (
        "users",
        "study_sessions",
        "study_session_segments",
        "user_settings",
        "seasonal_history",
        "pomodoro_sessions",
        "group_pomo_lobbies",
        "group_pomo_members",
        "group_pomo_votes",
    )
    for t in required_tables:
        if f"CREATE TABLE IF NOT EXISTS {t}" not in db_src:
            fail(f"database.py missing CREATE TABLE for '{t}'")

    # Required columns must exist either in CREATE TABLE or in migrations list.
    required_cols = [
        ("study_sessions", "source_guild_id"),
        ("study_sessions", "tags"),
        ("study_sessions", "allowed_member"),
        ("study_session_segments", "paused_offset_seconds_start"),
        ("users", "seasonal_cheers_sent"),
        ("seasonal_history", "cheers_sent_at_reset"),
        ("group_pomo_lobbies", "announce_channel_id"),
        ("group_pomo_lobbies", "announce_message_id"),
        ("bounties", "refunded"),
        ("bounties", "refunded_at"),
    ]

    # Very simple checks: look for either "colname" in the CREATE TABLE block, or an ALTER migration tuple.
    for table, col in required_cols:
        create_hit = re.search(
            rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\([\s\S]*?\b{re.escape(col)}\b",
            db_src,
        )
        mig_hit = re.search(
            rf"\(\s*\"{re.escape(table)}\"\s*,\s*\"{re.escape(col)}\"\s*,",
            db_src,
        )
        if not (create_hit or mig_hit):
            fail(f"database.py missing column '{table}.{col}' (create table or migration)")

    # DM policy sanity: ensure a DM interaction gate exists and the RPG root blocklist is present.
    if "_tree_interaction_check" not in src or "DM_BLOCKED_RPG_ROOT_COMMANDS" not in src:
        fail("bot.py missing DM interaction gate or DM_BLOCKED_RPG_ROOT_COMMANDS")
    if "self.tree.interaction_check" not in src:
        fail("bot.py missing self.tree.interaction_check wiring (DM gate may not run)")
    if "slash_root_command_name" not in src:
        fail("bot.py missing slash_root_command_name (slash root resolution for gates)")
    if "RPG / economy commands only work in the allowlisted server" not in src:
        fail("bot.py missing foreign-guild RPG block user message")

    _require_schedule_block_dms_respect_toggle(studybot)

    # Structural CI invariants (S1–S4 / H3 compile hygiene).
    _require_cog_extensions_match_cogs_package(setup_src)
    _require_sqlite_conn_pragmas(db_src)
    _require_outbox_dedupe_unique_index(db_src)
    _require_scheduled_dm_send_allowlist(studybot)

    # Hypothetical / high-impact regressions (Discord + SQLite + scheduler nature of this bot).
    _require_tree_interaction_check_denials_are_ephemeral(studybot)
    _require_daily_reset_serializes_on_state_lock(studybot)
    _require_outbox_pump_singleton_scheduled(studybot)
    _require_hourly_cleanup_includes_bounty_expiry(studybot)
    _require_discord_token_from_env(src)
    _require_no_discord_token_assignment(src)
    _require_graceful_shutdown_wal_checkpoint(src)

    # Weekly time windows: enforce the standardized 7-day inclusive strip ending today (EST).
    _require_weekly_window_days_minus_one(db_src)
    _require_weekly_report_consistency(src)

    # Zombie sweeper behavior: only run if there are zero active sessions.
    _require_zombie_sweeper_guard(src)

    # Outbox policy deferrals: DM disabled must not increment attempts.
    _require_outbox_dm_disabled_defers_without_attempts(db_src, src)
    _require_no_unknown_settings_key_strings(src, dm_toggle_keys)
    _require_enqueue_outbox_settings_keys_in_dm_toggles(dm_toggle_keys)
    _require_quest_badge_dm_notifications_use_outbox()
    _require_temptation_bundle_uses_outbox()
    _require_admin_constants_imports_grouped()
    _require_reminders_commands_default_ephemeral()
    _require_end_boss_is_claim_idempotent(db_src)
    _require_profile_and_leaderboards_default_ephemeral()
    _require_pomodoro_stop_followups_are_ephemeral()
    _require_expire_bounties_refund_is_claim_idempotent(db_src)
    _require_raid_leaderboard_default_ephemeral()
    _require_cleanup_old_data_does_not_delete_unrefunded_bounties(db_src)
    _require_use_bounty_is_claim_idempotent(db_src)
    _require_inactivity_dm_send_handles_http_exception()
    _require_checkin_settings_default_ephemeral()

    # Group Pomodoro auto-start idempotency guard: ensure we gate on begin_group_lobby() success.
    pomo_path = os.path.join(ROOT, "cogs", "pomodoro.py")
    if os.path.exists(pomo_path):
        with open(pomo_path, "r", encoding="utf-8") as f:
            pomo_src = f.read()
        pomo_tree = ast.parse(pomo_src, filename="cogs/pomodoro.py")
        pomo_cls = _find_class(pomo_tree, "Pomodoro")
        if pomo_cls is None:
            fail("cogs/pomodoro.py missing Pomodoro class")
        autostart = _find_function(pomo_cls, "_group_autostart_loop")
        if autostart is None:
            fail("Pomodoro._group_autostart_loop missing (autostart feature expected)")
        if not _has_begin_group_guard(autostart):
            fail("pomodoro.py auto-start loop missing begin_group_lobby(lobby_id) failure guard (double-start risk)")

        # Interaction response hazard scan (high-signal only): prevent multiple response sends in one callback.
        # This catches common "already responded" bugs when refactoring views.
        for n in pomo_cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.endswith("_btn"):
                calls = _interaction_response_call_names(n)
                # Allow a single response method call; followups/edit_original_response are not checked here.
                if len(calls) > 1:
                    fail(f"Pomodoro.{n.name} calls interaction.response.* multiple times: {calls}")

    # Interaction response hazard scan for View button handlers (study + group pomo views).
    _scan_view_button_handlers_for_double_response(os.path.join(ROOT, "cogs", "pomodoro.py"))
    _scan_view_button_handlers_for_double_response(os.path.join(ROOT, "cogs", "study.py"))

    # Live study controls: current-interaction refresh and separate private/DM edit routes.
    study_path = os.path.join(ROOT, "cogs", "study.py")
    with open(study_path, "r", encoding="utf-8") as f:
        study_src = f.read()
    _require_study_controls_refresh_uses_defer_and_edit(study_src)

    # Locale-safe goal keys: ban strftime('%a') around goal_ usage anywhere important.
    _require_no_strftime_a_for_goal_keys(os.path.join(ROOT, "database.py"))
    _require_no_strftime_a_for_goal_keys(os.path.join(ROOT, "cogs", "stats.py"))
    _require_no_strftime_a_for_goal_keys(os.path.join(ROOT, "bot.py"))

    # Ensure start_session call sites snapshot allowed_member (prevents DM/guild drift).
    _require_start_session_allowed_member(os.path.join(ROOT, "cogs", "pomodoro.py"))
    _require_start_session_allowed_member(os.path.join(ROOT, "cogs", "study.py"))

    # Rewards engine decoupling sanity: avoid importing datetime parser from utils.
    engine_path = os.path.join(ROOT, "services", "rewards_engine.py")
    if os.path.exists(engine_path):
        with open(engine_path, "r", encoding="utf-8") as f:
            eng_src = f.read()
        if re.search(r"\bfrom\s+utils\s+import\s+parse_stored\b", eng_src):
            fail("services/rewards_engine.py should not import parse_stored from utils (engine coupling)")

    # Ensure removed command group stays removed.
    # Use token-ish matching to reduce false positives from log text/comments/longer identifiers.
    if re.search(r"\bname\s*=\s*[\"']raid_lb[\"']\b", src) or re.search(r"\braid_lb\b", db_src):
        fail("Found legacy 'raid_lb' reference (should be removed)")

    # H1 deeper lane (optional): bounded DB/outbox simulation subprocess.
    if os.getenv("SMOKE_WITH_DB_SIM", "").strip().lower() in ("1", "true", "yes"):
        import subprocess

        db_sim = os.path.join(ROOT, "scripts", "db_sim_test.py")
        subprocess.check_call([sys.executable, db_sim], cwd=ROOT)

    print("SMOKE OK")


if __name__ == "__main__":
    main()
