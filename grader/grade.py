import os
import sys
import json
import subprocess
from datetime import datetime, timezone

repo = sys.argv[1]

def check(description):
    """Decorator to mark a function as a grading check."""
    def decorator(fn):
        fn._check = True
        fn._description = description
        return fn
    return decorator

# ─── Helpers ──────────────────────────────────────────────────────────────────

def file_contains(path, pattern):
    try:
        with open(os.path.join(repo, path)) as f:
            return pattern in f.read()
    except FileNotFoundError:
        return False

def file_exists(path):
    return os.path.isfile(os.path.join(repo, path))

def dir_exists(path):
    return os.path.isdir(os.path.join(repo, path))

def read_file(path):
    try:
        with open(os.path.join(repo, path)) as f:
            return f.read()
    except FileNotFoundError:
        return ""

def git_history_contains(pattern):
    result = subprocess.run(
        ["git", "-C", repo, "log", "--all", "-p"],
        capture_output=True, text=True
    )
    return pattern in result.stdout

def compose_service_block(service):
    """Returns the text block of a service in docker-compose.yml."""
    content = read_file("docker-compose.yml")
    lines = content.split("\n")
    capturing = False
    block = []
    for line in lines:
        if line.strip().startswith(f"{service}:"):
            capturing = True
        elif capturing and line and not line.startswith(" ") and not line.startswith("\t"):
            break
        if capturing:
            block.append(line)
    return "\n".join(block)

# ─── Containerization Checks ──────────────────────────────────────────────────

def check_multistage_builds():
    results = []
    for service in ["api", "frontend"]:
        path = f"{service}/Dockerfile"
        content = read_file(path)
        from_count = content.count("\nFROM ") + (1 if content.startswith("FROM ") else 0)
        if from_count >= 2:
            results.append({"service": service, "status": "pass"})
        else:
            results.append({"service": service, "status": "fail",
                           "note": f"Only {from_count} FROM stage(s) found"})
    earned = sum(4 if r["status"] == "pass" else 0 for r in results)
    return earned, 8, results

def check_nonroot_users():
    results = []
    for service in ["api", "worker", "frontend"]:
        path = f"{service}/Dockerfile"
        content = read_file(path)
        has_user = "USER " in content
        has_adduser = "adduser" in content or "addgroup" in content or "useradd" in content
        if has_user and has_adduser:
            results.append({"service": service, "status": "pass"})
        elif has_user:
            results.append({"service": service, "status": "partial",
                           "note": "USER instruction found but no user creation — just USER 1000"})
        else:
            results.append({"service": service, "status": "fail",
                           "note": "No USER instruction found"})
    pass_count = sum(1 for r in results if r["status"] == "pass")
    partial_count = sum(1 for r in results if r["status"] == "partial")
    earned = (pass_count * 1) + (partial_count * 0)  # partial gets no marks — must be named user
    return pass_count * (5 // 3), 5, results

def check_healthchecks():
    results = []
    for service in ["api", "worker", "frontend"]:
        path = f"{service}/Dockerfile"
        content = read_file(path)
        if "HEALTHCHECK" not in content:
            results.append({"service": service, "status": "fail",
                           "note": "No HEALTHCHECK instruction"})
        elif "echo" in content.split("HEALTHCHECK")[1].split("\n")[0]:
            results.append({"service": service, "status": "partial",
                           "note": "HEALTHCHECK found but uses 'echo' — not a real health test"})
        else:
            results.append({"service": service, "status": "pass"})
    pass_count = sum(1 for r in results if r["status"] == "pass")
    partial_count = sum(1 for r in results if r["status"] == "partial")
    earned = (pass_count * 2) + (partial_count * 1)
    return min(earned, 5), 5, results

def check_redis_not_exposed():
    compose = read_file("docker-compose.yml")
    # Find redis block and check if ports: appears within it
    lines = compose.split("\n")
    in_redis = False
    redis_has_ports = False
    for line in lines:
        if "redis:" in line and not line.strip().startswith("#"):
            in_redis = True
        elif in_redis and line and not line.startswith(" ") and not line.startswith("\t"):
            in_redis = False
        if in_redis and "ports:" in line:
            redis_has_ports = True
    if redis_has_ports:
        return 0, 5, [{"status": "fail", "note": "Redis has a ports: block — exposed on host"}]
    return 5, 5, [{"status": "pass"}]

def check_named_network():
    compose = read_file("docker-compose.yml")
    has_networks_section = "networks:" in compose
    uses_default = "network_mode: host" in compose
    if has_networks_section and not uses_default:
        return 5, 5, [{"status": "pass"}]
    return 0, 5, [{"status": "fail", "note": "No named network defined or default network used"}]

def check_depends_on_healthy():
    compose = read_file("docker-compose.yml")
    has_condition = "condition: service_healthy" in compose
    has_depends = "depends_on:" in compose
    if has_depends and has_condition:
        return 4, 4, [{"status": "pass"}]
    elif has_depends:
        return 0, 4, [{"status": "fail",
                       "note": "depends_on present but missing condition: service_healthy"}]
    return 0, 4, [{"status": "fail", "note": "No depends_on found in compose file"}]

def check_resource_limits():
    compose = read_file("docker-compose.yml")
    has_limits = "limits:" in compose
    has_cpus = "cpus:" in compose
    has_memory = "memory:" in compose
    if has_limits and has_cpus and has_memory:
        return 4, 4, [{"status": "pass"}]
    elif has_limits:
        return 2, 4, [{"status": "partial", "note": "limits: found but missing cpus or memory"}]
    return 0, 4, [{"status": "fail", "note": "No resource limits defined"}]

def check_env_hygiene():
    has_example = file_exists(".env.example")
    gitignore = read_file(".gitignore")
    env_ignored = ".env" in gitignore
    compose = read_file("docker-compose.yml")
    has_hardcoded = any(word in compose for word in ["password:", "secret:", "changeme"])

    notes = []
    score = 0
    if has_example:
        score += 2
    else:
        notes.append(".env.example missing")
    if env_ignored:
        score += 2
    else:
        notes.append(".env not in .gitignore")
    if not has_hardcoded:
        score += 1
    else:
        notes.append("Possible hardcoded values in docker-compose.yml")

    status = "pass" if score == 5 else ("partial" if score > 0 else "fail")
    return score, 5, [{"status": status, "note": "; ".join(notes) if notes else None}]

def check_restart_policies():
    compose = read_file("docker-compose.yml")
    has_restart = "restart:" in compose
    # Worker should have 'always', others 'unless-stopped'
    worker_always = "always" in compose_service_block("worker")
    if has_restart and worker_always:
        return 3, 3, [{"status": "pass"}]
    elif has_restart:
        return 1, 3, [{"status": "partial",
                       "note": "restart: found but worker should use 'always', not 'unless-stopped'"}]
    return 0, 3, [{"status": "fail", "note": "No restart policies defined"}]

# ─── Bug Fix Checks ───────────────────────────────────────────────────────────

def check_bugs():
    bugs = []

    # Bug 1: Redis host env var in API
    api = read_file("api/main.py")
    if 'host="localhost"' in api or "host='localhost'" in api:
        bugs.append({"bug": "Redis hardcoded host fixed in API", "earned": 0, "status": "fail",
                     "note": "Still using hardcoded localhost"})
    else:
        bugs.append({"bug": "Redis hardcoded host fixed in API", "earned": 1, "status": "pass"})

    # Bug 2: Queue name consistency
    api_queue = '"jobs"' in api or "'jobs'" in api
    worker = read_file("worker/worker.py")
    worker_queue = '"jobs"' in worker or "'jobs'" in worker
    if api_queue and worker_queue:
        bugs.append({"bug": "Queue name 'jobs' consistent in API and worker",
                     "earned": 1, "status": "pass"})
    else:
        note = []
        if not api_queue:
            note.append("API still uses wrong queue name")
        if not worker_queue:
            note.append("Worker still uses wrong queue name")
        bugs.append({"bug": "Queue name 'jobs' consistent in API and worker",
                     "earned": 0, "status": "fail", "note": "; ".join(note)})

    # Bug 3: Health check endpoint
    if "@app.get(\"/health\")" in api or "@app.get('/health')" in api:
        bugs.append({"bug": "Health check endpoint added to API", "earned": 1, "status": "pass"})
    else:
        bugs.append({"bug": "Health check endpoint added to API", "earned": 0, "status": "fail",
                     "note": "No /health route found in api/main.py"})

    # Bug 4: Pinned deps
    api_reqs = read_file("api/requirements.txt")
    worker_reqs = read_file("worker/requirements.txt")
    api_pinned = "==" in api_reqs
    worker_pinned = "==" in worker_reqs
    if api_pinned and worker_pinned:
        bugs.append({"bug": "Dependencies pinned in both services", "earned": 1, "status": "pass"})
    else:
        note = []
        if not api_pinned: note.append("api/requirements.txt unpinned")
        if not worker_pinned: note.append("worker/requirements.txt unpinned")
        bugs.append({"bug": "Dependencies pinned in both services", "earned": 0,
                     "status": "fail", "note": "; ".join(note)})

    # Bug 5: .env secret removed from git history
    if git_history_contains("supersecretpassword"):
        bugs.append({"bug": "Committed .env secret removed from history", "earned": 0,
                     "status": "fail", "note": "Secret found in git history"})
    elif file_exists("api/.env"):
        bugs.append({"bug": "Committed .env secret removed from history", "earned": 0,
                     "status": "fail", "note": "api/.env still present in repo"})
    else:
        bugs.append({"bug": "Committed .env secret removed from history",
                     "earned": 1, "status": "pass"})

    # Bug 6: Graceful SIGTERM
    if "signal.signal" in worker and "SIGTERM" in worker:
        if "shutdown" in worker:
            bugs.append({"bug": "Graceful SIGTERM handling in worker",
                         "earned": 1, "status": "pass"})
        else:
            bugs.append({"bug": "Graceful SIGTERM handling in worker", "earned": 0,
                         "status": "partial",
                         "note": "SIGTERM handler found but no shutdown flag checked in loop"})
    else:
        bugs.append({"bug": "Graceful SIGTERM handling in worker", "earned": 0,
                     "status": "fail", "note": "No signal handler found"})

    # Bug 9: Frontend API_URL env var
    frontend = read_file("frontend/app.js")
    if "process.env.API_URL" in frontend:
        bugs.append({"bug": "Frontend API_URL uses environment variable",
                     "earned": 1, "status": "pass"})
    else:
        bugs.append({"bug": "Frontend API_URL uses environment variable", "earned": 0,
                     "status": "fail", "note": "API_URL still hardcoded"})

    # Bug 11: Frontend 0.0.0.0
    if "0.0.0.0" in frontend:
        bugs.append({"bug": "Frontend listens on 0.0.0.0", "earned": 1, "status": "pass"})
    else:
        bugs.append({"bug": "Frontend listens on 0.0.0.0", "earned": 0, "status": "fail",
                     "note": "listen() called without 0.0.0.0 binding"})

    # Bug 10: Error logging
    if "console.error" in frontend:
        bugs.append({"bug": "Error logging in frontend catch blocks",
                     "earned": 1, "status": "pass"})
    else:
        bugs.append({"bug": "Error logging in frontend catch blocks", "earned": 0,
                     "status": "fail", "note": "No console.error in catch blocks"})

    # Bug docs: FIXES.md
    fixes_md = read_file("FIXES.md")
    bug_count = fixes_md.lower().count("bug")
    if bug_count >= 9:
        bugs.append({"bug": "FIXES.md documents all bugs with file, line, explanation",
                     "earned": 1, "status": "pass"})
    elif bug_count > 0:
        bugs.append({"bug": "FIXES.md documents all bugs with file, line, explanation",
                     "earned": 0, "status": "partial",
                     "note": f"Only {bug_count} bug(s) documented — expected 11"})
    else:
        bugs.append({"bug": "FIXES.md documents all bugs with file, line, explanation",
                     "earned": 0, "status": "fail", "note": "FIXES.md missing or empty"})

    return bugs

# ─── CI/CD Checks ─────────────────────────────────────────────────────────────

def get_pipeline_yaml():
    import glob
    files = glob.glob(os.path.join(repo, ".github/workflows/*.yml"))
    content = ""
    for f in files:
        with open(f) as fh:
            content += fh.read()
    return content

def check_lint_stages():
    pipeline = get_pipeline_yaml()
    results = []
    if "flake8" in pipeline and ".flake8" in read_file(".flake8"):
        results.append({"tool": "flake8", "status": "pass"})
    elif "flake8" in pipeline:
        results.append({"tool": "flake8", "status": "partial",
                        "note": "flake8 present but no .flake8 config file found"})
    else:
        results.append({"tool": "flake8", "status": "fail", "note": "flake8 not in pipeline"})

    if "eslint" in pipeline:
        results.append({"tool": "eslint", "status": "pass"})
    else:
        results.append({"tool": "eslint", "status": "fail", "note": "eslint not in pipeline"})

    if "hadolint" in pipeline:
        results.append({"tool": "hadolint", "status": "pass"})
    else:
        results.append({"tool": "hadolint", "status": "fail", "note": "hadolint not in pipeline"})

    earned = sum({"pass": 3, "partial": 1, "fail": 0}[r["status"]] for r in results)
    return earned, 9, results

def check_failfast():
    pipeline = get_pipeline_yaml()
    has_needs = pipeline.count("needs:") >= 3
    if has_needs:
        return 5, 5, [{"status": "pass"}]
    return 0, 5, [{"status": "fail",
                   "note": "Jobs don't declare 'needs:' — stages can run in parallel or independently"}]

def check_unit_tests():
    pipeline = get_pipeline_yaml()
    has_pytest = "pytest" in pipeline
    test_files = [f for f in os.listdir(os.path.join(repo, "tests"))
                  if f.startswith("test_") and f.endswith(".py")] if dir_exists("tests") else []
    has_mock = any(
        "mock" in read_file(f"tests/{f}").lower() or "fakeredis" in read_file(f"tests/{f}").lower()
        for f in test_files
    )
    has_coverage = "--cov" in pipeline or "coverage" in pipeline
    has_artifact = "upload-artifact" in pipeline and "coverage" in pipeline

    score = 0
    notes = []
    if has_pytest and test_files: score += 3
    else: notes.append("pytest not in pipeline or no test files found")
    if has_mock: score += 2
    else: notes.append("No Redis mock detected in tests")
    if has_coverage: score += 1
    else: notes.append("No coverage report generated")
    if has_artifact: score += 1
    else: notes.append("Coverage not uploaded as artifact")

    status = "pass" if score >= 6 else ("partial" if score > 0 else "fail")
    return score, 7, [{"status": status, "note": "; ".join(notes) if notes else None}]

def check_build_stage():
    pipeline = get_pipeline_yaml()
    has_sha_tag = "github.sha" in pipeline
    has_latest = "latest" in pipeline
    has_registry = "registry:2" in pipeline or "localhost:5000" in pipeline
    has_cache = "cache-from" in pipeline

    score = 0
    notes = []
    if has_sha_tag and has_latest: score += 2
    else: notes.append("Images not tagged with both SHA and latest")
    if has_registry: score += 2
    else: notes.append("No local registry service found")
    if has_cache: score += 1
    else: notes.append("No Docker layer caching configured")

    status = "pass" if score == 5 else ("partial" if score > 0 else "fail")
    return score, 5, [{"status": status, "note": "; ".join(notes) if notes else None}]

def check_security_scan():
    pipeline = get_pipeline_yaml()
    has_trivy = "trivy" in pipeline
    has_critical = "CRITICAL" in pipeline
    has_sarif = "sarif" in pipeline.lower()
    has_artifact = "upload-artifact" in pipeline and "sarif" in pipeline.lower()

    score = 0
    notes = []
    if has_trivy: score += 2
    else: notes.append("Trivy not in pipeline")
    if has_critical: score += 1
    else: notes.append("Pipeline doesn't fail on CRITICAL severity")
    if has_sarif: score += 1
    else: notes.append("SARIF output not configured")
    if has_artifact: score += 1
    else: notes.append("Scan results not uploaded as artifact")

    status = "pass" if score == 5 else ("partial" if score > 0 else "fail")
    return score, 5, [{"status": status, "note": "; ".join(notes) if notes else None}]

def check_integration_test():
    pipeline = get_pipeline_yaml()
    has_compose_up = "docker compose up" in pipeline or "docker-compose up" in pipeline
    has_timeout = "timeout" in pipeline or "MAX_WAIT" in read_file("tests/integration_test.sh")
    has_teardown = "always()" in pipeline
    has_test_script = file_exists("tests/integration_test.sh")

    score = 0
    notes = []
    if has_compose_up: score += 2
    else: notes.append("No docker compose up in pipeline")
    if has_test_script: score += 2
    else: notes.append("No integration_test.sh found")
    if has_timeout: score += 2
    else: notes.append("No timeout enforcement found")
    if has_teardown: score += 1
    else: notes.append("No always() teardown condition")

    status = "pass" if score >= 6 else ("partial" if score > 0 else "fail")
    return score, 7, [{"status": status, "note": "; ".join(notes) if notes else None}]

def check_deploy_stage():
    pipeline = get_pipeline_yaml()
    main_only = "refs/heads/main" in pipeline or "branches: [main]" in pipeline
    has_rolling = file_exists("scripts/rolling_deploy.sh")
    rolling_content = read_file("scripts/rolling_deploy.sh")
    health_gated = "healthy" in rolling_content and "sleep" not in rolling_content.replace("start_period", "")
    deploy_has_needs = "needs:" in pipeline.split("deploy:")[1] if "deploy:" in pipeline else False

    score = 0
    notes = []
    if main_only: score += 2
    else: notes.append("Deploy not restricted to main branch")
    if has_rolling: score += 1
    else: notes.append("No rolling_deploy.sh script found")
    if health_gated: score += 2
    else: notes.append("Rolling deploy does not gate on health check — uses sleep or no check")

    status = "pass" if score == 5 else ("partial" if score > 0 else "fail")
    return score, 5, [{"status": status, "note": "; ".join(notes) if notes else None}]

def check_pipeline_hygiene():
    pipeline = get_pipeline_yaml()
    pinned = "@v" in pipeline and "@main" not in pipeline and "@master" not in pipeline
    uses_secrets = "${{ secrets." in pipeline
    named_steps = pipeline.count("name:") >= 10

    score = 0
    notes = []
    if pinned: score += 1
    else: notes.append("Action versions not pinned — @main or @master found")
    if uses_secrets: score += 1
    else: notes.append("No secrets references found — credentials may be hardcoded")
    if named_steps: score += 1
    else: notes.append("Steps missing name: fields")

    status = "pass" if score == 3 else ("partial" if score > 0 else "fail")
    return score, 3, [{"status": status, "note": "; ".join(notes) if notes else None}]

# ─── Documentation Checks ─────────────────────────────────────────────────────

def check_readme():
    readme = read_file("README.md")
    has_prereqs = any(w in readme.lower() for w in ["prerequisite", "requirements", "before you"])
    has_commands = "docker" in readme and "```" in readme
    has_structure = len(readme) > 300
    score = sum([has_prereqs, has_commands, has_structure]) + 2
    notes = []
    if not has_prereqs: notes.append("No prerequisites section")
    if not has_commands: notes.append("No copyable commands found")
    status = "pass" if score >= 4 else "partial"
    return min(score, 5), 5, [{"status": status, "note": "; ".join(notes) if notes else None}]

def check_fixes_md():
    fixes = read_file("FIXES.md")
    if not fixes:
        return 0, 5, [{"status": "fail", "note": "FIXES.md not found"}]
    has_lines = any(w in fixes for w in ["line", "Line", "L."])
    has_files = any(ext in fixes for ext in [".py", ".js", ".yml"])
    score = 0
    notes = []
    if len(fixes) > 500: score += 2
    else: notes.append("FIXES.md seems very short")
    if has_lines: score += 1
    else: notes.append("No line number references found")
    if has_files: score += 2
    else: notes.append("No file references found")
    status = "pass" if score == 5 else ("partial" if score > 0 else "fail")
    return score, 5, [{"status": status, "note": "; ".join(notes) if notes else None}]

# ─── Penalty Checks ───────────────────────────────────────────────────────────

def check_penalties():
    penalties = []
    pipeline = get_pipeline_yaml()

    if git_history_contains("supersecretpassword") or file_contains("api/.env", "supersecret"):
        penalties.append({"reason": "Hardcoded secret found in repo or git history", "amount": 15})

    if "deploy:" in pipeline:
        deploy_section = pipeline.split("deploy:")[1]
        if "needs:" not in deploy_section[:500]:
            penalties.append({"reason": "Deploy stage has no needs: — runs even if tests fail",
                               "amount": 10})

    if file_exists("api/.env") and not file_contains(".gitignore", ".env"):
        penalties.append({"reason": ".env file committed to repository", "amount": 10})

    if "always()" not in pipeline and "docker compose down" in pipeline:
        penalties.append({"reason": "Integration test stack not torn down after test", "amount": 5})

    if "github.sha" not in pipeline and "latest" in pipeline:
        penalties.append({"reason": "Images tagged latest only — no SHA tag", "amount": 3})

    if not penalties:
        penalties.append({"reason": "No penalties triggered", "amount": 0})

    return penalties

# ─── Build Report ─────────────────────────────────────────────────────────────

def build_section(label, max_score, items, bugs=None):
    earned = sum(i.get("earned", 0) for i in items) + (
        sum(b["earned"] for b in bugs) if bugs else 0
    )
    return {
        "label": label,
        "max": max_score,
        "earned": min(earned, max_score),
        "items": [i for i in items if "criterion" in i],
        **({"bug_fixes": bugs} if bugs else {})
    }

def run():
    # Containerization
    ms_score, ms_max, ms_detail = check_multistage_builds()
    nr_score, nr_max, nr_detail = check_nonroot_users()
    hc_score, hc_max, hc_detail = check_healthchecks()
    re_score, re_max, re_detail = check_redis_not_exposed()
    nn_score, nn_max, nn_detail = check_named_network()
    do_score, do_max, do_detail = check_depends_on_healthy()
    rl_score, rl_max, rl_detail = check_resource_limits()
    eh_score, eh_max, eh_detail = check_env_hygiene()
    rp_score, rp_max, rp_detail = check_restart_policies()
    bugs = check_bugs()

    container_items = [
        {"criterion": "Multi-stage builds — API and frontend", "max": ms_max, "earned": ms_score,
         "status": ms_detail[0]["status"] if len(ms_detail)==1 else "mixed", "detail": ms_detail},
        {"criterion": "Non-root user in all Dockerfiles", "max": nr_max, "earned": nr_score,
         "status": nr_detail[0]["status"] if len(nr_detail)==1 else "mixed", "detail": nr_detail},
        {"criterion": "HEALTHCHECK in all Dockerfiles", "max": hc_max, "earned": hc_score,
         "status": hc_detail[0]["status"] if len(hc_detail)==1 else "mixed", "detail": hc_detail},
        {"criterion": "Named bridge network, Redis not exposed to host", "max": re_max+nn_max,
         "earned": re_score+nn_score,
         "status": "pass" if re_score+nn_score == re_max+nn_max else "fail",
         "detail": re_detail + nn_detail},
        {"criterion": "depends_on with service_healthy condition", "max": do_max, "earned": do_score,
         "status": do_detail[0]["status"], "detail": do_detail},
        {"criterion": "Resource limits on all services", "max": rl_max, "earned": rl_score,
         "status": rl_detail[0]["status"], "detail": rl_detail},
        {"criterion": "Env var hygiene — .env.example, no hardcoded values", "max": eh_max,
         "earned": eh_score, "status": eh_detail[0]["status"], "detail": eh_detail},
        {"criterion": "Restart policies appropriate per service", "max": rp_max, "earned": rp_score,
         "status": rp_detail[0]["status"], "detail": rp_detail},
    ]
    container_raw = sum(i["earned"] for i in container_items) + sum(b["earned"] for b in bugs)

    # CI/CD
    lint_score, lint_max, lint_detail = check_lint_stages()
    ff_score, ff_max, ff_detail = check_failfast()
    ut_score, ut_max, ut_detail = check_unit_tests()
    build_score, build_max, build_detail = check_build_stage()
    sec_score, sec_max, sec_detail = check_security_scan()
    int_score, int_max, int_detail = check_integration_test()
    dep_score, dep_max, dep_detail = check_deploy_stage()
    hyg_score, hyg_max, hyg_detail = check_pipeline_hygiene()

    cicd_items = [
        {"criterion": "Lint — Python, JavaScript, Dockerfiles", "max": lint_max,
         "earned": lint_score, "status": lint_detail[0]["status"] if len(lint_detail)==1 else "mixed",
         "detail": lint_detail},
        {"criterion": "Fail-fast behaviour", "max": ff_max, "earned": ff_score,
         "status": ff_detail[0]["status"], "detail": ff_detail},
        {"criterion": "Unit tests — meaningful, mocked, coverage uploaded", "max": ut_max,
         "earned": ut_score, "status": ut_detail[0]["status"], "detail": ut_detail},
        {"criterion": "Build — SHA + latest, registry, caching", "max": build_max,
         "earned": build_score, "status": build_detail[0]["status"], "detail": build_detail},
        {"criterion": "Security scan — Trivy, CRITICAL gate, SARIF", "max": sec_max,
         "earned": sec_score, "status": sec_detail[0]["status"], "detail": sec_detail},
        {"criterion": "Integration test — submit, poll, timeout, teardown", "max": int_max,
         "earned": int_score, "status": int_detail[0]["status"], "detail": int_detail},
        {"criterion": "Deploy — main only, rolling update, health-gated", "max": dep_max,
         "earned": dep_score, "status": dep_detail[0]["status"], "detail": dep_detail},
        {"criterion": "Pipeline hygiene — pinned, secrets, named steps", "max": hyg_max,
         "earned": hyg_score, "status": hyg_detail[0]["status"], "detail": hyg_detail},
    ]

    # Docs
    rm_score, rm_max, rm_detail = check_readme()
    fm_score, fm_max, fm_detail = check_fixes_md()
    doc_items = [
        {"criterion": "README.md", "max": rm_max, "earned": rm_score,
         "status": rm_detail[0]["status"], "detail": rm_detail},
        {"criterion": "FIXES.md", "max": fm_max, "earned": fm_score,
         "status": fm_detail[0]["status"], "detail": fm_detail},
    ]

    penalties = check_penalties()
    total_penalty = sum(p["amount"] for p in penalties)

    raw = (
        min(container_raw, 45) +
        sum(i["earned"] for i in cicd_items) +
        sum(i["earned"] for i in doc_items)
    )
    final = max(0, raw - total_penalty)

    needs_review = [
        "HEALTHCHECK content — automated check verifies instruction exists but not what it tests",
        "Unit test quality — tests counted but mentor should verify they are non-trivial",
        "FIXES.md explanation depth — presence checked but quality needs human review",
    ]

    report = {
        "meta": {
            "intern_github": os.getenv("INTERN_GITHUB", "unknown"),
            "intern_slack_id": os.getenv("INTERN_SLACK_ID", "unknown"),
            "repo_url": os.getenv("INTERN_REPO", "unknown"),
            "commit_sha": os.getenv("COMMIT_SHA", "unknown"),
            "graded_at": datetime.now(timezone.utc).isoformat(),
            "workflow_run_url": os.getenv("WORKFLOW_RUN_URL", "unknown"),
        },
        "sections": {
            "containerization": {
                "label": "Containerization",
                "max": 45,
                "earned": min(container_raw, 45),
                "items": container_items,
                "bug_fixes": bugs,
            },
            "cicd": {
                "label": "CI/CD Pipeline",
                "max": 45,
                "earned": sum(i["earned"] for i in cicd_items),
                "items": cicd_items,
            },
            "documentation": {
                "label": "Documentation",
                "max": 10,
                "earned": rm_score + fm_score,
                "items": doc_items,
            },
        },
        "penalties": penalties,
        "totals": {
            "raw_score": raw,
            "penalties": total_penalty,
            "final_score": final,
            "max_score": 100,
            "passed": final >= 65,
            "distinction": final >= 85,
            "needs_mentor_review": needs_review,
        },
    }

    with open("grade_report.json", "w") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))
    print(f"\n{'='*50}")
    print(f"FINAL SCORE: {final}/100 — {'PASS' if final >= 65 else 'FAIL'}")
    print(f"{'='*50}")

if __name__ == "__main__":
    run()