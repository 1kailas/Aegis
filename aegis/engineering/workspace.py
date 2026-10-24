"""Isolated repair workspace manager and patch validation pipeline.

All untrusted repair code and patches are evaluated inside an isolated,
ephemeral directory with sanitized environment variables, strict timeouts,
path traversal checks, and scope enforcement.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.engineering.workspace")

PROHIBITED_PATCH_PATTERNS = [
    re.compile(r"^\.env", re.I),
    re.compile(r"^\.github/workflows/", re.I),
    re.compile(r"(key|secret|token|password|cred)", re.I),
    re.compile(r"(\.\./|/\.\.)"),  # Path traversal
]

SECRET_ENV_PATTERNS = re.compile(
    r"(token|key|secret|password|auth|credential|api_key|priv)", re.IGNORECASE
)


@dataclass
class ValidationStepResult:
    command: str
    exit_code: int
    duration_s: float
    stdout: str
    stderr: str
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_s": self.duration_s,
            "stdout": self.stdout[:2000],  # truncated for safety
            "stderr": self.stderr[:2000],
            "passed": self.passed,
        }


@dataclass
class ValidationReport:
    task_id: str
    passed: bool
    scope_valid: bool
    changed_files: list[str]
    steps: list[ValidationStepResult] = field(default_factory=list)
    rejection_reason: str | None = None
    created_at: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "scope_valid": self.scope_valid,
            "changed_files": self.changed_files,
            "steps": [s.as_dict() for s in self.steps],
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at,
        }


class IsolatedWorkspaceManager:
    """Creates isolated repair environments and runs test validation safely."""

    def __init__(self, base_repo_dir: Path | str, workspaces_root: Path | str = "repair-workspaces"):
        self.base_repo_dir = Path(base_repo_dir).resolve()
        self.workspaces_root = Path(workspaces_root).resolve()
        self.workspaces_root.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, task_id: str) -> Path:
        """Creates an ephemeral copy of the repository in the workspaces directory."""
        ws_dir = self.workspaces_root / task_id
        if ws_dir.exists():
            shutil.rmtree(ws_dir)
        ws_dir.mkdir(parents=True)

        # Clone or copy non-ignored files
        subprocess.run(
            ["git", "clone", "--local", str(self.base_repo_dir), str(ws_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        return ws_dir

    def inspect_patch_scope(self, patch_text: str) -> tuple[bool, list[str], str | None]:
        """Validates that a patch does not modify prohibited files or escape the repo."""
        changed_files: list[str] = []
        for line in patch_text.splitlines():
            # Git diff target lines: +++ b/path/to/file or diff --git a/file b/file
            if line.startswith("+++ b/"):
                target = line[6:].strip()
                changed_files.append(target)
            elif line.startswith("diff --git a/"):
                parts = line.split()
                if len(parts) >= 4:
                    target = parts[3].lstrip("b/")
                    if target not in changed_files:
                        changed_files.append(target)

        if not changed_files:
            return False, [], "No file modifications detected in patch."

        for f in changed_files:
            for pattern in PROHIBITED_PATCH_PATTERNS:
                if pattern.search(f):
                    return (
                        False,
                        changed_files,
                        f"Patch rejected: file '{f}' violates scope policy (matches prohibited pattern {pattern.pattern})",
                    )

        return True, changed_files, None

    def apply_and_validate(
        self,
        task_id: str,
        patch_text: str,
        test_commands: list[str] | None = None,
        timeout_s: float = 60.0,
    ) -> ValidationReport:
        """Executes the full validation pipeline in an isolated workspace."""
        # 1. Validate patch scope
        scope_valid, changed_files, reason = self.inspect_patch_scope(patch_text)
        if not scope_valid:
            return ValidationReport(
                task_id=task_id,
                passed=False,
                scope_valid=False,
                changed_files=changed_files,
                rejection_reason=reason,
            )

        ws_dir = self.create_workspace(task_id)
        patch_file = ws_dir / "proposed_fix.patch"
        patch_file.write_text(patch_text, encoding="utf-8")

        # 2. Check and apply patch
        apply_res = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=str(ws_dir),
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if apply_res.returncode != 0:
            return ValidationReport(
                task_id=task_id,
                passed=False,
                scope_valid=True,
                changed_files=changed_files,
                rejection_reason=f"git apply check failed: {apply_res.stderr.strip()}",
            )

        subprocess.run(
            ["git", "apply", str(patch_file)],
            cwd=str(ws_dir),
            check=True,
            capture_output=True,
            text=True,
            timeout=10.0,
        )

        # 3. Build sanitized environment
        sanitized_env = self._build_sanitized_env(ws_dir)

        # 4. Run test commands
        commands = test_commands or ["pytest -q"]
        steps: list[ValidationStepResult] = []
        all_passed = True

        for cmd in commands:
            start_t = time.time()
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=str(ws_dir),
                    env=sanitized_env,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
                dur = round(time.time() - start_t, 2)
                passed = proc.returncode == 0
                if not passed:
                    all_passed = False
                steps.append(ValidationStepResult(
                    command=cmd,
                    exit_code=proc.returncode,
                    duration_s=dur,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    passed=passed,
                ))
            except subprocess.TimeoutExpired:
                all_passed = False
                steps.append(ValidationStepResult(
                    command=cmd,
                    exit_code=124,
                    duration_s=timeout_s,
                    stdout="",
                    stderr=f"Command timed out after {timeout_s}s",
                    passed=False,
                ))

        return ValidationReport(
            task_id=task_id,
            passed=all_passed,
            scope_valid=True,
            changed_files=changed_files,
            steps=steps,
            rejection_reason=None if all_passed else "One or more validation test commands failed.",
        )

    def cleanup_workspace(self, task_id: str) -> None:
        ws_dir = self.workspaces_root / task_id
        if ws_dir.exists():
            shutil.rmtree(ws_dir, ignore_errors=True)

    def _build_sanitized_env(self, ws_dir: Path | None = None) -> dict[str, str]:
        """Strips all sensitive keys and tokens from the execution environment."""
        sanitized = {}
        for k, v in os.environ.items():
            if SECRET_ENV_PATTERNS.search(k):
                continue
            sanitized[k] = v
        # Ensure venv python is at the front of PATH
        venv_bin = str(self.base_repo_dir / ".venv/bin")
        if Path(venv_bin).exists():
            sanitized["PATH"] = f"{venv_bin}:{sanitized.get('PATH', '')}"
            sanitized["VIRTUAL_ENV"] = str(self.base_repo_dir / ".venv")
        if ws_dir:
            sanitized["PYTHONPATH"] = f"{str(ws_dir)}:{sanitized.get('PYTHONPATH', '')}".rstrip(":")
        return sanitized
