import os
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


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
