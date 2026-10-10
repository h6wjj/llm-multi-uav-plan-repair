from __future__ import annotations
import argparse, hashlib, json, os, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import requests
from run_agent4drone_baseline import wait_health
from run_agent4drone_parallel import start_bridge, terminate_process
from run_v6_3_preexec_ablation import get_bundles, get_agents, info, read_jsonl
from run_cds_mr_contract_online import _dynamic_slice, _plan_distance
from summarize_v6_3_preexec_ablation import global_remaining_plan
from v6_metric_evaluator import common_metrics

MODEL="deepseek-v4-flash"
ENDPOINT="https://api.deepseek.com/chat/completions"
METHODS=("H-MRTA-Repair","LLM-Assisted Plan Execution")
def port(task, method):
    h=hashlib.sha256((str(task.get("task_id"))+"::"+method).encode()).hexdigest()
    return 26000+int(h[:4],16)%2500
def get_metrics(url):
    value=requests.get(url+"/bridge/metrics",timeout=20).json()
    if not isinstance(value,dict): raise RuntimeError("bridge_metrics_not_object")
    return value
def move(url, drone, bid, points):
    r=requests.post(url+"/drones/"+str(drone)+"/command/move_along_path",
                    json={"waypoints":points,"allow_partial_move":True,"bundle_id":bid},timeout=120)
    r.raise_for_status()
    v=r.json()
    return v if isinstance(v,dict) else {"success":False,"error":"non_object_response"}
def initial(task,url):
    by_id={str(b["bundle_id"]):b for b in get_bundles(task)}
    records=[]
    for b in sorted(by_id.values(),key=lambda x:(int(x.get("order",0)),str(x["bundle_id"]))):
        bid=str(b["bundle_id"])
        drone=str(b.get("preferred_drone_id") or b.get("original_owner") or b.get("drone_id"))
        t=time.perf_counter()
        try: value=move(url,drone,bid,list(b.get("waypoints") or []))
        except Exception as e: value={"success":False,"error":repr(e)}
        records.append({"sequence":b.get("order"),"bundle_id":bid,"assigned_drone":drone,
                        "waypoints":b.get("waypoints") or [],"response":value,
                        "seconds":round(time.perf_counter()-t,6)})
        if not value.get("success"): break
    return {"structured_commands":records,"gym_metrics":get_metrics(url),
            "execution_mode":"identical_contract_bound_initial_execution"}
def execute(url,plan,by_id):
    records=[]
    for item in sorted(plan,key=lambda x:(int(x.get("sequence",x.get("order",0))),str(x.get("bundle_id")))):
        bid=str(item["bundle_id"]); b=by_id.get(bid)
        if b is None: return {"status":"unknown_bundle_in_repair_plan","structured_commands":records,"gym_metrics":get_metrics(url)}
        pts=list(item.get("remaining_waypoints") or b.get("waypoints") or [])
        drone=str(item.get("assigned_drone") or item.get("assigned_drone_id") or "")
        t=time.perf_counter()
        try: value=move(url,drone,bid,pts)
        except Exception as e: value={"success":False,"error":repr(e)}
        records.append({"sequence":item.get("sequence",item.get("order")),"bundle_id":bid,
                        "assigned_drone":drone,"waypoints":pts,"response":value,
                        "seconds":round(time.perf_counter()-t,6)})
        if not value.get("success"): break
    return {"structured_commands":records,"gym_metrics":get_metrics(url),
            "execution_mode":"same_live_bridge_external_repair"}
def repair_context(task,initial_record):
    mm=initial_record["gym_metrics"]; progress=mm.get("completed_waypoints_by_bundle") or []
    progress=mm.get("completed_waypoints_by_bundle") or {}; unfinished=[]
    for b in get_bundles(task):
        bid=str(b["bundle_id"]); done=int(progress.get(bid,0) or 0); pts=list(b.get("waypoints") or [])
        if done<len(pts):
            unfinished.append({"bundle_id":bid,"order":b.get("order"),"completed_waypoints":done,
                "remaining_waypoints":pts[done:],"eligible_drone_ids":b.get("eligible_drone_ids") or [],
                "required_capabilities":b.get("required_capabilities") or [],
                "eligible_roles":b.get("eligible_roles") or []})
    c=task.get("contract") or {}; m=c.get("mission") or {}
    return {"failure":mm.get("first_failure"),"completed_waypoints_by_bundle":progress,
            "unavailable_drone_ids":mm.get("unavailable_drone_ids") or [],
            "unfinished_bundles":unfinished,
            "agents":[{"agent_id":aid,"role":a.get("role"),"capabilities":a.get("capabilities") or [],
                       "start":a.get("start")} for aid,a in get_agents(task).items()],
            "dependencies":m.get("bundle_dependencies") or {},
            "hard_constraints":c.get("repair_constraints") or {},
            "resources":c.get("resources") or {}}
def parse_plan(raw):
    raw=str(raw).strip()
    if raw.startswith(chr(96)*3): raw=raw.strip(chr(96)).replace("json","",1).strip()
    value=json.loads(raw)
    if isinstance(value,dict): value=value.get("bundles") or value.get("plan") or value.get("assignments")
    if not isinstance(value,list): raise ValueError("llm_repair_plan_is_not_a_list")
    out=[]
    for n,x in enumerate(value,1):
        if not isinstance(x,dict): continue
        bid=x.get("bundle_id") or x.get("id"); drone=x.get("assigned_drone_id") or x.get("assigned_drone") or x.get("drone_id")
        if bid is not None and drone is not None:
            out.append({"bundle_id":str(bid),"assigned_drone":str(drone),"sequence":int(x.get("order",x.get("sequence",n)))})
    return out
def llm_plan(task, initial_record, key, timeout):
    ctx=repair_context(task,initial_record)
    prompt=("You are an external LLM-assisted plan execution baseline. The mission just failed. "
        "Generate a complete executable plan for every unfinished bundle from the live failure state. "
        "Preserve completed prefixes. Obey role/capability eligibility, dependencies, deadlines, and "
        "the shared repair budget. Do not use future failure information, causal slicing, or a minimum-edit "
        "objective. Output JSON only with schema {bundles:[{bundle_id,assigned_drone,order}]}.\n"+json.dumps(ctx,ensure_ascii=False,separators=(",",":")))
    body={"model":MODEL,"messages":[{"role":"system","content":"Return valid JSON only."},{"role":"user","content":prompt}],
          "temperature":0.0,"response_format":{"type":"json_object"}}
    t=time.perf_counter(); headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"}
    r=requests.post(ENDPOINT,headers=headers,json=body,timeout=timeout)
    if r.status_code>=400:
        body.pop("response_format",None); r=requests.post(ENDPOINT,headers=headers,json=body,timeout=timeout)
    r.raise_for_status(); payload=r.json()
    choices=payload.get("choices") if isinstance(payload,dict) else []; msg=(choices or [{}])[0].get("message") or {}
    raw=str(msg.get("content") or ""); usage=payload.get("usage") if isinstance(payload,dict) else {}
    return {"plan":parse_plan(raw),"raw_response":raw,"planner_model":MODEL,
            "planner_latency_seconds":round(time.perf_counter()-t,6),
            "planner_token_cost":usage.get("total_tokens") if isinstance(usage,dict) else None,"prompt_context":ctx}
def validate(task,initial_record,plan):
    mm=initial_record["gym_metrics"]; progress=mm.get("completed_waypoints_by_bundle") or {}
    bundles={str(b["bundle_id"]):b for b in get_bundles(task)}
    expected={bid for bid,b in bundles.items() if int(progress.get(bid,0) or 0)<len(b.get("waypoints") or [])}
    actual=[str(x.get("bundle_id")) for x in plan]; assigned={str(x.get("bundle_id")):str(x.get("assigned_drone")) for x in plan}
    agents=get_agents(task)
    checks={"unfinished_bundle_coverage_once":len(actual)==len(expected) and set(actual)==expected and len(set(actual))==len(actual),
            "eligible_assignment":True,"role_capability_valid":True,"dependency_order":True,"completed_prefix_not_replanned":not(set(actual)-expected)}
    reasons=[]
    for bid in expected:
        b=bundles[bid]; aid=assigned.get(bid,""); a=agents.get(aid); allowed={str(x) for x in b.get("eligible_drone_ids") or []}
        if allowed and aid not in allowed: checks["eligible_assignment"]=False; reasons.append("ineligible:"+bid)
        req={str(x) for x in b.get("required_capabilities") or []}; caps={str(x) for x in (a or {}).get("capabilities") or []}; roles={str(x) for x in b.get("eligible_roles") or []}
        if a is None or not req.issubset(caps) or (roles and str(a.get("role")) not in roles):
            checks["role_capability_valid"]=False; reasons.append("role_capability:"+bid)
    deps=((task.get("contract") or {}).get("mission") or {}).get("bundle_dependencies") or {}; orders={str(x.get("bundle_id")):int(x.get("sequence",0)) for x in plan}
    for bid,reqs in deps.items():
        for dep in reqs if isinstance(reqs,list) else []:
            if bid in orders and str(dep) in orders and orders[str(dep)]>=orders[bid]:
                checks["dependency_order"]=False; reasons.append("dependency:"+str(dep)+"->"+str(bid))
    return {"valid":all(checks.values()),"checks":checks,"reasons":reasons}
def run_one(task,method,tasks_path,args,key):
    started=time.perf_counter(); bridge=None; safe=hashlib.sha256(str(task["task_id"]).encode()).hexdigest()[:16]
    handle=(args.log_dir/(safe+".log")).open("w",encoding="utf-8"); bp=port(task,method); url="http://127.0.0.1:"+str(bp)
    try:
        bridge=start_bridge(task,SimpleNamespace(input=tasks_path,bridge_max_steps=None),bp,handle)
        health=wait_health(url,args.startup_timeout)
        if str(health.get("task_id"))!=str(task.get("task_id")): raise RuntimeError("bridge_task_mismatch")
        init=initial(task,url); imm=init["gym_metrics"]; failure=imm.get("first_failure"); repair={"triggered":False}; final=init; window=None
        if isinstance(failure,dict):
            window=requests.post(url+"/bridge/repair-window",timeout=20).json(); by_id={str(b["bundle_id"]):b for b in get_bundles(task)}
            if method=="H-MRTA-Repair":
                plan,audit=global_remaining_plan(task,imm)
                repair.update({"triggered":True,"implementation":"platform_adapted_global_mrta_repair","official_source":"https://github.com/multirobot-use/mrta_heuristic_planner","official_runtime_invoked":False,"repair_plan":plan,"planner_audit":audit})
                final=execute(url,plan,by_id)
            else:
                decision=llm_plan(task,init,key,args.llm_timeout); plan=decision["plan"]; check=validate(task,init,plan)
                repair.update({"triggered":True,"implementation":"platform_adapted_llm_full_remaining_replan","llm_model":MODEL,"official_source":"paper_method_adapter; official_runtime_not_available","official_runtime_invoked":False,"llm_decision":decision,"repair_plan":plan,"plan_validation":check})
                if check["valid"]:
                    progress=imm.get("completed_waypoints_by_bundle") or {}; ep=[]
                    for item in plan:
                        b=by_id[item["bundle_id"]]; done=int(progress.get(item["bundle_id"],0) or 0)
                        ep.append({**item,"remaining_waypoints":list(b.get("waypoints") or [])[done:]})
                    final=execute(url,ep,by_id)
                else:
                    final={"status":"invalid_llm_repair_plan","structured_commands":[],"gym_metrics":get_metrics(url)}
        fm=final.get("gym_metrics") or get_metrics(url); before=imm.get("command_history") or []; after=fm.get("command_history") or []
        rh=after[len(before):] if len(after)>=len(before) else []; fw=float(failure["wall_time_seconds"]) if isinstance(failure,dict) and failure.get("wall_time_seconds") is not None else None
        rw=next((float(x["wall_started_seconds"]) for x in rh if isinstance(x,dict) and x.get("wall_started_seconds") is not None),None)
        latency=max(0.0,rw-fw) if fw is not None and rw is not None else None
        dynamic=_dynamic_slice(task,init)
        distance=_plan_distance(dynamic,rh) if dynamic is not None else {}
        standard=common_metrics(task,imm,fm,plan_stability_distance=distance.get("plan_stability_distance"),action_distance=distance.get("action_distance"),repair_decision_latency_seconds=round(latency,6) if latency is not None else None,
            runner_wallclock_seconds=round(time.perf_counter()-started,6),mission_completion_time_seconds=fm.get("sim_time_seconds") if fm.get("task_success") else None,repair_attempted=bool(repair.get("triggered")))
        return {"protocol_version":"external_role_conflict_paired_v1","method":method,"method_definition":method,"task_id":task.get("task_id"),**info(task),"implementation_scope":"platform_adapted_external_baseline","initial_execution_record":init,"failure_event":failure,"repair_window":window,"repair":repair,"final_execution_record":final,"standard_metrics":standard,"runner_status":"completed"}
    except Exception as exc:
        try: partial=get_metrics(url)
        except Exception: partial=None
        return {"protocol_version":"external_role_conflict_paired_v1","method":method,"method_definition":method,"task_id":task.get("task_id"),**info(task),"implementation_scope":"platform_adapted_external_baseline","runner_status":"runner_error","runner_error":repr(exc),"partial_metrics":partial}
    finally:
        terminate_process(bridge); handle.close()

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",required=True,type=Path); p.add_argument("--output",required=True,type=Path); p.add_argument("--method",required=True,choices=METHODS); p.add_argument("--startup-timeout",type=float,default=120); p.add_argument("--llm-timeout",type=float,default=120); p.add_argument("--agent-env-file",type=Path,default=Path("~/.config/agent4drone/agent4drone.env").expanduser()); p.add_argument("--log-dir",type=Path,default=None); a=p.parse_args()
    a.input=a.input.expanduser().resolve(); a.output=a.output.expanduser().resolve(); a.log_dir=(a.log_dir or a.output.parent/"logs").expanduser().resolve(); a.output.parent.mkdir(parents=True,exist_ok=True); a.log_dir.mkdir(parents=True,exist_ok=True)
    env={}
    if a.agent_env_file.exists():
        for line in a.agent_env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k,v=line.split("=",1); env[k.strip()]=v.strip().strip("'\"")
    key=env.get("LLM_API_KEY") or os.environ.get("LLM_API_KEY","")
    if a.method.startswith("LLM") and not key: raise RuntimeError("LLM_API_KEY_is_unavailable")
    tasks=read_jsonl(a.input); done=set()
    if a.output.exists():
        for line in a.output.read_text(encoding="utf-8").splitlines():
            if line.strip(): done.add(str(json.loads(line).get("task_id")))
    pending=[t for t in tasks if str(t.get("task_id")) not in done]
    print(json.dumps({"method":a.method,"tasks":len(tasks),"pending":len(pending),"model":MODEL if a.method.startswith("LLM") else None}),flush=True)
    with a.output.open("a",encoding="utf-8") as h:
        for i,t in enumerate(pending,1):
            r=run_one(t,a.method,a.input,a,key); h.write(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n"); h.flush()
            print(json.dumps({"progress":f"{i}/{len(pending)}","task_id":t.get("task_id"),"status":r.get("runner_status"),"recovery":(r.get("standard_metrics") or {}).get("recovery_success_rate_item")}),flush=True)
if __name__=="__main__": main()
