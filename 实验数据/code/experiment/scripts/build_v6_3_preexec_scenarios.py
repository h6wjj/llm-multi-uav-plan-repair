"""Build v6.4 UAVBench tasks for the shared-candidate-pool ablation.

This derives paired conditions from frozen v6.2 geometry.  It removes the
method-specific requirement that every bundle must use one pre-resolved UAV,
while retaining hard capability/role/dependency/deadline/budget constraints.
The four labels are represented by the evaluator's two condition families plus
metadata factor ``planning_uncertainty``.
"""
from __future__ import annotations
import argparse, copy, hashlib, json
from pathlib import Path
from typing import Any

LABELS=(
    ('nominal_deterministic','nominal',False,False),
    ('nominal_uncertain','nominal',True,False),
    ('recoverable_failure_deterministic','recoverable_failure',False,True),
    ('recoverable_failure_uncertain','recoverable_failure',True,True),
)

def read_jsonl(path:Path)->list[dict[str,Any]]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]

def source_id(row:dict[str,Any])->str:
    return str((row.get('metadata') or {}).get('source_task_id') or row.get('task_id','').split('::')[0])

def role_map(task:dict[str,Any])->dict[str,str]:
    return {str(a.get('agent_id')):str(a.get('role') or 'unspecified') for a in task.get('agents',[]) if isinstance(a,dict)}

def prepare_agents(task:dict[str,Any])->None:
    """Expose explicit role-specialty tags without altering flight geometry."""
    specialties=['camera_rgb','lidar','camera_thermal','comms_relay']
    for i,agent in enumerate(task.get('agents',[])):
        if not isinstance(agent,dict): continue
        caps={str(x) for x in agent.get('capabilities',[]) or []}
        caps.add('gnss')
        # These tags are contract-level role specialties; existing physical
        # motion remains the same projected CF2X bridge.
        caps.add(specialties[i % len(specialties)])
        agent['capabilities']=sorted(caps)
        agent['heterogeneous_role_provenance']='v6_3_contract_specialty_tag'

def eligible_ids(task:dict[str,Any], original:str, index:int, uncertain:bool)->list[str]:
    ids=[str(a.get('agent_id')) for a in task.get('agents',[]) if isinstance(a,dict) and a.get('agent_id')]
    if not ids: return [original]
    if not uncertain: return [original]+[x for x in ids if x!=original]
    # Adjacent alternatives produce genuine assignment disagreement while
    # retaining a compact, auditable candidate space.
    pos=ids.index(original) if original in ids else index % len(ids)
    return [ids[pos]] + [x for x in ids if x != ids[pos]]

def adapt_contract(task:dict[str,Any], uncertain:bool, failure:bool)->dict[str,Any]:
    contract=copy.deepcopy(task.get('contract') or {})
    mission=contract.setdefault('mission',{})
    mission.pop('required_drone_id',None)
    mission.pop('assignment_constraint',None)
    bundles=mission.get('task_bundles') or {}
    roles=role_map(task)
    ids=list(roles)
    for index,(owner,bundle_value) in enumerate(bundles.items()):
        bundle=copy.deepcopy(bundle_value)
        bid=str(bundle.get('bundle_id') or owner)
        original=str(bundle.get('drone_id') or owner)
        choices=eligible_ids(task,original,index,uncertain)
        bundle['bundle_id']=bid
        bundle['drone_id']=original
        bundle['original_owner']=original
        bundle['preferred_drone_id']=original
        bundle['eligible_drone_ids']=choices
        bundle['eligible_roles']=sorted({roles.get(x,'unspecified') for x in choices})
        # Every projected UAV has GNSS; role eligibility supplies the
        # heterogeneous constraint and avoids making failure recovery empty.
        bundle['required_capabilities']=['gnss']
        bundle['assignment_is_decision_variable']=bool(uncertain)
        bundle['planning_cost_hint']='estimated_start_to_bundle_route_distance'
        bundles[owner]=bundle
    mission['task_bundles']=bundles
    mission['planning_assignment_mode']='sampled_eligible_assignments' if uncertain else 'preferred_assignment_anchor'
    mission['planning_variables']=['bundle_to_eligible_drone_assignment'] if uncertain else []
    mission['assignment_is_not_pre_resolved']=bool(uncertain)
    contract['contract_version']='mission_contract_v6_4_shared_pool_ablation'
    contract['scenario_contract_notes']={
        'hard_constraints_shared_by_all_methods':['role_and_capability_eligibility','bundle_dependencies','deadline','shared_step_budget','immutable_completed_prefix'],
        'planning_uncertainty':bool(uncertain),
        'preferred_assignment_is_anchor_only':True,
        'method_outputs_must_not_be_used_for_scenario_selection':True,
    }
    if failure:
        policy=copy.deepcopy(contract.get('failure_policy') or {})
        target=str(policy.get('bundle_id') or 'bundle_2')
        target_bundle=next((b for b in bundles.values() if str(b.get('bundle_id'))==target),None)
        if target_bundle:
            policy['required_capabilities']=target_bundle.get('required_capabilities', ['gnss'])
            policy['required_role_family']=target_bundle.get('eligible_roles',[])
            policy['replacement_candidates']=[x for x in target_bundle.get('eligible_drone_ids',[]) if x!=str(policy.get('drone_id') or '')]
        policy['constraint_layer']='role_capability_dependency_deadline_shared_budget'
        policy['repairable']=True
        contract['failure_policy']=policy
    else:
        contract['failure_policy']={
            'type':None,'fault_injection':False,'trigger_source':'none_nominal_control','repairable':False,
            'repair_constraints':{'immutable_completed_prefix':True,'enforce_role_and_capability_eligibility':True,'enforce_bundle_dependencies':True,'enforce_shared_step_budget':True,'enforce_deadlines':True}
        }
    contract['agents']={
        'allowed_drone_ids':[str(a.get('agent_id')) for a in task.get('agents',[]) if isinstance(a,dict) and a.get('agent_id')],
        'role_by_drone':roles,
        'capabilities_by_drone':{str(a.get('agent_id')):sorted(str(x) for x in a.get('capabilities',[]) or []) for a in task.get('agents',[]) if isinstance(a,dict) and a.get('agent_id')},
    }
    return contract

def build(row:dict[str,Any], label:str, evaluator_condition:str, uncertain:bool, failure:bool, seed_index:int)->dict[str,Any]:
    task=copy.deepcopy(row)
    prepare_agents(task)
    sid=source_id(task)
    task['task_id']=f'{sid}::comparative_v6_4_{label}::run_seed_{seed_index}'
    task['split']='comparative_repair_v6_4_shared_pool_ablation'
    task['contract']=adapt_contract(task,uncertain,failure)
    meta=dict(task.get('metadata') or {})
    # The old v6.3 files varied only the label.  v6.4 uses genuinely distinct
    # episode seeds while keeping source geometry, contracts, and failure
    # injection paired across methods.
    base_seed = int(meta.get('base_episode_seed') or meta.get('episode_seed') or 0)
    digest = hashlib.sha256(f'{sid}::{base_seed}::{seed_index}'.encode()).hexdigest()
    meta['episode_seed'] = int(digest[:8], 16) & 0x7fffffff
    meta.update({'source_task_id':sid,'condition':evaluator_condition,'condition_label':label,'planning_uncertainty':uncertain,'run_seed_index':seed_index,'scenario_version':'uavbench_comparative_repair_v6_4_shared_pool','seed_derivation':'sha256(source_task_id,base_episode_seed,run_seed_index)'})
    task['metadata']=meta
    previous=copy.deepcopy(task.get('scenario') or {})
    previous_failure=copy.deepcopy(previous.get('failure_model') or {})
    if failure:
        failure_model=copy.deepcopy(task['contract'].get('failure_policy') or previous_failure)
        if not failure_model.get('type'): failure_model=previous_failure
    else:
        failure_model=task['contract']['failure_policy']
    task['scenario']={
        'scenario_version':'uavbench_comparative_repair_v6_4_shared_pool',
        'scenario_id':task['task_id'],'source_task_id':sid,'condition':evaluator_condition,'condition_label':label,
        'planning_uncertainty':uncertain,'evaluation_mode':'preexecution_plan_selection_then_single_execution',
        'method_definition':'B0+SeSE+CDS-MR','repair_module':'CDS-MR','failure_model':failure_model,
        'fault_injection':failure,'failure_trigger_source':'predeclared_bridge_failure' if failure else 'none_nominal_control',
        'paired_protocol':{'same_source_geometry':True,'same_episode_seed':True,'same_contract':True,'only_planning_uncertainty_factor_varies':True,'candidate_selection_before_execution':True,'selected_candidate_only_is_executed':True},
        'method_boundaries':{'B0':'one deterministic preexecution plan sample','SeSE':'multi-sample structural-entropy contract gate before execution','CDS-MR':'post-failure causal slice and minimal structured repair'},
    }
    return task

def main()->None:
    ap=argparse.ArgumentParser(); ap.add_argument('--input',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--manifest',type=Path,required=True); ap.add_argument('--source-count',type=int,default=6); ap.add_argument('--source-ids',default=''); ap.add_argument('--seed-index',type=int,required=True)
    a=ap.parse_args(); rows=read_jsonl(a.input)
    by={}
    for r in rows:
        if str((r.get('metadata') or {}).get('condition'))!='nominal': continue
        by.setdefault(source_id(r),r)
    requested=[x.strip() for x in a.source_ids.split(',') if x.strip()]
    selected=[by[x] for x in requested] if requested else list(by.values())[:a.source_count]
    if len(selected)!=a.source_count or any(source_id(x) not in by for x in selected): raise SystemExit(f'expected {a.source_count} selected nominal source tasks, found {len(selected)}')
    output=[]
    for row in selected:
        for label,cond,uncertain,failure in LABELS: output.append(build(row if not failure else next(x for x in rows if source_id(x)==source_id(row) and str((x.get('metadata') or {}).get('condition'))=='recoverable_failure'),label,cond,uncertain,failure,a.seed_index))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(''.join(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n' for x in output),encoding='utf-8')
    manifest={'protocol_version':'v6_4_shared_pool_ablation_v1','input':str(a.input),'input_sha256':hashlib.sha256(a.input.read_bytes()).hexdigest(),'output':str(a.output),'seed_index':a.seed_index,'source_task_ids':[source_id(x) for x in selected],'condition_labels':[x[0] for x in LABELS],'rows':len(output),'planning_uncertainty_is_metadata_factor':True,'candidate_selection_before_execution':True,'candidate_pool_generated_once_per_task_seed':True,'method_output_excluded_from_task_selection':True}
    a.manifest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))
if __name__=='__main__': main()
