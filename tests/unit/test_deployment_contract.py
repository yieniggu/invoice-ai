import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

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


def test_production_portal_forwards_the_complete_secure_auth_contract() -> None:
    compose = (ROOT / "compose.yml").read_text()
    portal_production = compose_service(compose, "portal-production")

    for name in (
        "INVOICEOPS_DEMO_USERNAME",
        "INVOICEOPS_DEMO_PASSWORD",
        "INVOICEOPS_SESSION_SECRET",
        "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS",
    ):
        assert f"{name}: ${{{name}:-}}" in portal_production

    assert "INVOICEOPS_SESSION_COOKIE_SECURE: ${INVOICEOPS_SESSION_COOKIE_SECURE:-true}" in (
        portal_production
    )


def test_production_portal_uses_model_api_private_mlflow_tracking_uri() -> None:
    compose = (ROOT / "compose.yml").read_text()
    portal_production = compose_service(compose, "portal-production")
    model_api_production = compose_service(compose, "model-api-production")

    tracking_uri = "http://mlflow-production:5000"
    assert f"MLFLOW_TRACKING_URI: {tracking_uri}" in portal_production
    assert f"MLFLOW_TRACKING_URI: {tracking_uri}" in model_api_production


def test_production_image_runs_portal_as_the_documented_data_volume_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "addgroup --system --gid 101 invoiceops" in dockerfile
    assert "adduser --system --uid 100 --ingroup invoiceops invoiceops" in dockerfile
    assert "USER invoiceops" in dockerfile


@pytest.mark.parametrize(
    ("metadata", "required_bits", "expected"),
    (
        ("100:101:770", "7", "0"),
        ("100:101:750", "7", "0"),
        ("0:0:755", "1", "0"),
        ("0:0:750", "1", "1"),
    ),
)
def test_production_data_mount_access_uses_the_image_service_user(
    metadata: str, required_bits: str, expected: str
) -> None:
    script = ROOT / "scripts" / "lab-preflight.sh"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; set +e; stat() { printf "%s\\n" "$STAT_METADATA"; }; '
                'service_user_access /srv/invoiceops/var "$REQUIRED_BITS"; printf "%s\\n" "$?"'
            ),
            "bash",
            str(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "STAT_METADATA": metadata,
            "REQUIRED_BITS": required_bits,
        },
    )

    assert completed.returncode == 0
    assert completed.stdout == f"{expected}\n"


@pytest.mark.parametrize(
    ("final_metadata", "ancestor_metadata", "expected"),
    (
        ("100:101:770", "0:0:755", "0"),
        ("100:101:750", "0:0:755", "1"),
        ("100:101:770", "0:0:700", "1"),
    ),
)
def test_production_data_mount_requires_exact_directory_and_service_user_traversal(
    final_metadata: str, ancestor_metadata: str, expected: str
) -> None:
    script = ROOT / "scripts" / "lab-preflight.sh"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; set +e; directory_exists() { return 0; }; '
                'stat() { if [ "$3" = /srv/invoiceops/var ]; then printf "%s\\n" "$FINAL_METADATA"; '
                'else printf "%s\\n" "$ANCESTOR_METADATA"; fi; }; '
                'INVOICEOPS_DB_PATH=/app/var/invoiceops.db; INVOICEOPS_DATA_VOLUME=/srv/invoiceops/var; '
                '( validate_production_data_mount >/dev/null 2>&1 ); printf "%s\\n" "$?"'
            ),
            "bash",
            str(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "FINAL_METADATA": final_metadata,
            "ANCESTOR_METADATA": ancestor_metadata,
        },
    )

    assert completed.returncode == 0
    assert completed.stdout == f"{expected}\n"


def test_production_portal_mount_contract_is_canonical_and_checked_before_deploy() -> None:
    compose = (ROOT / "compose.yml").read_text()
    preflight = (ROOT / "scripts" / "lab-preflight.sh").read_text()
    portal_production = compose_service(compose, "portal-production")

    assert "INVOICEOPS_DB_PATH: ${INVOICEOPS_DB_PATH:-/app/var/invoiceops.db}" in portal_production
    assert "${INVOICEOPS_DATA_VOLUME:-/srv/invoiceops-unconfigured}:/app/var" in portal_production
    assert "readonly PORTAL_DB_PATH='/app/var/invoiceops.db'" in preflight
    assert "readonly PORTAL_DATA_VOLUME='/srv/invoiceops/var'" in preflight
    assert "readonly PORTAL_UID='100'" in preflight
    assert "readonly PORTAL_GID='101'" in preflight
    assert '"$PORTAL_UID:$PORTAL_GID:770"' in preflight
    assert 'for path in / /srv /srv/invoiceops; do' in preflight


def test_production_minio_initializer_passes_one_posix_script_to_sh() -> None:
    compose = (ROOT / "compose.yml").read_text()
    initializer = compose_service(compose, "minio-init-production")

    assert 'entrypoint: ["/bin/sh", "-c"]' in initializer
    assert "command:\n      - |-" in initializer
    assert '"$${MLFLOW_OBJECT_ACCESS_KEY}"' in initializer
    assert '"$${MLFLOW_OBJECT_SECRET_KEY}"' in initializer
    assert "until mc alias set production" in initializer
    assert "mc mb --ignore-existing production/mlflow-artifacts" in initializer


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


def test_production_mlflow_percent_encodes_its_postgres_password_at_runtime() -> None:
    compose = (ROOT / "compose.yml").read_text()
    mlflow_production = compose_service(compose, "mlflow-production")
    password = "pa:ss@word/with?reserved#chars%&+="
    encoder = (
        'import os; from urllib.parse import quote; '
        'print(quote(os.environ["MLFLOW_POSTGRES_PASSWORD"], safe=""))'
    )

    assert 'entrypoint: ["/bin/sh", "-c"]' in mlflow_production
    assert f"python -c '{encoder}'" in mlflow_production
    assert "MLFLOW_POSTGRES_PASSWORD: ${MLFLOW_POSTGRES_PASSWORD:-}" in mlflow_production
    assert 'postgresql://mlflow:$${password}@postgres-production:5432/mlflow' in mlflow_production
    assert "postgresql://mlflow:$${MLFLOW_POSTGRES_PASSWORD}" not in mlflow_production

    completed = subprocess.run(
        ["python", "-c", encoder],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MLFLOW_POSTGRES_PASSWORD": password},
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "pa%3Ass%40word%2Fwith%3Freserved%23chars%25%26%2B%3D\n"


def test_classroom_compose_binds_student_services_to_localhost() -> None:
    compose = (ROOT / "compose.yml").read_text()

    for port in ("5000", "8001", "8080"):
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
    assert "ports:" not in anvil
    assert '"production"' in anvil


def test_local_anchor_bootstrap_exclusively_initializes_the_shared_manifest_volume() -> None:
    compose = (ROOT / "compose.yml").read_text()

    portal = compose_service(compose, "portal-lab")
    bootstrap = compose_service(compose, "local-anchor-bootstrap")

    assert "local-anchor-deployments:/app/anchor-deployments:ro" in portal
    assert "image: ${INVOICEOPS_IMAGE:-invoiceops:unconfigured}" in bootstrap
    assert "build:" not in bootstrap
    assert 'user: "0:0"' in bootstrap
    assert "local-anchor-deployments:/app/anchor-deployments" in bootstrap


def test_production_compose_starts_private_anvil_before_the_portal() -> None:
    compose = (ROOT / "compose.yml").read_text()
    anvil = compose_service(compose, "anvil-classroom")
    bootstrap = compose_service(compose, "local-anchor-bootstrap")
    portal = compose_service(compose, "portal-production")

    assert 'profiles: ["local", "classroom", "production"]' in anvil
    assert "ports:" not in anvil
    assert 'profiles: ["local", "classroom", "production"]' in bootstrap
    assert "anvil-classroom:" in bootstrap
    assert "condition: service_healthy" in bootstrap
    assert "local-anchor-bootstrap:" in portal
    assert "condition: service_completed_successfully" in portal
    assert "local-anchor-deployments:/app/anchor-deployments:ro" in portal
    assert "INVOICEOPS_LOCAL_ANCHOR_MANIFEST: /app/anchor-deployments/local.json" in portal
    assert "INVOICEOPS_LOCAL_ANCHOR_RPC_URL: http://anvil-classroom:8545" in portal


def test_production_resolved_compose_keeps_rpc_internal_only() -> None:
    completed = subprocess.run(
        ["docker", "compose", "--profile", "production", "config", "--format", "json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "INVOICEOPS_IMAGE": "ghcr.io/acme/invoiceops@sha256:" + "a" * 64},
    )
    if completed.returncode:
        pytest.skip(f"Docker Compose resolved config unavailable: {completed.stderr.strip()}")

    resolved = json.loads(completed.stdout)
    anvil = resolved["services"]["anvil-classroom"]
    bootstrap = resolved["services"]["local-anchor-bootstrap"]
    portal = resolved["services"]["portal-production"]
    assert "ports" not in anvil
    assert bootstrap["image"] == "ghcr.io/acme/invoiceops@sha256:" + "a" * 64
    assert "build" not in bootstrap
    assert bootstrap["user"] == "0:0"
    assert bootstrap["volumes"][0]["type"] == "volume"
    assert bootstrap["volumes"][0]["source"] == "local-anchor-deployments"
    assert bootstrap["volumes"][0]["target"] == "/app/anchor-deployments"
    assert portal["environment"]["INVOICEOPS_LOCAL_ANCHOR_MANIFEST"] == (
        "/app/anchor-deployments/local.json"
    )
    assert portal["environment"]["INVOICEOPS_LOCAL_ANCHOR_RPC_URL"] == "http://anvil-classroom:8545"
    assert portal["depends_on"]["local-anchor-bootstrap"]["condition"] == (
        "service_completed_successfully"
    )


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
            "INVOICEOPS_DEMO_USERNAME": "secure-analyst",
            "INVOICEOPS_DEMO_PASSWORD": "secure-password",
            "INVOICEOPS_SESSION_SECRET": "test-session-secret",
            "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS": "secure-analyst",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 2
    assert "pinned by sha256 digest" in completed.stderr
    assert "config -q" not in log.read_text()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    (
        ("INVOICEOPS_DB_PATH", "/srv/invoiceops/invoiceops.db", "INVOICEOPS_DB_PATH"),
        ("INVOICEOPS_DATA_VOLUME", "/srv/invoiceops", "INVOICEOPS_DATA_VOLUME"),
    ),
)
def test_production_preflight_rejects_unsafe_data_mount_variables_before_compose_config(
    tmp_path: Path, name: str, value: str, message: str
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\nexit 0\n')
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "INVOICEOPS_IMAGE": "ghcr.io/acme/invoiceops@sha256:" + "a" * 64,
        "INVOICEOPS_DB_PATH": "/app/var/invoiceops.db",
        "INVOICEOPS_DATA_VOLUME": "/srv/invoiceops/var",
        "INVOICEOPS_DEMO_USERNAME": "secure-analyst",
        "INVOICEOPS_DEMO_PASSWORD": "secure-password",
        "INVOICEOPS_SESSION_SECRET": "test-session-secret",
        "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS": "secure-analyst",
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    environment[name] = value

    completed = subprocess.run(
        [str(ROOT / "scripts" / "lab-preflight.sh"), "production"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 1
    assert f"Invalid production data mount: {message}" in completed.stderr
    assert "config -q" not in log.read_text()


def test_production_preflight_rejects_an_absent_data_directory_before_compose_config(
    tmp_path: Path,
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\nexit 0\n')
    docker.chmod(0o755)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "lab-preflight.sh"), "production"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "INVOICEOPS_IMAGE": "ghcr.io/acme/invoiceops@sha256:" + "a" * 64,
            "INVOICEOPS_DB_PATH": "/app/var/invoiceops.db",
            "INVOICEOPS_DATA_VOLUME": "/srv/invoiceops/var",
            "INVOICEOPS_DEMO_USERNAME": "secure-analyst",
            "INVOICEOPS_DEMO_PASSWORD": "secure-password",
            "INVOICEOPS_SESSION_SECRET": "test-session-secret",
            "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS": "secure-analyst",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 1
    assert "must exist as a directory" in completed.stderr
    assert "config -q" not in log.read_text()


@pytest.mark.parametrize(
    "missing_name",
    (
        "INVOICEOPS_DEMO_USERNAME",
        "INVOICEOPS_DEMO_PASSWORD",
        "INVOICEOPS_SESSION_SECRET",
        "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS",
    ),
)
def test_production_preflight_rejects_each_missing_secure_auth_variable_before_compose_config(
    tmp_path: Path, missing_name: str
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "exit 0\n"
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "INVOICEOPS_IMAGE": "ghcr.io/acme/invoiceops@sha256:" + "a" * 64,
        "INVOICEOPS_DB_PATH": "/app/var/invoiceops.db",
        "INVOICEOPS_DATA_VOLUME": "/srv/invoiceops/var",
        "INVOICEOPS_DEMO_USERNAME": "secure-analyst",
        "INVOICEOPS_DEMO_PASSWORD": "secure-password",
        "INVOICEOPS_SESSION_SECRET": "test-session-secret",
        "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS": "secure-analyst",
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    environment.pop(missing_name)

    completed = subprocess.run(
        [str(ROOT / "scripts" / "lab-preflight.sh"), "production"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 1
    assert f"Missing required production variable: {missing_name}" in completed.stderr
    assert "config -q" not in log.read_text()


def test_operator_runbooks_resolve_and_reuse_the_persisted_digest() -> None:
    runbooks = ROOT.parent.parent / "clases" / "03_02 de Septiembre" / "Clase4_Runbooks_Practicos"
    runbook_04 = (runbooks / "04_DEPLOY_INVOICEOPS_MODEL_API_Y_MLFLOW_EN_VM.md").read_text()
    runbook_05 = (runbooks / "05_ENV_SECRETS_LOGS_Y_TRAZABILIDAD.md").read_text()

    assert './scripts/resolve-image-tag.sh "${AR_PATH}/invoiceops:latest"' in runbook_04
    assert "/etc/invoiceops/image-ref" in runbook_04
    assert "read -r -p 'Pegue el IMAGE_REF" not in runbook_04
    assert 'INVOICEOPS_IMAGE="$(</etc/invoiceops/image-ref)"' in runbook_05
    assert "read -r -p 'Pegue el IMAGE_REF" not in runbook_05
    assert "Pegue el IMAGE_REF" not in runbook_05


def test_portal_runbooks_keep_the_proven_runtime_identity_and_scoped_reset() -> None:
    runbooks = ROOT.parent.parent / "clases" / "03_02 de Septiembre" / "Clase4_Runbooks_Practicos"
    runbook_05 = (runbooks / "05_ENV_SECRETS_LOGS_Y_TRAZABILIDAD.md").read_text()
    runbook_05b = (runbooks / "05B_DEPLOY_MANUAL_PORTAL_EN_VM.md").read_text()
    runbook_15 = (runbooks / "15_PREFLIGHT_Y_TROUBLESHOOTING.md").read_text()

    for runbook in (runbook_05, runbook_05b):
        assert "999:999" not in runbook
        assert "999" not in runbook
    assert "install -d -o 100 -g 101 -m 0770" in runbook_05
    assert (
        "docker compose --profile production rm -f anvil-classroom local-anchor-bootstrap "
        "portal-production model-api-production proxy-production"
    ) in runbook_05b
    assert "rm -f /srv/invoiceops/var/invoiceops.db /srv/invoiceops/var/invoiceops.db-wal /srv/invoiceops/var/invoiceops.db-shm" in runbook_05b
    assert "docker compose --profile production run --rm --no-deps portal-production" in runbook_15
    assert "compose_write_probe_exit" in runbook_15


def test_portal_mlflow_lineage_runbooks_verify_private_access_before_declaring_a_run_missing() -> None:
    runbooks = ROOT.parent.parent / "clases" / "03_02 de Septiembre" / "Clase4_Runbooks_Practicos"
    runbook_05b = (runbooks / "05B_DEPLOY_MANUAL_PORTAL_EN_VM.md").read_text()
    runbook_15 = (runbooks / "15_PREFLIGHT_Y_TROUBLESHOOTING.md").read_text()

    for runbook in (runbook_05b, runbook_15):
        assert "http://mlflow-production:5000" in runbook
        assert "portal_tracking_uri=private_mlflow_service" in runbook
        assert "portal_mlflow_run=resolved" in runbook
        assert "MLflow run is unavailable: <id>" in runbook
        assert "no imprime la URI efectiva, secretos ni el identificador del run" in runbook or (
            "no muestra secretos, la URI efectiva ni el ID" in runbook
        )


def test_http_classroom_session_override_is_explicit_and_scoped_to_portal() -> None:
    runbooks = ROOT.parent.parent / "clases" / "03_02 de Septiembre" / "Clase4_Runbooks_Practicos"
    runbook_05 = (runbooks / "05_ENV_SECRETS_LOGS_Y_TRAZABILIDAD.md").read_text()
    runbook_05b = (runbooks / "05B_DEPLOY_MANUAL_PORTAL_EN_VM.md").read_text()
    runbook_09 = (runbooks / "09_PREDICTION_EVIDENCE_Y_ANCHOR_REMOTO.md").read_text()

    assert runbook_05.count("INVOICEOPS_SESSION_COOKIE_SECURE") >= 8
    assert "INVOICEOPS_SESSION_COOKIE_SECURE=false" in runbook_05
    assert "INVOICEOPS_SESSION_COOKIE_SECURE=true" in runbook_05
    assert "pueden ser interceptados" in runbook_05
    assert "Prueba de inicio de sesión:" in runbook_05b
    assert "INVOICEOPS_SESSION_COOKIE_SECURE=false" in runbook_05b
    assert "Caddy HTTP" in runbook_09


def test_foundry_runbook_uses_an_ignored_local_env_template_and_preserves_vm_identity() -> None:
    template = (ROOT / "contracts" / ".env.example").read_text()
    runbooks = ROOT.parent.parent / "clases" / "03_02 de Septiembre" / "Clase4_Runbooks_Practicos"
    runbook_07 = (runbooks / "07_DEPLOY_CONTRATO_CON_REMIX.md").read_text()
    runbook_08 = (runbooks / "08_DEPLOY_Y_VERIFICACION_CON_FOUNDRY.md").read_text()
    technical_runbook = (ROOT / "docs" / "gnosis-chiado-anchor-runbook.md").read_text()

    assert template == (
        "GNOSIS_CHIADO_RPC_URL=https://rpc.chiadochain.net\n"
        "PRIVATE_KEY=\n"
        "EVIDENCE_ROOT_ANCHOR_SIGNER=\n"
    )
    assert ".env" in (ROOT / ".gitignore").read_text().splitlines()
    for runbook in (runbook_08, technical_runbook):
        assert "cp .env.example .env" in runbook
        assert "chmod 600 .env" in runbook
        assert "set -a; . ./.env; set +a" in runbook
        assert 'cast wallet address --private-key "$PRIVATE_KEY"' in runbook
        assert '--private-key "$PRIVATE_KEY" --broadcast' in runbook
        assert "no corresponde a un contrato desplegado on-chain" in runbook
        assert "run-latest.json" in runbook
        assert "CONTRACT_ADDRESS" in runbook
        assert "DEPLOY_TX_HASH" in runbook
        assert '| .hash' in runbook
        assert '| .transactionHash' not in runbook
        assert 'test -n "$CONTRACT_ADDRESS"' in runbook
        assert 'test -n "$DEPLOY_TX_HASH"' in runbook
        assert 'cast receipt "$DEPLOY_TX_HASH" status' in runbook
        assert 'cast code "$CONTRACT_ADDRESS"' in runbook
        assert 'test "$runtime_bytecode" != "0x"' in runbook
        assert "verify_source || { sleep 60; verify_source; }" in runbook
        assert "forge verify-contract --chain-id 10200" in runbook
        assert "forge verify-contract --watch" not in runbook
        assert 'test "$chain_id" = "10200"' in runbook
    assert "no ejecute `cd contracts` otra vez" in runbook_08
    assert "/etc/invoiceops/contract-manifest.json" in runbook_07
    assert "/etc/invoiceops/contract-manifest.json" in runbook_08


def test_contract_deploy_workflow_passes_the_private_key_to_foundry_without_printing_it() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-contract.yml").read_text()

    validation = 'cast wallet address --private-key "$PRIVATE_KEY"'
    broadcast = '--rpc-url "$GNOSIS_CHIADO_RPC_URL" --private-key "$PRIVATE_KEY" --broadcast'
    metadata = 'broadcast="$(ls -t contracts/broadcast/DeployEvidenceRootAnchor.s.sol/10200/run-latest.json'

    assert validation in workflow
    assert broadcast in workflow
    assert workflow.index(validation) < workflow.index(broadcast) < workflow.index(metadata)
    assert "printf '%s\\n' \"$deployer_address\"" in workflow
    assert '| .hash' in workflow
    assert '| .transactionHash' not in workflow
    assert 'test -n "$address"' in workflow
    assert 'test -n "$tx_hash"' in workflow
    assert 'cast receipt "$tx_hash" status' in workflow
    assert 'cast code "$address"' in workflow
    assert 'test "$runtime_bytecode" != "0x"' in workflow
    assert "verify_source || { sleep 60; verify_source; }" in workflow
    assert "forge verify-contract --watch" not in workflow


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


@pytest.mark.parametrize(
    ("state", "expected_returncode", "expected_stderr"),
    (
        ("exited:0", 0, ""),
        (
            "exited:1",
            1,
            "local-anchor-bootstrap did not complete successfully within 1 attempts "
            "(last state: exited:1).\n",
        ),
        (
            "running:0",
            1,
            "local-anchor-bootstrap did not complete successfully within 1 attempts "
            "(last state: running:0).\n",
        ),
    ),
)
def test_one_shot_deploy_wait_requires_a_successfully_exited_container(
    state: str, expected_returncode: int, expected_stderr: str
) -> None:
    deploy = (ROOT / "scripts" / "deploy-lab.sh").read_text()
    match = re.search(
        r"(wait_for_completed_service\(\) \{.*?^\})",
        deploy,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    wait_function = match.group(1)
    harness = f"""set -u
health_attempts=1
health_interval=0
compose() {{
  [ \"$*\" = \"ps --quiet --all local-anchor-bootstrap\" ] && printf 'bootstrap-id\\n'
}}
docker() {{
  [ \"$1\" = inspect ] && printf '%s\\n' \"$BOOTSTRAP_STATE\"
}}
{wait_function}
wait_for_completed_service local-anchor-bootstrap
"""

    completed = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BOOTSTRAP_STATE": state},
    )

    assert completed.returncode == expected_returncode
    assert completed.stderr == expected_stderr


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


def test_production_deploy_rejects_unsafe_data_mount_before_champion_check(
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
            "INVOICEOPS_DEMO_USERNAME": "secure-analyst",
            "INVOICEOPS_DEMO_PASSWORD": "secure-password",
            "INVOICEOPS_SESSION_SECRET": "test-session-secret",
            "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS": "secure-analyst",
            "DOCKER_LOG": str(log),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )

    assert completed.returncode == 1
    assert "INVOICEOPS_DB_PATH must be /app/var/invoiceops.db" in completed.stderr
    calls = [shlex.split(call) for call in log.read_text().splitlines()]
    assert not any(command[:2] == ["compose", "run"] for command in calls)
    assert not any(command[:2] == ["compose", "up"] or "up" in command[1:] for command in calls)


def test_production_deploy_limits_serving_services_after_preflight() -> None:
    deploy = (ROOT / "scripts" / "deploy-lab.sh").read_text()
    rollback = (ROOT / "scripts" / "rollback-lab.sh").read_text()

    assert (
        'production) printf \'%s\\n\' anvil-classroom portal-production '
        'model-api-production proxy-production ;;'
    ) in deploy
    assert 'production) printf \'%s\\n\' local-anchor-bootstrap ;;' in deploy
    assert "wait_for_completed_service" in deploy
    assert "local-anchor-bootstrap" in rollback
    assert (
        'if [ "$profile" = "production" ]; then\n'
        '  compose_up=(up --detach)\n'
        'else\n'
        '  compose_up=(up --detach --remove-orphans)\n'
        'fi'
    ) in deploy


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
