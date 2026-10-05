"""Fresh, owned WebArena containers for each sequential trial.

Only IDs created by this invocation may be removed. No host directories or
existing volumes are mounted. Images must already be loaded; no implicit pull.
"""

import asyncio
import json
import os
import re
import socket
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError
from .webarena import SITES

LABEL = "org.agent-jit-compilation.run"
DEFAULT_IMAGES = {
    "gitlab": "gitlab-populated-final-port8023",
    "reddit": "postmill-populated-exposed-withimg",
}


class Snapshot:
    def __init__(self, configuration, sites, run_id):
        if not set(sites) <= SITES or not sites:
            raise ValueError("Only the GitLab and Reddit paper snapshots are supported")
        self.configuration = configuration
        self.sites = sorted(set(sites))
        self.run_id = run_id
        self.containers = []
        self.endpoints = {}
        self.evidence = {
            "method": "fresh_docker_snapshot_and_browser_profile",
            "server_reset": True,
            "containers": self.containers,
        }

    def docker(self, *args, timeout=60):
        p = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout
        )
        if p.returncode:
            raise RuntimeError(f"Docker {args[0]} failed: {p.stderr.strip()}")
        return p.stdout.strip()

    def start(self):
        deadline = time.monotonic() + self.configuration.get("startup_timeout_s", 600)
        for site in self.sites:
            spec = self.configuration.get("sites", {}).get(site, {})
            port = spec.get("port", 8023 if site == "gitlab" else 9999)
            if not isinstance(port, int) or not 1024 <= port <= 65535:
                raise ValueError("WebArena port must be an integer in 1024..65535")
            image = spec.get("image", DEFAULT_IMAGES[site])
            metadata = json.loads(self.docker("image", "inspect", image))[0]
            image_id = metadata["Id"]
            url = f"http://127.0.0.1:{port}"
            self.endpoints[site] = url
            internal = port if site == "gitlab" else 80
            args = [
                "create",
                "--pull=never",
                "--label",
                f"{LABEL}={self.run_id}",
                "--name",
                f"agent-jit-{site}-{self.run_id}",
                "-p",
                f"127.0.0.1:{port}:{internal}",
                "--shm-size",
                spec.get("shm_size", "256m"),
                image_id,
            ]
            if site == "gitlab":
                args += ["/opt/gitlab/embedded/bin/runsvdir-start"]
            cid = self.docker(*args)
            self.containers.append(
                {
                    "id": cid,
                    "site": site,
                    "image_id": image_id,
                    "image_reference": image,
                    "shm_size": spec.get("shm_size", "256m"),
                    "puma_workers": spec.get("puma_workers", 4)
                    if site == "gitlab"
                    else None,
                    "endpoint": url,
                    "image_architecture": metadata.get("Architecture"),
                    "repo_digests": metadata.get("RepoDigests", []),
                }
            )
            self.docker("start", cid)
            if site == "gitlab":
                self.docker(
                    "exec",
                    cid,
                    "sed",
                    "-i",
                    f"s|^external_url.*|external_url '{url}'|",
                    "/etc/gitlab/gitlab.rb",
                )
                workers = spec.get("puma_workers", 4)
                if type(workers) is not int or not 1 <= workers <= 64:
                    raise ValueError("puma_workers must be an integer in 1..64")
                self.docker(
                    "exec",
                    cid,
                    "sh",
                    "-c",
                    'printf \'\npuma["worker_processes"] = '
                    + str(workers)
                    + "\n' >> /etc/gitlab/gitlab.rb",
                )
                self.docker(
                    "exec",
                    cid,
                    "gitlab-ctl",
                    "reconfigure",
                    timeout=max(1, deadline - time.monotonic()),
                )
                # Wait for the old Puma to stop before considering HTTP readiness.
                self.docker(
                    "exec",
                    cid,
                    "gitlab-ctl",
                    "restart",
                    "puma",
                    timeout=max(1, deadline - time.monotonic()),
                )
            wait_ready(site, url, deadline)
        return self.evidence

    def close(self):
        errors = []
        for entry in reversed(self.containers):
            try:
                owned = self.docker(
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "' + LABEL + '" }}',
                    entry["id"],
                )
                if owned != self.run_id:
                    raise RuntimeError(
                        "Container ownership label changed; refusing removal"
                    )
                self.docker("rm", "-f", "-v", entry["id"])
                entry["removed"] = True
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("WebArena cleanup failed: " + "; ".join(errors))


def wait_ready(site, url, deadline):
    """Require sustained readiness of the actual login page, across restarts."""
    probe = url + "/users/sign_in" if site == "gitlab" else url
    streak = 0
    while time.monotonic() < deadline:
        try:
            with urlopen(
                probe, timeout=min(5, max(0.1, deadline - time.monotonic()))
            ) as response:
                body = response.read().decode("utf-8", "replace")
                good = response.status < 400 and (
                    site != "gitlab" or "username-field" in body
                )
                streak = streak + 1 if good else 0
        except (URLError, TimeoutError, ConnectionError, OSError):
            streak = 0
        if streak >= 3:
            return
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise TimeoutError(
        f"WebArena {site} login page was not stably ready before the startup deadline"
    )


def preflight(configuration, sites):
    """Read-only checks performed once before any trial or model call."""
    snapshot = Snapshot(configuration, sites, "preflight")
    info = json.loads(snapshot.docker("info", "--format", "{{json .}}"))
    if info.get("OSType") != "linux":
        raise ValueError("WebArena requires a local Linux Docker engine")
    host = os.environ.get("DOCKER_HOST", "")
    if not host:
        context = json.loads(snapshot.docker("context", "inspect"))[0]
        host = context.get("Endpoints", {}).get("docker", {}).get("Host", "")
    if host and not host.startswith("unix://"):
        raise ValueError(
            "WebArena requires local Docker (a Unix socket); remote contexts are unsupported"
        )
    ports = set()
    for site in sites:
        spec = configuration.get("sites", {}).get(site, {})
        port = spec.get("port", 8023 if site == "gitlab" else 9999)
        if type(port) is not int or not 1024 <= port <= 65535 or port in ports:
            raise ValueError(
                "WebArena sites need distinct integer ports in 1024..65535"
            )
        ports.add(port)
        shm = spec.get("shm_size", "256m")
        if not isinstance(shm, str) or not re.fullmatch(r"[1-9][0-9]*[kmgKMG]?", shm):
            raise ValueError("shm_size must be a Docker size such as 256m or 1g")
        workers = spec.get("puma_workers", 4)
        if type(workers) is not int or not 1 <= workers <= 64:
            raise ValueError("puma_workers must be an integer in 1..64")
        image = spec.get("image", DEFAULT_IMAGES[site])
        snapshot.docker("image", "inspect", image)
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise ValueError(
                    f"WebArena port {port} is occupied; choose another port"
                ) from exc
    nltk = configuration.get("nltk_data")
    if nltk and not (Path(nltk) / "tokenizers/punkt_tab/english").is_dir():
        raise ValueError(
            "WebArena punkt_tab data is missing; follow docs/WEBARENA.md before running"
        )


async def login(
    page, sites, endpoints, *, root, checkout, auth_folder, timeout=90, log_path=None
):
    """Run upstream auto-login after reset and import its fresh browser state.

    Authentication belongs to setup, not to the agent's measured task execution.
    The subprocess has its own deadline/process group, including browser children.
    """
    from .process import run_isolated
    from .webarena import verify

    if not sites or not set(sites) <= SITES:
        raise ValueError("Only GitLab and Reddit authentication is supported")
    sites = sorted(set(sites))
    checkout = Path(checkout).resolve()
    verify(Path(root), checkout)
    auth_folder = Path(auth_folder).resolve()
    auth_folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    env = dict(os.environ, PYTHONPATH=str(checkout))
    for site in (
        "gitlab",
        "reddit",
        "shopping",
        "shopping_admin",
        "wikipedia",
        "map",
        "homepage",
    ):
        env[site.upper()] = endpoints.get(site, "http://127.0.0.1:1")
    command = [
        sys.executable,
        str(checkout / "browser_env/auto_login.py"),
        "--site_list",
        *sites,
        "--auth_folder",
        str(auth_folder),
    ]
    log_path = Path(log_path) if log_path else auth_folder / "auto-login.log"
    with log_path.open("w") as log:
        code = await asyncio.to_thread(
            run_isolated,
            command,
            cwd=checkout,
            env=env,
            log=log,
            timeout=timeout,
        )
    if code:
        raise RuntimeError(f"WebArena auto-login exited {code}; see {log_path}")
    state_file = auth_folder / (".".join(sites) + "_state.json")
    state_file.chmod(0o600)
    state = json.loads(state_file.read_text())
    await page.context.add_cookies(state["cookies"])
    # Persistent contexts do not accept a storage_state constructor argument.
    # Restore localStorage once, without an init script that would reset later work.
    for origin in state.get("origins", []):
        await page.goto(origin["origin"])
        await page.evaluate(
            "items => { for (const {name, value} of items) localStorage.setItem(name, value); }",
            origin["localStorage"],
        )
    # --site_list renews cookies but does not perform upstream's expiry checks.
    # Verify the authenticated destination using the same URL/keyword criteria.
    for site in sites:
        if site == "gitlab":
            expected = endpoints[site] + "/-/profile"
            await page.goto(expected)
            if page.url.rstrip("/") != expected.rstrip("/"):
                raise RuntimeError("GitLab benchmark login failed")
        elif site == "reddit":
            await page.goto(endpoints[site] + "/user/MarvelsGrantMan136/account")
            if "/login" in page.url or "Delete" not in await page.content():
                raise RuntimeError("Reddit benchmark login failed")
    return {
        "method": "upstream_auto_login",
        "sites": sites,
        "state_file": str(state_file),
    }
