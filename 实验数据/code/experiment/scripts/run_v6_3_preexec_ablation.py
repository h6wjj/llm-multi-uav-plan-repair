"""v6.3 pre-execution SeSE/CDS-MR ablation runner."""
from __future__ import annotations
import argparse, json, math, os, re, time
from pathlib import Path
import requests
from run_agent4drone_parallel import load_environment_file, run_agent_command, start_agent, start_bridge, terminate_process, wait_health
from run_agent4drone_baseline import extract_tool_calls
from run_cds_mr_contract_online import _close_live_candidate, _contract_intent, _decision_latency, _dynamic_slice, _execute_structured_repair, _plan_distance
from v6_metric_evaluator import common_metrics

METHODS = ("B0", "B0+SeSE", "B0+CDS-MR", "B0+SeSE+CDS-MR")
SESE = {"B0+SeSE", "B0+SeSE+CDS-MR"}
CDS = {"B0+CDS-MR", "B0+SeSE+CDS-MR"}

def read_jsonl(path):
    rows = []
    with path.open(encoding="utf-8-sig") as h:
        for n, line in enumerate(h, 1):
            if line.strip():
                try: value = json.loads(line)
                except json.JSONDecodeError as exc: raise RuntimeError(f"bad JSONL {path}:{n}: {exc}") from exc
                if isinstance(value, dict): rows.append(value)
    return rows

def info(task):
    sc = task.get("scenario") if isinstance(task.get("scenario"), dict) else {}
    md = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    source = sc.get("source_task_id") or md.get("source_task_id") or task.get("source_task_id")
    return {"source_task_id": str(source or str(task.get("task_id", "")).split("::")[0]), "condition": str(md.get("condition") or sc.get("condition") or ""), "condition_label": str(md.get("condition_label") or sc.get("condition_label") or ""), "planning_uncertainty": bool(md.get("planning_uncertainty", sc.get("planning_uncertainty", False))), "run_seed_index": md.get("run_seed_index"), "run_seed": md.get("run_seed")}

def get_bundles(task):
    c = task.get("contract") if isinstance(task.get("contract"), dict) else {}; m = c.get("mission") if isinstance(c.get("mission"), dict) else {}; values = m.get("task_bundles") if isinstance(m.get("task_bundles"), dict) else {}
    out = []
    for owner, value in values.items():
        if isinstance(value, dict):
            item = dict(value); item["bundle_id"] = str(item.get("bundle_id") or owner); out.append(item)
    return sorted(out, key=lambda x: (int(x.get("order", 0)), x["bundle_id"]))

def get_agents(task):
    out = {}
    for value in task.get("agents", []):
        if isinstance(value, dict):
            aid = str(value.get("agent_id") or value.get("id") or "")
            if aid: out[aid] = value
    return out

def xyz(value):
    if isinstance(value, dict): value = [value.get("x"), value.get("y"), value.get("z", value.get("altitude"))]
    if not isinstance(value, (list, tuple)) or len(value) != 3: return None
    try: return tuple(float(v) for v in value)
    except (TypeError, ValueError): return None

def route_cost(start, points):
    origin = xyz(start); seq = [p for p in (xyz(v) for v in (points or [])) if p is not None]
    if origin is None or not seq: return 0.0
    return math.dist(origin, seq[0]) + sum(math.dist(a, b) for a, b in zip(seq, seq[1:]))

def context(task):
    c = task.get("contract") if isinstance(task.get("contract"), dict) else {}; m = c.get("mission") if isinstance(c.get("mission"), dict) else {}; r = c.get("resources") if isinstance(c.get("resources"), dict) else {}
    return {"task_id": task.get("task_id"), "instruction": str(task.get("instruction", ""))[:2000], "planning_uncertainty": info(task)["planning_uncertainty"], "agents": [{"agent_id": aid, "role": a.get("role"), "capabilities": a.get("capabilities", []), "start": a.get("start")} for aid, a in get_agents(task).items()], "bundles": [{"bundle_id": b["bundle_id"], "order": b.get("order"), "preferred_drone_id": b.get("preferred_drone_id"), "eligible_drone_ids": b.get("eligible_drone_ids", []), "eligible_roles": b.get("eligible_roles", []), "required_capabilities": b.get("required_capabilities", []), "waypoint_indices": b.get("waypoint_indices", []), "waypoints": b.get("waypoints", []), "deadline_s": b.get("deadline_s")} for b in get_bundles(task)], "bundle_dependencies": m.get("bundle_dependencies", {}), "hard_constraints": c.get("repair_constraints", {}), "resources": {"max_total_steps": r.get("max_total_steps"), "time_budget_s": r.get("time_budget_s")}}

def fallback(task, index, uncertain):
    out = []
    for pos, b in enumerate(get_bundles(task), 1):
        eligible = [str(x) for x in b.get("eligible_drone_ids", []) or []]; chosen = str(b.get("preferred_drone_id") or b.get("drone_id") or "")
        if uncertain and index > 0 and eligible:
            anchor = eligible.index(chosen) if chosen in eligible else 0; chosen = eligible[(anchor + index) % len(eligible)]
        out.append({"bundle_id": b["bundle_id"], "assigned_drone_id": chosen, "order": int(b.get("order", pos))})
    return out

def parse_plan(value):
    if isinstance(value, dict):
        value = value.get("bundles") or value.get("assignments") or value.get("plan")
        if isinstance(value, dict): value = [dict(v, bundle_id=k) for k, v in value.items() if isinstance(v, dict)]
    if not isinstance(value, list): raise ValueError("planner returned no bundle list")
    out = []
    for x in value:
        if not isinstance(x, dict): continue
        bid = x.get("bundle_id") or x.get("id"); aid = x.get("assigned_drone_id") or x.get("assigned_drone") or x.get("drone_id")
        if bid is None or aid is None: continue
        try: order = int(x.get("order", x.get("sequence")))
        except (TypeError, ValueError): order = len(out) + 1
        out.append({"bundle_id": str(bid), "assigned_drone_id": str(aid), "order": order})
    return out

def call_planner(task, index, temperature, endpoint, model, key, timeout):
    uncertain = info(task)["planning_uncertainty"]; guidance = "Use preferred assignment as candidate-0 anchor. Explore a different eligible assignment for this candidate when valid." if uncertain and index > 0 else "Use preferred assignments unless a contract field makes one invalid."
    prompt = ("You are a pre-execution UAVBench planner. Output JSON only with schema " + '{"bundles":[{"bundle_id":"...", "assigned_drone_id":"...", "order":1}]}. ' + "Assign every bundle once, use only eligible UAVs, satisfy role/capability and dependency constraints, and issue no actions. " + f"This is candidate {index}; {guidance}\nCONTRACT CONTEXT:\n" + json.dumps(context(task), ensure_ascii=False, separators=(",", ":")))
    body = {"model": model, "messages": [{"role": "system", "content": "Output valid JSON only."}, {"role": "user", "content": prompt}], "temperature": temperature, "response_format": {"type": "json_object"}}
    started = time.perf_counter(); headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    response = requests.post(endpoint, headers=headers, json=body, timeout=timeout)
    if response.status_code >= 400:
        body.pop("response_format", None); response = requests.post(endpoint, headers=headers, json=body, timeout=timeout)
    response.raise_for_status()
    payload = response.json(); choices = payload.get("choices", []) if isinstance(payload, dict) else []; message = choices[0].get("message", {}) if choices else {}; raw = str(message.get("content", "") if isinstance(message, dict) else "").strip()
    marker = chr(96) * 3
    if raw.startswith(marker): raw = raw.strip(chr(96)).replace("json", "", 1).strip()
    usage = payload.get("usage") if isinstance(payload, dict) else {}
    return {"plan": parse_plan(json.loads(raw)), "planner_status": "completed", "planner_latency_seconds": round(time.perf_counter() - started, 6), "planner_token_cost": usage.get("total_tokens") if isinstance(usage, dict) else None, "temperature": temperature}

def validate(task, plan, preferred):
    amap, bmap = get_agents(task), {b["bundle_id"]: b for b in get_bundles(task)}; ids = [str(x.get("bundle_id")) for x in plan]; assigns = {str(x.get("bundle_id")): str(x.get("assigned_drone_id")) for x in plan}; orders = {str(x.get("bundle_id")): x.get("order") for x in plan}; expected = list(bmap)
    checks = {"all_bundles_present_once": len(ids) == len(expected) and len(set(ids)) == len(ids) and set(ids) == set(expected), "all_orders_unique": len(orders) == len(expected) and len(set(orders.values())) == len(orders), "assignment_eligible": True, "role_capability_valid": True, "preferred_assignment_respected": True, "dependency_order_valid": True}; reasons = []
    if not checks["all_bundles_present_once"]: reasons.append("bundle_coverage")
    if not checks["all_orders_unique"]: reasons.append("order_uniqueness")
    for bid, b in bmap.items():
        aid, agent = assigns.get(bid, ""), amap.get(assigns.get(bid, "")); allowed = {str(x) for x in b.get("eligible_drone_ids", []) or []}
        if allowed and aid not in allowed: checks["assignment_eligible"] = False; reasons.append("ineligible:" + bid)
        required, actual = {str(x) for x in b.get("required_capabilities", []) or []}, {str(x) for x in (agent or {}).get("capabilities", []) or []}; roles = {str(x) for x in b.get("eligible_roles", []) or []}
        if agent is None or not required.issubset(actual) or (roles and str(agent.get("role")) not in roles): checks["role_capability_valid"] = False; reasons.append("role_capability:" + bid)
        anchor = str(b.get("preferred_drone_id") or b.get("drone_id") or "")
        if preferred and aid != anchor: checks["preferred_assignment_respected"] = False; reasons.append("preferred:" + bid)
    c = task.get("contract") if isinstance(task.get("contract"), dict) else {}; m = c.get("mission") if isinstance(c.get("mission"), dict) else {}; deps = m.get("bundle_dependencies", {})
    if isinstance(deps, dict):
        for bid, required in deps.items():
            for dep in required if isinstance(required, list) else []:
                if bid not in orders or str(dep) not in orders or orders[str(dep)] >= orders[bid]:
                    checks["dependency_order_valid"] = False; reasons.append(f"dependency:{dep}->{bid}")
    cost = sum(route_cost(amap.get(assigns.get(b["bundle_id"], ""), {}).get("start"), b.get("waypoints", [])) for b in get_bundles(task))
    return {"valid": all(checks.values()), "contract_score": round(sum(checks.values()) / len(checks), 6), "checks": checks, "reasons": reasons, "route_cost": round(cost, 9), "bundle_count": len(expected)}

def signature(plan): return tuple(sorted((str(x["bundle_id"]), str(x["assigned_drone_id"]), int(x["order"])) for x in plan))

def entropy(candidates):
    counts = {}
    for x in candidates: counts[str(x["signature"])] = counts.get(str(x["signature"]), 0) + 1
    total = sum(counts.values())
    if total < 2 or len(counts) <= 1: return 0.0
    return round(-sum((n / total) * math.log(n / total, 2) for n in counts.values()) / math.log(total, 2), 6)

def select(task, candidates, method):
    if method not in SESE: index, reason = 0, "single_candidate_baseline"
    else:
        chosen = min(candidates, key=lambda x: (-float(x["validation"].get("contract_score", 0)), float(x["validation"].get("route_cost", math.inf)), int(x["candidate_index"])))
        index, reason = int(chosen["candidate_index"]), "contract_validity_then_route_cost"
    base, chosen = candidates[0], candidates[index]; bv, cv = base["validation"], chosen["validation"]
    return {"gate": "SeSE_structural_contract_gate_v1" if method in SESE else "not_applicable", "selected_index": index, "selected_by_gate": bool(method in SESE and index != 0), "selection_reason": reason, "planning_uncertainty": info(task)["planning_uncertainty"], "candidate_count": len(candidates), "candidate_signature_count": len({x["signature"] for x in candidates}), "structural_entropy": entropy(candidates), "baseline_contract_score": bv.get("contract_score"), "selected_contract_score": cv.get("contract_score"), "selection_gain": round(float(cv.get("contract_score", 0)) - float(bv.get("contract_score", 0)), 6), "route_cost_gain": round(float(bv.get("route_cost", 0)) - float(cv.get("route_cost", 0)), 9), "selection_inputs": ["bundle_coverage", "role_capability_eligibility", "dependency_order", "contract_route_cost"], "excluded_from_selection": ["simulator_metrics", "failure_events", "task_success", "baseline_outputs"]}

def candidates_for(task, method, endpoint, model, key, timeout):
    uncertain = info(task)["planning_uncertainty"]; count, temps = (3, [0.0, 0.55, 0.9]) if method in SESE else (1, [0.0]); out = []
    for index in range(count):
        fallback_reason = None
        try: row = call_planner(task, index, temps[index], endpoint, model, key, timeout)
        except Exception as exc:
            row = {"plan": fallback(task, index, uncertain), "planner_status": "fallback", "planner_error": repr(exc), "planner_latency_seconds": None, "planner_token_cost": None, "temperature": temps[index]}; fallback_reason = "planner_error"
        checked = validate(task, row["plan"], not uncertain)
        if not checked["valid"]:
            row["plan"] = fallback(task, index, uncertain); checked = validate(task, row["plan"], not uncertain); fallback_reason = fallback_reason or "invalid_contract_fallback"
        sig = signature(row["plan"])
        if uncertain and method in SESE and index > 0 and any(str(x["signature"]) == str(sig) for x in out):
            row["plan"] = fallback(task, index, True); checked, sig = validate(task, row["plan"], False), signature(row["plan"]); fallback_reason = fallback_reason or "duplicate_contract_diversity_fallback"
        row.update({"candidate_index": index, "validation": checked, "signature": str(sig), "generation_fallback_reason": fallback_reason}); out.append(row)
    return out, select(task, out, method)

def command_for(task, plan):
    c = task.get("contract") if isinstance(task.get("contract"), dict) else {}; m = c.get("mission") if isinstance(c.get("mission"), dict) else {}; pmap = {str(x["bundle_id"]): x for x in plan}; ordered = []
    for b in get_bundles(task):
        x = pmap.get(b["bundle_id"], {}); ordered.append({"bundle_id": b["bundle_id"], "order": x.get("order", b.get("order")), "assigned_drone_id": x.get("assigned_drone_id"), "waypoints": b.get("waypoints", []), "required_capabilities": b.get("required_capabilities", [])})
    payload = {"task_id": task.get("task_id"), "agents": task.get("agents", []), "mission_instruction": task.get("instruction", ""), "selected_plan": ordered, "dependencies": m.get("bundle_dependencies", {}), "resources": c.get("resources", {})}
    return "Execute exactly the selected pre-execution UAV plan below. Use one move_along_path per bundle in dependency order, with the listed assigned_drone_id, bundle_id, and waypoint list. Do not probe, reorder, invent coordinates, replay a completed prefix, or attempt repair in this phase. If the bridge reports the pre-registered failure, stop immediately and report it. Return [TASK DONE] only after all bundles succeed.\n\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

def start_session(task, args, env):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task.get("task_id"))); bp = args.log_dir / f"v6_3_bridge_{safe}.log"; ap = args.log_dir / f"v6_3_agent_{safe}.log"; bh, ah = bp.open("w"), ap.open("w"); bridge = agent = None
    try:
        bridge = start_bridge(task, args, args.bridge_port_base, bh); burl = f"http://127.0.0.1:{args.bridge_port_base}"; wait_health(burl, args.startup_timeout)
        agent = start_agent(args, args.agent_port_base, args.bridge_port_base, env, ah, agent_dir=args.agent_dir); aurl = f"http://127.0.0.1:{args.agent_port_base}"; health = wait_health(aurl, args.startup_timeout, require_agent=True)
        return {"bridge": bridge, "agent": agent, "bridge_url": burl, "agent_url": aurl, "agent_health": health, "bridge_handle": bh, "agent_handle": ah, "bridge_log": str(bp), "agent_log": str(ap)}
    except Exception:
        terminate_process(agent); terminate_process(bridge); bh.close(); ah.close(); raise

def execute(session, task, args, plan):
    started = time.perf_counter(); result = run_agent_command(session["agent_url"], command_for(task, plan), args.command_timeout, args.poll_interval); metrics = requests.get(f"{session['bridge_url']}/bridge/metrics", timeout=20).json()
    return {"candidate_index": 0, "api_success": result.get("success"), "token_usage": result.get("token_usage"), "tool_calls": extract_tool_calls(result.get("intermediate_steps", [])), "gym_metrics": metrics, "timing": {"command_seconds": round(time.perf_counter() - started, 6), "total_seconds": round(time.perf_counter() - started, 6)}, "bridge_log": session["bridge_log"], "agent_log": session["agent_log"], "execution_mode": "selected_candidate_only"}

def execution_error(session, exc):
    try: metrics = requests.get(f"{session['bridge_url']}/bridge/metrics", timeout=10).json()
    except requests.RequestException: metrics = None
    return {"candidate_index": 0, "status": "runner_error", "error": repr(exc), "tool_calls": [], "gym_metrics": metrics, "timing": {}, "execution_mode": "selected_candidate_only"}

def run_task(task, method, args, env, endpoint, model, key):
    started = time.perf_counter(); cand, gate = candidates_for(task, method, endpoint, model, key, args.planner_timeout); selected_index = int(gate["selected_index"]); selected = cand[selected_index]; session = None
    try:
        session = start_session(task, args, env)
        try: initial = execute(session, task, args, selected["plan"])
        except Exception as exc: initial = execution_error(session, exc)
        initial_metrics = initial.get("gym_metrics") if isinstance(initial.get("gym_metrics"), dict) else {}; dynamic = _dynamic_slice(task, initial)
        repair = {"triggered": False, "repair_success": False, "intent_preserved": False, "executor": None, "same_live_session": False}
        if dynamic is not None and method in CDS:
            repair.update({"triggered": True, "executor": "structured_cds_mr_v1", "same_live_session": True})
            try: repair["repair_window"] = requests.post(f"{session['bridge_url']}/bridge/repair-window", timeout=10).json()
            except requests.RequestException as exc: repair["repair_window"] = {"enabled": False, "error": repr(exc)}
            repaired = _execute_structured_repair(session, task, initial, dynamic, len(cand)); repair["execution_record"] = repaired; final = repaired; final_metrics = repaired.get("gym_metrics") if isinstance(repaired.get("gym_metrics"), dict) else {}
            ih, fh = initial_metrics.get("command_history", []), final_metrics.get("command_history", []); count = len(ih) if isinstance(ih, list) else 0; repair_history = fh[count:] if isinstance(fh, list) else []
            repair["plan_distance"] = _plan_distance(dynamic, repair_history); intent = _contract_intent(task, repaired.get("tool_calls", []), final_metrics)
            repair.update({"intent": intent, "intent_preserved": bool(intent.get("preserved")), "repair_success": bool(final_metrics.get("task_success")) and bool(intent.get("preserved")), "repair_decision_latency_seconds": _decision_latency(initial_metrics, final_metrics)})
        else:
            final, final_metrics = initial, initial_metrics
            if dynamic is not None: repair["not_triggered_reason"] = "method_has_no_CDS-MR"
        pd = repair.get("plan_distance") if isinstance(repair.get("plan_distance"), dict) else {}
        standard = common_metrics(task, initial_metrics, final_metrics, plan_stability_distance=pd.get("plan_stability_distance"), action_distance=pd.get("action_distance"), repair_decision_latency_seconds=repair.get("repair_decision_latency_seconds"), runner_wallclock_seconds=round(time.perf_counter() - started, 6), mission_completion_time_seconds=final_metrics.get("sim_time_seconds") if final_metrics.get("task_success") else None, repair_attempted=bool(repair.get("triggered")))
        latency = sum(float(x["planner_latency_seconds"]) for x in cand if x.get("planner_latency_seconds") is not None); toks = [x["planner_token_cost"] for x in cand if x.get("planner_token_cost") is not None]
        return {"protocol_version": "v6_3_preexec_ablation_v1", "method": method, "method_definition": method, "task_id": task.get("task_id"), **info(task), "candidate_count": len(cand), "candidates": cand, "selected_candidate_index": selected_index, "selected_plan": selected["plan"], "sese_gate": gate, "initial_plan_contract_validity": selected.get("validation"), "initial_planning_latency_seconds": round(latency, 6), "initial_planning_token_cost": sum(toks) if toks else None, "initial_execution_record": initial, "repair": repair, "standard_metrics": standard, "runner_status": "completed"}
    except Exception as exc:
        return {"protocol_version": "v6_3_preexec_ablation_v1", "method": method, "method_definition": method, "task_id": task.get("task_id"), **info(task), "candidates": cand if "cand" in locals() else [], "sese_gate": gate if "gate" in locals() else {}, "runner_status": "runner_error", "runner_error": repr(exc)}
    finally:
        if session is not None: _close_live_candidate(session)

def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--input", required=True, type=Path); p.add_argument("--output", required=True, type=Path); p.add_argument("--method", required=True, choices=METHODS); p.add_argument("--limit", type=int, default=None)
    p.add_argument("--bridge-port-base", type=int, default=9400); p.add_argument("--uvicorn-log-level", default="warning"); p.add_argument("--agent-port-base", type=int, default=19400); p.add_argument("--startup-timeout", type=float, default=120); p.add_argument("--command-timeout", type=float, default=420); p.add_argument("--planner-timeout", type=float, default=120); p.add_argument("--poll-interval", type=float, default=.5); p.add_argument("--bridge-max-steps", type=int, default=None)
    p.add_argument("--agent-env-file", type=Path, default=Path("~/.config/agent4drone/agent4drone.env").expanduser()); p.add_argument("--agent-dir", type=Path, default=Path("~/uav_exp/MultiUAV-Plat/agent4drone").expanduser()); p.add_argument("--log-dir", type=Path, default=None); p.add_argument("--planner-endpoint", default="https://api.deepseek.com/chat/completions"); p.add_argument("--planner-model", default="deepseek-v4-flash")
    a = p.parse_args(); a.input, a.output = a.input.expanduser().resolve(), a.output.expanduser().resolve(); a.agent_dir, a.agent_env_file = a.agent_dir.expanduser().resolve(), a.agent_env_file.expanduser().resolve(); a.log_dir = (a.log_dir or a.output.parent / "logs").expanduser().resolve(); a.output.parent.mkdir(parents=True, exist_ok=True); a.log_dir.mkdir(parents=True, exist_ok=True)
    env = load_environment_file(a.agent_env_file); key = env.get("LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not key: raise RuntimeError("LLM_API_KEY is unavailable")
    tasks = read_jsonl(a.input); tasks = tasks[:max(0, a.limit)] if a.limit is not None else tasks; done = set()
    if a.output.exists():
        for line in a.output.read_text(encoding="utf-8").splitlines():
            if line.strip(): done.add(str(json.loads(line).get("task_id")))
    pending = [x for x in tasks if str(x.get("task_id")) not in done]
    print(json.dumps({"method": a.method, "tasks": len(tasks), "pending": len(pending), "candidate_count": 3 if a.method in SESE else 1, "planner_model": a.planner_model}), flush=True)
    with a.output.open("a", encoding="utf-8") as h:
        for n, task in enumerate(pending, 1):
            record = run_task(task, a.method, a, env, a.planner_endpoint, a.planner_model, key)
            h.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"); h.flush()
            print(json.dumps({"progress": f"{n}/{len(pending)}", "task_id": task.get("task_id"), "status": record.get("runner_status"), "selected_candidate": record.get("selected_candidate_index"), "repair_triggered": (record.get("repair") or {}).get("triggered")}, ensure_ascii=False), flush=True)

if __name__ == "__main__": main()
