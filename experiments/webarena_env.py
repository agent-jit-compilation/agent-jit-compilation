"""
WebArena Environment Manager

Provides functionality to reset WebArena Docker containers and run auto-login
to ensure a fresh environment state before running tasks.

Usage:
    from experiments.webarena_env import reset_webarena_env

    # Reset containers for specific sites and run auto-login
    reset_webarena_env(["gitlab", "shopping"])
"""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Docker container configurations for WebArena sites
# Maps site name to container configuration
WEBARENA_CONTAINERS: Dict[str, Dict] = {
    "shopping": {
        "container_name": "shopping",
        "image": "shopping_final_0712",
        "port": 7770,
        "run_cmd": [
            "docker",
            "run",
            "--name",
            "shopping",
            "-p",
            "7770:80",
            "-d",
            "shopping_final_0712",
        ],
        "health_url": "http://localhost:7770",
        "startup_wait": 60,  # seconds to wait for container to be ready
    },
    "shopping_admin": {
        "container_name": "shopping_admin",
        "image": "shopping_admin_final_0719",
        "port": 7780,
        "run_cmd": [
            "docker",
            "run",
            "--name",
            "shopping_admin",
            "-p",
            "7780:80",
            "-d",
            "shopping_admin_final_0719",
        ],
        "health_url": "http://localhost:7780",
        "startup_wait": 60,
    },
    "gitlab": {
        "container_name": "gitlab",
        "image": "gitlab-populated-final-port8023",
        "port": 8023,
        "run_cmd": [
            "docker",
            "run",
            "--name",
            "gitlab",
            "-d",
            "-p",
            "8023:8023",
            "gitlab-populated-final-port8023",
            "/opt/gitlab/embedded/bin/runsvdir-start",
        ],
        "health_url": "http://localhost:8023",
        "startup_wait": 60,
    },
    "reddit": {
        "container_name": "forum",
        "image": "postmill-populated-exposed-withimg",
        "port": 9999,
        "run_cmd": [
            "docker",
            "run",
            "--name",
            "forum",
            "-p",
            "9999:80",
            "-d",
            "postmill-populated-exposed-withimg",
        ],
        "health_url": "http://localhost:9999",
        "startup_wait": 120,
    },
    # Alias for reddit (some configs use "forum")
    "forum": {
        "container_name": "forum",
        "image": "postmill-populated-exposed-withimg",
        "port": 9999,
        "run_cmd": [
            "docker",
            "run",
            "--name",
            "forum",
            "-p",
            "9999:80",
            "-d",
            "postmill-populated-exposed-withimg",
        ],
        "health_url": "http://localhost:9999",
        "startup_wait": 30,
    },
}


def stop_container(container_name: str) -> bool:
    """
    Stop a Docker container.

    Args:
        container_name: Name of the container to stop

    Returns:
        True if successful or container doesn't exist, False on error
    """
    try:
        result = subprocess.run(
            ["docker", "stop", container_name],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info(f"Stopped container: {container_name}")
            return True
        elif "No such container" in result.stderr:
            logger.debug(f"Container {container_name} does not exist, nothing to stop")
            return True
        else:
            logger.warning(
                f"Failed to stop container {container_name}: {result.stderr}"
            )
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout stopping container {container_name}")
        return False
    except Exception as e:
        logger.error(f"Error stopping container {container_name}: {e}")
        return False


def remove_container(container_name: str) -> bool:
    """
    Remove a Docker container.

    Args:
        container_name: Name of the container to remove

    Returns:
        True if successful or container doesn't exist, False on error
    """
    try:
        result = subprocess.run(
            ["docker", "rm", container_name], capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info(f"Removed container: {container_name}")
            return True
        elif "No such container" in result.stderr:
            logger.debug(
                f"Container {container_name} does not exist, nothing to remove"
            )
            return True
        else:
            logger.warning(
                f"Failed to remove container {container_name}: {result.stderr}"
            )
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout removing container {container_name}")
        return False
    except Exception as e:
        logger.error(f"Error removing container {container_name}: {e}")
        return False


def start_container(site: str) -> bool:
    """
    Start a Docker container for a WebArena site.

    Args:
        site: Site name (e.g., "gitlab", "shopping")

    Returns:
        True if successful, False on error
    """
    if site not in WEBARENA_CONTAINERS:
        logger.error(f"Unknown site: {site}")
        return False

    config = WEBARENA_CONTAINERS[site]
    run_cmd = config["run_cmd"]

    try:
        result = subprocess.run(run_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            logger.info(f"Started container for {site}: {config['container_name']}")
            return True
        else:
            logger.error(f"Failed to start container for {site}: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout starting container for {site}")
        return False
    except Exception as e:
        logger.error(f"Error starting container for {site}: {e}")
        return False


def wait_for_container(site: str, timeout: Optional[int] = None) -> bool:
    """
    Wait for a container to be ready by checking its health URL.

    Args:
        site: Site name (e.g., "gitlab", "shopping")
        timeout: Maximum time to wait in seconds (uses default from config if not specified)

    Returns:
        True if container is ready, False if timeout
    """
    if site not in WEBARENA_CONTAINERS:
        logger.error(f"Unknown site: {site}")
        return False

    config = WEBARENA_CONTAINERS[site]
    health_url = config["health_url"]
    max_wait = timeout if timeout is not None else config["startup_wait"]

    logger.info(f"Waiting for {site} to be ready at {health_url} (max {max_wait}s)...")

    start_time = time.time()
    check_interval = 5  # seconds between checks

    while time.time() - start_time < max_wait:
        try:
            response = requests.get(health_url, timeout=5)
            if response.status_code < 500:  # Accept any non-server-error response
                elapsed = time.time() - start_time
                logger.info(
                    f"{site} is ready after {elapsed:.1f}s (HTTP {response.status_code})"
                )
                return True
        except requests.exceptions.RequestException:
            pass  # Container not ready yet

        time.sleep(check_interval)

    logger.error(f"Timeout waiting for {site} to be ready after {max_wait}s")
    return False


def reset_container(site: str, wait: bool = True) -> bool:
    """
    Reset a single container: stop, remove, and restart.

    Args:
        site: Site name (e.g., "gitlab", "shopping")
        wait: Whether to wait for container to be ready

    Returns:
        True if successful, False on error
    """
    if site not in WEBARENA_CONTAINERS:
        logger.error(f"Unknown site: {site}")
        return False

    config = WEBARENA_CONTAINERS[site]
    container_name = config["container_name"]

    logger.info(f"Resetting container for {site} ({container_name})...")

    # Stop the container
    if not stop_container(container_name):
        return False

    # Remove the container
    if not remove_container(container_name):
        return False

    # Start the container
    if not start_container(site):
        return False

    # Wait for container to be ready
    if wait:
        if not wait_for_container(site):
            return False

    return True


def reset_containers(sites: List[str], wait: bool = True) -> bool:
    """
    Reset multiple containers.

    Args:
        sites: List of site names to reset
        wait: Whether to wait for containers to be ready

    Returns:
        True if all successful, False if any failed
    """
    if not sites:
        logger.warning("No sites specified for reset")
        return True

    # Deduplicate sites (e.g., "reddit" and "forum" map to same container)
    unique_containers = {}
    for site in sites:
        if site not in WEBARENA_CONTAINERS:
            logger.warning(f"Unknown site: {site}, skipping")
            continue
        container_name = WEBARENA_CONTAINERS[site]["container_name"]
        if container_name not in unique_containers:
            unique_containers[container_name] = site

    logger.info(
        f"Resetting {len(unique_containers)} containers: {list(unique_containers.values())}"
    )

    success = True
    for site in unique_containers.values():
        if not reset_container(site, wait=wait):
            success = False

    return success


# Default WebArena URLs (used if environment variables not set)
WEBARENA_DEFAULT_URLS = {
    "GITLAB": "http://localhost:8023",
    "REDDIT": "http://localhost:9999",
    "SHOPPING": "http://localhost:7770",
    "SHOPPING_ADMIN": "http://localhost:7780/admin",
    "WIKIPEDIA": "http://localhost:8888",
    "MAP": "http://localhost:3000",
    "HOMEPAGE": "http://localhost:4399",
}


def run_auto_login(
    sites: List[str], auth_folder: str = "./.auth", webarena_root: Optional[str] = None
) -> bool:
    """
    Run the auto-login script to generate fresh authentication cookies.

    Args:
        sites: List of site names to login to
        auth_folder: Path to store auth state files
        webarena_root: Path to webarena directory (defaults to webarena/ relative to this file)

    Returns:
        True if successful, False on error
    """
    if not sites:
        logger.warning("No sites specified for auto-login")
        return True

    # Determine webarena root directory
    if webarena_root is None:
        # Default: assume webarena/ is sibling to experiments/
        this_file = Path(__file__).resolve()
        webarena_root = str(this_file.parent.parent / "webarena")

    webarena_path = Path(webarena_root)
    auto_login_script = webarena_path / "browser_env" / "auto_login.py"

    if not auto_login_script.exists():
        logger.error(f"Auto-login script not found: {auto_login_script}")
        return False

    # Ensure auth folder exists
    auth_path = webarena_path / auth_folder
    auth_path.mkdir(parents=True, exist_ok=True)

    # Filter to valid sites that support login
    login_sites = [
        s for s in sites if s in ["gitlab", "shopping", "shopping_admin", "reddit"]
    ]

    if not login_sites:
        logger.info("No sites require login")
        return True

    logger.info(f"Running auto-login for sites: {login_sites}")

    try:
        # Set up environment with PYTHONPATH and WebArena URLs
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{webarena_path}:{pythonpath}" if pythonpath else str(webarena_path)
        )

        # Set WebArena URL environment variables (use existing env vars or defaults)
        for var_name, default_url in WEBARENA_DEFAULT_URLS.items():
            if var_name not in env:
                env[var_name] = default_url
                logger.debug(f"Set {var_name}={default_url}")

        result = subprocess.run(
            [
                "python",
                str(auto_login_script),
                "--auth_folder",
                str(auth_path),
                "--site_list",
                *login_sites,
            ],
            cwd=str(webarena_path),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

        if result.returncode == 0:
            logger.info("Auto-login completed successfully")
            return True
        else:
            logger.error(f"Auto-login failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Auto-login timed out")
        return False
    except Exception as e:
        logger.error(f"Error running auto-login: {e}")
        return False


def reset_webarena_env(
    sites: List[str],
    run_login: bool = True,
    auth_folder: str = "./.auth",
    webarena_root: Optional[str] = None,
) -> bool:
    """
    Fully reset WebArena environment: restart containers and run auto-login.

    This is the main entry point for resetting the WebArena environment before
    running tasks.

    Args:
        sites: List of site names to reset (e.g., ["gitlab", "shopping"])
        run_login: Whether to run auto-login after container reset
        auth_folder: Path to store auth state files
        webarena_root: Path to webarena directory

    Returns:
        True if successful, False on error
    """
    if not sites:
        logger.warning("No sites specified for environment reset")
        return True

    logger.info(f"Resetting WebArena environment for sites: {sites}")

    # Reset containers
    if not reset_containers(sites, wait=True):
        logger.error("Failed to reset containers")
        return False

    # Run auto-login
    if run_login:
        if not run_auto_login(
            sites, auth_folder=auth_folder, webarena_root=webarena_root
        ):
            logger.error("Failed to run auto-login")
            return False

    logger.info("WebArena environment reset complete")
    return True


def get_sites_for_task(task_config: dict) -> List[str]:
    """
    Extract the list of sites from a task configuration.

    Args:
        task_config: Task configuration dict (may contain 'sites' or 'webarena_config.sites')

    Returns:
        List of site names
    """
    # Direct sites list
    if "sites" in task_config:
        return task_config["sites"]

    # Nested in webarena_config
    if "webarena_config" in task_config and "sites" in task_config["webarena_config"]:
        return task_config["webarena_config"]["sites"]

    # Try to infer from task ID or initial_url
    task_id = task_config.get("id", "")
    initial_url = task_config.get("initial_url", "")

    sites = []
    if "gitlab" in task_id.lower() or ":8023" in initial_url or "GITLAB" in initial_url:
        sites.append("gitlab")
    if (
        "shopping_admin" in task_id.lower()
        or ":7780" in initial_url
        or "SHOPPING_ADMIN" in initial_url
    ):
        sites.append("shopping_admin")
    elif (
        "shopping" in task_id.lower()
        or ":7770" in initial_url
        or "SHOPPING" in initial_url
    ):
        sites.append("shopping")
    if (
        "reddit" in task_id.lower()
        or "forum" in task_id.lower()
        or ":9999" in initial_url
        or "REDDIT" in initial_url
    ):
        sites.append("reddit")

    return sites


if __name__ == "__main__":
    # Simple CLI for testing
    import argparse

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="Reset WebArena Docker containers")
    parser.add_argument("--sites", nargs="+", required=True, help="Sites to reset")
    parser.add_argument("--no-login", action="store_true", help="Skip auto-login")
    parser.add_argument("--auth-folder", default="./.auth", help="Auth folder path")

    args = parser.parse_args()

    success = reset_webarena_env(
        sites=args.sites, run_login=not args.no_login, auth_folder=args.auth_folder
    )

    exit(0 if success else 1)
