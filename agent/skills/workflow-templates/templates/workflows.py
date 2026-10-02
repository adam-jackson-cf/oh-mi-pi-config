# pyright: reportUndefinedVariable=false
# agent and wait are OMP eval kernel globals, injected when this file is %load-ed.
"""Owner additions to OMP's native `workflowz` / `jevify` / `orchestrate` contracts.

Load with `%load ~/.omp/agent/skills/workflow-templates/templates/workflows.py`. Both templates are
dependency-coupled (each step needs the previous result), the case where the native contract allows
`agent()` handles with `wait()`. Independent fan-out belongs in a native `workpool()`.
"""
import json
import subprocess

MAX_AGENTS = 16


class WorkflowError(RuntimeError):
    pass


def run_check(cmd, cwd):
    """Deterministic gate. Returns (ok, last 4000 chars of output)."""
    p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=900)
    return p.returncode == 0, (p.stdout + p.stderr)[-4000:]


FINDING = {"type": "object", "required": ["title", "evidence", "severity"], "additionalProperties": False,
           "properties": {"title": {"type": "string"}, "evidence": {"type": "string"},
                          "severity": {"enum": ["high", "medium", "low"]}}}
VERDICT = {"type": "object", "required": ["verdict", "findings"], "additionalProperties": False, "properties": {
    "verdict": {"enum": ["pass", "fail"]}, "findings": {"type": "array", "items": FINDING}}}


def adversarial_verify(objective, repo, check_cmd, max_rounds=3, author="task", judge_agent="reviewer"):
    """Implement -> repo check must pass -> independent review session -> fix, until pass or max_rounds.
    Each agent() spawn is a fresh session with no shared context; same model family is allowed."""
    history, feedback = [], ""
    for rnd in range(1, max_rounds + 1):
        brief = (f"Work in {repo}. Objective: {objective}\n" +
                 (f"A previous attempt is in the working tree. Fix these problems:\n{feedback}\n" if feedback else "") +
                 "Edit only what the objective needs. Do not run gates or commit.")
        wait([agent(brief, agent=author, label=f"Impl{rnd}")], raise_errors=False)
        ok, out = run_check(check_cmd, repo)
        if not ok:  # deterministic failure: no LLM review this round
            history.append({"round": rnd, "check": "fail"})
            feedback = f"`{check_cmd}` failed:\n{out}"
            continue
        review = wait([agent(f"Try to break the uncommitted change in {repo} (git diff). Objective: {objective}\n"
                             f"`{check_cmd}` passes. Report only defects that violate the objective, with evidence. "
                             "verdict=pass when none.", agent=judge_agent, label=f"Break{rnd}", schema=VERDICT)],
                      raise_errors=False)[0]
        history.append({"round": rnd, "check": "pass", "review": review})
        if isinstance(review, dict) and review["verdict"] == "pass":
            return {"status": "pass", "rounds": rnd, "history": history}
        feedback = json.dumps(review.get("findings", []) if isinstance(review, dict) else str(review))
    return {"status": "exhausted", "rounds": max_rounds, "history": history}


def blind_label(batch_files, rubric_path, labels, labellers=("reviewer", "task")):
    """Frozen JSON batches of {id, ...} cases; two blind labellers (distinct agents, separate sessions) per batch.
    Returns agreed labels and disputes for the orchestrator to settle; a human confirms."""
    if labellers[0] == labellers[1]:
        raise WorkflowError(f"labellers must be two distinct agents, got {labellers!r}")
    if 2 * len(batch_files) > MAX_AGENTS:
        raise WorkflowError(f"{2 * len(batch_files)} labellers exceeds cap {MAX_AGENTS}; merge batches")
    schema = {"type": "object", "required": ["cases"], "additionalProperties": False, "properties": {"cases": {
        "type": "array", "items": {"type": "object", "required": ["id", "label", "evidence", "rationale"],
                                   "additionalProperties": False, "properties": {
                                       "id": {"type": "string"}, "label": {"enum": list(labels)},
                                       "evidence": {"type": "string"}, "rationale": {"type": "string"}}}}}}
    jobs = [(who, agent(f"Read-only. Label every case in {f} with the rubric {rubric_path}. Read only those two "
                        "files. Quote the deciding evidence; rationale in 1-2 plain sentences.",
                        agent=who, label=f"Label{n:02d}{who[:3]}", schema=schema))
            for n, f in enumerate(batch_files) for who in labellers]
    results = wait([h for _, h in jobs], raise_errors=False)
    votes = {}
    for (who, _), r in zip(jobs, results):
        for c in (r.get("cases", []) if isinstance(r, dict) else []):
            votes.setdefault(c["id"], {})[who] = c
    agreed = {i: v[labellers[0]]["label"] for i, v in votes.items()
              if len(v) == 2 and v[labellers[0]]["label"] == v[labellers[1]]["label"]}
    return {"agreed": agreed, "disputed": {i: v for i, v in votes.items() if i not in agreed}}
