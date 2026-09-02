import json
import os
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def compose_service(compose: str, service: str) -> str:
    match = re.search(
        rf"^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [\w-]+:|^volumes:|\Z)",
        compose,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"Missing Compose service: {service}"
    return match.group("body")


def test_caddy_only_publishes_the_portal() -> None:
    lab_caddyfile = (ROOT / "Caddyfile.lab").read_text()
    production_caddyfile = (ROOT / "Caddyfile.production").read_text()
    compose = (ROOT / "compose.yml").read_text()

    for contents in (lab_caddyfile, production_caddyfile):
        assert "reverse_proxy mlflow" not in contents
        assert "mlflow" not in contents.lower()
        assert "reverse_proxy portal" in contents

    assert ":80 {" in production_caddyfile
    assert "tls " not in production_caddyfile
    assert 'ports: ["80:80"]' in compose
    assert '"443:443"' not in compose
    lab_environment = (ROOT / "config" / "lab.env.example").read_text()
    assert "INVOICEOPS_DB_PATH=/app/var/invoiceops.db" in lab_environment
    assert "INVOICEOPS_DATA_VOLUME=/srv/invoiceops/var" in lab_environment
    for deployment_file in (
        ROOT / "scripts" / "lab-preflight.sh",
        ROOT / ".github" / "workflows" / "deploy-services.yml",
        ROOT / "config" / "lab.env.example",
    ):
        contents = deployment_file.read_text()
        assert "PUBLIC_HOST" not in contents
        assert "TLS_EMAIL" not in contents


def test_mlflow_cannot_be_reexposed_by_the_deployment_configuration() -> None:
    compose = (ROOT / "compose.yml").read_text()
    mlflow_production = compose.split("  mlflow-production:", maxsplit=1)[1].split(
        "  proxy-production:", maxsplit=1
    )[0]
    deployment_files = (
        ROOT / "scripts" / "lab-preflight.sh",
        ROOT / ".github" / "workflows" / "deploy-services.yml",
        ROOT / "config" / "lab.env.example",
    )

    assert "ports:" not in mlflow_production
    assert "MLFLOW_PUBLIC_HOST" not in compose
    for deployment_file in deployment_files:
        assert "MLFLOW_PUBLIC_HOST" not in deployment_file.read_text()


def test_classroom_compose_profile_provides_the_local_teaching_stack() -> None:
    compose = (ROOT / "compose.yml").read_text()

    for service in (
        "portal-lab",
        "classroom-db-bootstrap",
        "postgres",
        "minio",
        "mlflow-lab",
        "model-bootstrap",
        "model-api",
        "proxy-lab",
        "jupyter-classroom",
        "anvil-classroom",
    ):
        service_definition = compose_service(compose, service)
        assert '"classroom"' in service_definition

    portal_lab = compose_service(compose, "portal-lab")
    assert "start_period: 90s" in portal_lab
    assert "classroom-db-bootstrap:" in portal_lab
    assert "condition: service_completed_successfully" in portal_lab

    bootstrap = compose_service(compose, "classroom-db-bootstrap")
    assert 'profiles: ["manual", "local", "full-lab", "classroom"]' in bootstrap
    assert '"invoiceops.legacy.classroom_bootstrap"' in bootstrap
    assert 'INVOICEOPS_DB_PATH: /app/var/invoiceops.db' in bootstrap
    assert 'invoice-data:/app/var' in bootstrap

    portal_production = compose_service(compose, "portal-production")
    assert '"production"' not in bootstrap
    assert "classroom-db-bootstrap:" not in portal_production

    model_api = compose_service(compose, "model-api")
    assert "model-bootstrap:" in model_api
    assert "condition: service_completed_successfully" in model_api

    minio_init = compose_service(compose, "minio-init")
    assert "command:\n      - >-" in minio_init
    assert "mc mb --ignore-existing lab/mlflow-artifacts" in minio_init


def test_minio_healthchecks_use_curl_available_in_the_pinned_image() -> None:
    compose = (ROOT / "compose.yml").read_text()
    expected_healthcheck = (
        'test: ["CMD-SHELL", "curl --fail --silent --show-error --output /dev/null '
        'http://127.0.0.1:9000/minio/health/live"]'
    )

    for service in ("minio", "minio-production"):
        definition = compose_service(compose, service)
        assert expected_healthcheck in definition
        assert "wget" not in definition


def test_mlflow_services_share_a_reproducible_postgres_and_s3_image() -> None:
    compose = (ROOT / "compose.yml").read_text()
    dockerfile = (ROOT / "Dockerfile.mlflow").read_text()

    for service in ("mlflow-lab", "mlflow-production"):
        definition = compose_service(compose, service)
        assert "image: invoiceops-mlflow:v2.22.0" in definition
        assert "context: ." in definition
        assert "dockerfile: Dockerfile.mlflow" in definition

    assert "FROM ghcr.io/mlflow/mlflow:v2.22.0" in dockerfile
    assert "boto3==1.37.38" in dockerfile
    assert "psycopg2-binary==2.9.10" in dockerfile


def test_classroom_compose_binds_student_services_to_localhost() -> None:
    compose = (ROOT / "compose.yml").read_text()

    for port in ("5000", "8001", "8080", "8545"):
        assert f'"127.0.0.1:{port}:{port}"' in compose
    assert '"127.0.0.1:8889:8888"' in compose
    assert '"8000:8000"' not in compose

    jupyter = compose_service(compose, "jupyter-classroom")
    assert "--ServerApp.token=" in jupyter
    assert "--ServerApp.password=" in jupyter
    assert "./notebooks:/app/notebooks" in jupyter
    assert "target: classroom" in jupyter
    assert "INVOICEOPS_EVM_RPC_URL: http://anvil-classroom:8545" in jupyter
    assert "HOME: /tmp/invoiceops" in jupyter
    assert "JUPYTER_RUNTIME_DIR: /tmp/jupyter-runtime" in jupyter

    anvil = compose_service(compose, "anvil-classroom")
    assert "ghcr.io/foundry-rs/foundry:v1.0.0" in anvil
    assert 'entrypoint: ["anvil"]' in anvil
    assert 'command: ["--host", "0.0.0.0", "--port", "8545", "--chain-id", "31337"]' in anvil
    assert 'ports: ["127.0.0.1:8545:8545"]' in anvil
    assert '"production"' not in anvil


def test_classroom_docker_target_installs_the_locked_teaching_group() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "FROM base AS classroom" in dockerfile
    assert "HOME=/tmp/invoiceops" in dockerfile
    assert "UV_CACHE_DIR=/tmp/uv-cache" in dockerfile
    assert "JUPYTER_RUNTIME_DIR=/tmp/jupyter-runtime" in dockerfile
    base_target, classroom_target = dockerfile.split("FROM base AS classroom", maxsplit=1)
    assert "apt-get install" not in base_target
    assert " git" not in base_target
    assert "USER root" in classroom_target
    assert "apt-get install --no-install-recommends -y git" in classroom_target
    assert classroom_target.index("USER invoiceops") < classroom_target.index("uv sync")
    assert "uv sync --locked --no-dev --group teaching" in dockerfile
    assert "COPY --chown=invoiceops:invoiceops notebooks ./notebooks" in dockerfile


def test_classroom_notebooks_keep_internal_tracking_and_present_the_host_ui_url() -> None:
    compose = (ROOT / "compose.yml").read_text()
    jupyter = compose_service(compose, "jupyter-classroom")
    assert "MLFLOW_TRACKING_URI: http://mlflow-lab:5000" in jupyter
    assert "INVOICEOPS_MLFLOW_UI_URL: http://127.0.0.1:5000" in jupyter
    assert "INVOICEOPS_MODEL_API_URL: http://model-api:8001" in jupyter

    for notebook_name in (
        "03_mlflow_and_model_selection.ipynb",
        "04_registry_gate_and_promotion.ipynb",
        "05_serving_policy_and_audit.ipynb",
    ):
        notebook = json.loads((ROOT / "notebooks" / notebook_name).read_text())
        source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"{notebook_name}:{cell['id']}", "exec")
        assert 'os.environ.get("INVOICEOPS_MLFLOW_UI_URL")' in source
        assert "Abre la UI de MLflow: {MLFLOW_UI_URL}" in source


def test_notebook_05_uses_the_configured_compose_model_api_without_starting_uvicorn() -> None:
    notebook = json.loads((ROOT / "notebooks" / "05_serving_policy_and_audit.ipynb").read_text())
    source = "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["id"] == "start-api"
    )

    assert 'os.environ.get("INVOICEOPS_MODEL_API_URL", "http://model-api:8001")' in source
    assert "BASE_URL = MODEL_API_BASE_URL" in source
    assert 'httpx.get(f"{BASE_URL}/health", timeout=5)' in source
    for removed_complexity in (
        "subprocess.Popen",
        '"uvicorn"',
        "cleanup_api",
        "unused_local_port",
        "tempfile.NamedTemporaryFile",
    ):
        assert removed_complexity not in source


def test_notebook_05_audits_compose_runtime_metadata_and_simulates_fallback() -> None:
    notebook = json.loads((ROOT / "notebooks" / "05_serving_policy_and_audit.ipynb").read_text())
    sources = {cell["id"]: "".join(cell["source"]) for cell in notebook["cells"]}

    audit = sources["persist-two-champions"]
    fallback = sources["safe-fallback"]

    assert "La metadata de /health y /predict no coincide" in audit
    assert 'state["evaluations"]["compose-runtime"]' in audit
    assert "promote_model" not in audit
    assert "cleanup_api" not in fallback
    assert "fallback_recommendation()" in fallback


def test_image_reference_validator_accepts_normalized_digest_reference() -> None:
    image = "ghcr.io/acme/invoiceops@sha256:" + "a" * 64

    completed = subprocess.run(
        [str(ROOT / "scripts" / "validate-image-reference.sh"), f"{image}\r"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{image}\n"


def test_image_reference_validator_rejects_shell_metacharacters() -> None:
    image = "ghcr.io/acme/invoiceops@sha256:" + "a" * 64 + "; touch /tmp/injected"

    completed = subprocess.run(
        [str(ROOT / "scripts" / "validate-image-reference.sh"), image],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "pinned by sha256 digest" in completed.stderr


def test_image_reference_validator_rejects_mutable_reference() -> None:
    completed = subprocess.run(
        [str(ROOT / "scripts" / "validate-image-reference.sh"), "ghcr.io/acme/invoiceops:latest"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "pinned by sha256 digest" in completed.stderr


def test_image_tag_resolver_resolves_only_the_requested_repository_digest(tmp_path: Path) -> None:
    image_tag = "us-central1-docker.pkg.dev/acme-project/invoiceops/invoiceops:latest"
    image = "us-central1-docker.pkg.dev/acme-project/invoiceops/invoiceops@sha256:" + "a" * 64
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$1" in\n'
        "  login) read -r token; test \"$token\" = metadata-token ;;\n"
        "  pull|logout) ;;\n"
        "  image) printf '%s\\n%s\\n' 'us-central1-docker.pkg.dev/acme-project/other/invoiceops@sha256:" + "b" * 64 + "' '"
        + image
        + "' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nprintf '%s\\n' '{\"access_token\": \"metadata-token\"}'\n")
    curl.chmod(0o755)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "resolve-image-tag.sh"), image_tag],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DOCKER_LOG": str(log), "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"{image}\n"
    calls = log.read_text()
    assert f"pull {image_tag}" in calls
    assert "logout https://us-central1-docker.pkg.dev" in calls


def test_image_tag_resolver_accepts_latest_only_at_its_boundary(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/usr/bin/env bash\nexit 1\n")
    docker.chmod(0o755)

    completed = subprocess.run(
        [
            str(ROOT / "scripts" / "resolve-image-tag.sh"),
            "us-central1-docker.pkg.dev/acme-project/invoiceops/invoiceops:stable",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )

    assert completed.returncode == 2
    assert "invoiceops:latest" in completed.stderr


def test_production_preflight_remains_digest_only(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        "exit 0\n"
    )
    docker.chmod(0o755)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "lab-preflight.sh"), "production"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "INVOICEOPS_IMAGE": "us-central1-docker.pkg.dev/acme-project/invoiceops/invoiceops:latest",
            "INVOICEOPS_DB_PATH": "/app/var/invoiceops.db",
            "INVOICEOPS_DATA_VOLUME": "/srv/invoiceops/var",
            "INVOICEOPS_SESSION_SECRET": "test-session-secret",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 2
    assert "pinned by sha256 digest" in completed.stderr
    assert "config -q" not in log.read_text()


def test_operator_runbooks_resolve_and_reuse_the_persisted_digest() -> None:
    runbooks = ROOT.parent.parent / "clases" / "03_02 de Septiembre" / "Clase4_Runbooks_Practicos"
    runbook_04 = (runbooks / "04_DEPLOY_INVOICEOPS_MODEL_API_Y_MLFLOW_EN_VM.md").read_text()
    runbook_05 = (runbooks / "05_ENV_SECRETS_LOGS_Y_TRAZABILIDAD.md").read_text()

    assert './scripts/resolve-image-tag.sh "${AR_PATH}/invoiceops:latest"' in runbook_04
    assert "/etc/invoiceops/image-ref" in runbook_04
    assert "read -r -p 'Pegue el IMAGE_REF" not in runbook_04
    assert 'INVOICEOPS_IMAGE="$(</etc/invoiceops/image-ref)"' in runbook_05
    assert "read -r -p" not in runbook_05
    assert "Pegue el IMAGE_REF" not in runbook_05


def test_rollback_uses_normalized_digest_reference_before_compose(tmp_path: Path) -> None:
    image = "ghcr.io/acme/invoiceops@sha256:" + "a" * 64
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s|%s\\n\' "$*" "$INVOICEOPS_IMAGE" >> "$DOCKER_LOG"\n'
        "exit 0\n"
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "APPLY": "1",
        "INVOICEOPS_IMAGE": f"{image}\r",
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    completed = subprocess.run(
        [str(ROOT / "scripts" / "rollback-lab.sh"), "manual"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"compose --profile manual up --detach --remove-orphans|{image}" in log.read_text()


def test_rollback_rejects_mutable_or_injected_image_before_compose(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\nexit 0\n')
    docker.chmod(0o755)

    for image in (
        "ghcr.io/acme/invoiceops:latest",
        "ghcr.io/acme/invoiceops@sha256:" + "a" * 64 + "; touch /tmp/injected",
    ):
        completed = subprocess.run(
            [str(ROOT / "scripts" / "rollback-lab.sh"), "manual"],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "APPLY": "1",
                "INVOICEOPS_IMAGE": image,
                "DOCKER_LOG": str(log),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
            },
        )

        assert completed.returncode == 2
        assert "pinned by sha256 digest" in completed.stderr

    assert not log.exists()


def test_deploy_workflow_transports_validated_image_as_data() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-services.yml").read_text()
    remote_command = workflow.split("| ssh", maxsplit=1)[1]

    assert "./scripts/validate-image-reference.sh" in workflow
    assert "printf '%s\\n' \"$VALIDATED_IMAGE\" | ssh" in workflow
    assert "IFS= read -r image" in workflow
    assert 'INVOICEOPS_IMAGE="$image" APPLY=1' in workflow
    assert "INVOICEOPS_IMAGE='$INVOICEOPS_IMAGE'" not in workflow
    assert "inputs.image" not in remote_command
    assert "$VALIDATED_IMAGE" not in remote_command


def test_deploy_waits_for_health_and_runs_smokes_without_rollback(tmp_path: Path) -> None:
    contents = (ROOT / "scripts" / "deploy-lab.sh").read_text()
    assert "wait_for_healthy_service" in contents
    assert "DEPLOY_HEALTH_ATTEMPTS" in contents

    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        "  'compose version') exit 0 ;;\n"
        "  'compose --profile manual config -q') exit 0 ;;\n"
        "  'compose --profile manual up --detach --remove-orphans') exit 0 ;;\n"
        "  'compose --profile manual exec -T portal-lab python -c '*) exit 0 ;;\n"
        "  'compose --profile manual ps --quiet portal-lab') printf 'portal-id\\n' ;;\n"
        "  'inspect '*) printf 'healthy\\n' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "APPLY": "1",
        "DEPLOY_HEALTH_ATTEMPTS": "1",
        "DEPLOY_HEALTH_INTERVAL_SECONDS": "0",
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    completed = subprocess.run(
        [str(ROOT / "scripts" / "deploy-lab.sh"), "manual"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    calls = log.read_text()
    assert "compose --profile manual ps --quiet portal-lab" in calls
    assert "compose --profile manual exec -T portal-lab python -c" in calls
    assert " down" not in calls


def test_full_lab_deploy_rejects_a_missing_champion_before_compose_up(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        "  'compose version'|'compose --profile full-lab config -q') exit 0 ;;\n"
        "  'compose --profile full-lab-bootstrap run --rm --no-deps model-bootstrap python -m invoiceops.ml.bootstrap --verify-champion') exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "deploy-lab.sh"), "full-lab"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "APPLY": "1",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 1
    assert "requires a ready invoice-review@champion" in completed.stderr
    calls = log.read_text()
    assert "compose --profile full-lab-bootstrap run --rm --no-deps model-bootstrap" in calls
    assert "compose --profile full-lab up" not in calls


def test_full_lab_deploy_reaches_compose_up_after_a_valid_champion(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        "  'compose version'|'compose --profile full-lab config -q') exit 0 ;;\n"
        "  'compose --profile full-lab-bootstrap run --rm --no-deps model-bootstrap python -m invoiceops.ml.bootstrap --verify-champion') exit 0 ;;\n"
        "  'compose --profile full-lab up --detach --remove-orphans') exit 0 ;;\n"
        "  'compose --profile full-lab ps --quiet '*) printf 'service-id\\n' ;;\n"
        "  'inspect '*) printf 'healthy\\n' ;;\n"
        "  'compose --profile full-lab exec -T '*) exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "deploy-lab.sh"), "full-lab"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "APPLY": "1",
            "DEPLOY_HEALTH_ATTEMPTS": "1",
            "DEPLOY_HEALTH_INTERVAL_SECONDS": "0",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 0, completed.stderr
    calls = log.read_text()
    assert "compose --profile full-lab up --detach --remove-orphans" in calls
    assert "compose --profile full-lab exec -T model-api python -c" in calls


def test_production_deploy_rejects_a_missing_champion_before_every_compose_up(
    tmp_path: Path,
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%q \' "$@" >> "$DOCKER_LOG"\n'
        "printf '\\n' >> \"$DOCKER_LOG\"\n"
        'case "$*" in\n'
        "  'compose version'|'compose --profile production config -q') exit 0 ;;\n"
        "  'compose --profile production run --rm --no-deps model-api-production python -m invoiceops.ml.bootstrap --verify-champion') exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "deploy-lab.sh"), "production"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "APPLY": "1",
            "INVOICEOPS_IMAGE": "ghcr.io/acme/invoiceops@sha256:" + "a" * 64,
            "INVOICEOPS_DB_PATH": "/srv/invoiceops/invoiceops.db",
            "INVOICEOPS_DATA_VOLUME": "/srv/invoiceops",
            "INVOICEOPS_SESSION_SECRET": "test-session-secret",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 1
    assert "requires a ready invoice-review@champion" in completed.stderr
    calls = [shlex.split(call) for call in log.read_text().splitlines()]
    assert [
        "compose",
        "--profile",
        "production",
        "run",
        "--rm",
        "--no-deps",
        "model-api-production",
        "python",
        "-m",
        "invoiceops.ml.bootstrap",
        "--verify-champion",
    ] in calls
    assert not any(command[:2] == ["compose", "up"] or "up" in command[1:] for command in calls)


def test_production_deploy_starts_only_serving_services_after_a_valid_champion(
    tmp_path: Path,
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%q \' "$@" >> "$DOCKER_LOG"\n'
        "printf '\\n' >> \"$DOCKER_LOG\"\n"
        'case "$*" in\n'
        "  'compose version'|'compose --profile production config -q') exit 0 ;;\n"
        "  'compose --profile production run --rm --no-deps model-api-production python -m invoiceops.ml.bootstrap --verify-champion') exit 0 ;;\n"
        "  'compose --profile production up --detach portal-production model-api-production proxy-production') exit 0 ;;\n"
        "  'compose --profile production ps --quiet '*) printf 'service-id\\n' ;;\n"
        "  'inspect '*) printf 'healthy\\n' ;;\n"
        "  'compose --profile production exec -T '*) exit 0 ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "deploy-lab.sh"), "production"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "APPLY": "1",
            "DEPLOY_HEALTH_ATTEMPTS": "1",
            "DEPLOY_HEALTH_INTERVAL_SECONDS": "0",
            "INVOICEOPS_IMAGE": "ghcr.io/acme/invoiceops@sha256:" + "a" * 64,
            "INVOICEOPS_DB_PATH": "/srv/invoiceops/invoiceops.db",
            "INVOICEOPS_DATA_VOLUME": "/srv/invoiceops",
            "INVOICEOPS_SESSION_SECRET": "test-session-secret",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 0, completed.stderr
    calls = [shlex.split(call) for call in log.read_text().splitlines()]
    compose_up_calls = [command for command in calls if "up" in command]
    assert compose_up_calls == [
        [
            "compose",
            "--profile",
            "production",
            "up",
            "--detach",
            "portal-production",
            "model-api-production",
            "proxy-production",
        ]
    ]
    assert not any("--remove-orphans" in command for command in compose_up_calls)
    assert not any(
        service in command
        for command in calls
        for service in ("model-bootstrap-production", "model-release-production")
    )


def test_deploy_collects_diagnostics_without_rollback_on_unhealthy_service(
    tmp_path: Path,
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        "  'compose version') exit 0 ;;\n"
        "  'compose --profile manual config -q') exit 0 ;;\n"
        "  'compose --profile manual up --detach --remove-orphans') exit 0 ;;\n"
        "  'compose --profile manual ps --quiet portal-lab') printf 'portal-id\\n' ;;\n"
        "  'inspect '*) printf 'unhealthy\\n' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "APPLY": "1",
        "DEPLOY_HEALTH_ATTEMPTS": "1",
        "DEPLOY_HEALTH_INTERVAL_SECONDS": "0",
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    completed = subprocess.run(
        [str(ROOT / "scripts" / "deploy-lab.sh"), "manual"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 1
    calls = log.read_text()
    assert "compose --profile manual ps" in calls
    assert "compose --profile manual logs --no-color --tail=100" in calls
    assert " down" not in calls
