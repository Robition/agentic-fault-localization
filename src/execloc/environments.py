"""Docker lifecycle handling shared by the two experiment arms."""

from __future__ import annotations

import logging
import subprocess

from minisweagent.environments.docker import DockerEnvironment


class ManagedDockerEnvironment(DockerEnvironment):
    """Keep mini-swe-agent execution behavior with portable, bounded cleanup."""

    def cleanup(self) -> None:
        """Remove only this environment's container with a bounded Docker call.

        The upstream destructor also calls cleanup, including after failed
        initialization. Clear ownership once removal succeeds or the container
        is already absent, making subsequent calls harmless. Retain the ID on
        other failures so that cleanup can be retried. Cleanup errors are logged
        rather than replacing the agent's result.
        """
        container_id = getattr(self, "container_id", None)
        if not container_id:
            return
        logger = getattr(self, "logger", None) or logging.getLogger(__name__)
        executable = getattr(getattr(self, "config", None), "executable", "docker")

        try:
            result = subprocess.run(
                [executable, "rm", "--force", "--", container_id],
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except Exception as error:
            logger.warning("Could not remove Docker container %s: %s", container_id, error)
            return

        if result.returncode:
            detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
            # --rm containers may disappear before cleanup, including when
            # their lifetime expires or another owner stops Docker Desktop.
            if "no such container" in detail.lower():
                logger.debug("Docker container %s is already absent", container_id)
                self.container_id = None
            else:
                logger.warning("Could not remove Docker container %s: %s", container_id, detail)
        else:
            self.container_id = None
