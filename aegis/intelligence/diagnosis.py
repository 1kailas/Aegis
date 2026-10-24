"""AI incident diagnosis engine with multi-tiered fallback and cost control.

Resolution order:
1. Local AI model (Ollama: qwen2.5:1.5b)
2. Cloud AI fallback (Gemini API: bounded by hourly budget, timeout, and redaction)
3. Deterministic expert rules (offline, local, zero-cost, completely reliable)

AI-generated explanations are strictly validated and labeled as ASSESSMENTS
rather than established facts. Never makes routine monitoring dependent on cloud calls.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.intelligence.diagnosis")


class ActionProposal(BaseModel):
    action: str
    target: str
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, PROHIBITED
    reason: str
    rollback_plan: str
    requires_approval: bool = False


class DiagnosisSchema(BaseModel):
    root_cause_hypothesis: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    is_code_or_config_issue: bool
    affected_components: list[str] = Field(default_factory=list)
    recommended_actions: list[ActionProposal] = Field(default_factory=list)
    diagnostic_steps: list[str] = Field(default_factory=list)
    reproduction_notes: str = ""


@dataclass
class DiagnosisResult:
    incident_id: str
    provider: str  # local_ollama | cloud_gemini | deterministic_expert
    model_name: str
    status: str = "ASSESSMENT"
    root_cause_hypothesis: str = ""
    confidence_score: float = 0.5
    is_code_or_config_issue: bool = False
    affected_components: list[str] = field(default_factory=list)
    recommended_actions: list[dict[str, Any]] = field(default_factory=list)
    diagnostic_steps: list[str] = field(default_factory=list)
    reproduction_notes: str = ""
    tokens_used: int = 0
    created_at: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "provider": self.provider,
            "model_name": self.model_name,
            "status": self.status,
            "root_cause_hypothesis": self.root_cause_hypothesis,
            "confidence_score": self.confidence_score,
            "is_code_or_config_issue": self.is_code_or_config_issue,
            "affected_components": self.affected_components,
            "recommended_actions": self.recommended_actions,
            "diagnostic_steps": self.diagnostic_steps,
            "reproduction_notes": self.reproduction_notes,
            "tokens_used": self.tokens_used,
            "created_at": self.created_at,
        }


class IncidentDiagnoser:
    """Investigates incidents using multi-tier fallback with cost and safety controls."""

    def __init__(
        self,
        ollama_url: str = "http://127.0.0.1:11434",
        local_model: str = "qwen2.5:1.5b",
        gemini_api_key: str | None = None,
        gemini_model: str = "gemini-flash-latest",
        max_gemini_requests_per_hour: int = 6,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.local_model = local_model
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model
        self.max_gemini_requests_per_hour = max_gemini_requests_per_hour
        self._gemini_request_timestamps: list[float] = []

    def diagnose(
        self, incident: dict[str, Any], telemetry_context: dict[str, Any] | None = None
    ) -> DiagnosisResult:
        """Diagnoses an incident, returning an evidence-backed Assessment."""
        iid = incident.get("id", f"INC-{utcnow()}")
        sanitized_context = self._prepare_context(incident, telemetry_context)

        # 1. Try local model (Ollama)
        try:
            local_res = self._diagnose_local(iid, sanitized_context)
            if local_res:
                return local_res
        except Exception as e:
            _LOG.debug("Local diagnosis skipped/failed: %s", e)

        # 2. Try Cloud Gemini fallback if budget permits
        if self.gemini_api_key and self._can_call_gemini():
            try:
                gemini_res = self._diagnose_gemini(iid, sanitized_context)
                if gemini_res:
                    return gemini_res
            except Exception as e:
                _LOG.warning("Cloud Gemini fallback error: %s; falling back to expert rules", e)

        # 3. Deterministic expert rules (guaranteed to succeed locally)
        return self._diagnose_expert_rules(iid, incident, sanitized_context)

    def _can_call_gemini(self) -> bool:
        now = time.time()
        self._gemini_request_timestamps = [
            t for t in self._gemini_request_timestamps if now - t <= 3600
        ]
        return len(self._gemini_request_timestamps) < self.max_gemini_requests_per_hour

    def _record_gemini_call(self) -> None:
        self._gemini_request_timestamps.append(time.time())

    def _prepare_context(
        self, incident: dict[str, Any], telemetry: dict[str, Any] | None
    ) -> str:
        """Sanitizes sensitive data and formats incident facts for AI prompt."""
        category = incident.get("category", "unknown")
        severity = incident.get("severity", "warning")
        title = incident.get("title", "")
        summary = incident.get("summary", "")
        evidence = incident.get("evidence") or []

        prompt_facts = [
            f"Incident ID: {incident.get('id')}",
            f"Category: {category}",
            f"Severity: {severity}",
            f"Title: {title}",
            f"Summary: {summary}",
            f"Supporting Evidence: {json.dumps(evidence, default=str)[:1000]}",
        ]
        if telemetry:
            prompt_facts.append(f"Host State: CPU={telemetry.get('cpu_percent')}% RAM={telemetry.get('memory', {}).get('percent')}%")

        joined = "\n".join(prompt_facts)
        # Redact tokens and keys
        joined = re.sub(r"(AIzaSy[A-Za-z0-9_-]{33})", "[REDACTED_GEMINI_KEY]", joined)
        joined = re.sub(r"(gh[pousr]_[A-Za-z0-9_]{30,})", "[REDACTED_GITHUB_TOKEN]", joined)
        return joined

    def _diagnose_local(self, iid: str, context: str) -> DiagnosisResult | None:
        url = f"{self.ollama_url}/api/generate"
        system = (
            "You are Aegis AI Infrastructure Diagnoser. Analyze the incident facts.\n"
            "Respond strictly in JSON matching this schema:\n"
            "{\n"
            '  "root_cause_hypothesis": "string",\n'
            '  "confidence_score": 0.8,\n'
            '  "is_code_or_config_issue": true,\n'
            '  "affected_components": ["component1"],\n'
            '  "recommended_actions": [{"action": "restart_container", "target": "c1", "risk_level": "LOW", "reason": "str", "rollback_plan": "str", "requires_approval": false}],\n'
            '  "diagnostic_steps": ["step 1"],\n'
            '  "reproduction_notes": "repro notes"\n'
            "}"
        )
        payload = {
            "model": self.local_model,
            "prompt": f"{system}\n\nIncident Telemetry:\n{context}",
            "stream": False,
            "format": "json",
        }
        with httpx.Client(timeout=10.0) as client:
            res = client.post(url, json=payload)
            if res.status_code == 200:
                raw_json = res.json().get("response", "{}")
                validated = DiagnosisSchema.model_validate_json(raw_json)
                return DiagnosisResult(
                    incident_id=iid,
                    provider="local_ollama",
                    model_name=self.local_model,
                    root_cause_hypothesis=validated.root_cause_hypothesis,
                    confidence_score=validated.confidence_score,
                    is_code_or_config_issue=validated.is_code_or_config_issue,
                    affected_components=validated.affected_components,
                    recommended_actions=[a.model_dump() for a in validated.recommended_actions],
                    diagnostic_steps=validated.diagnostic_steps,
                    reproduction_notes=validated.reproduction_notes,
                )
        return None

    def _diagnose_gemini(self, iid: str, context: str) -> DiagnosisResult | None:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        instruction = (
            "Analyze this infrastructure incident and output ONLY valid JSON matching this schema:\n"
            "{\n"
            '  "root_cause_hypothesis": "string",\n'
            '  "confidence_score": 0.85,\n'
            '  "is_code_or_config_issue": true,\n'
            '  "affected_components": ["component"],\n'
            '  "recommended_actions": [{"action": "restart", "target": "target", "risk_level": "LOW", "reason": "why", "rollback_plan": "none", "requires_approval": false}],\n'
            '  "diagnostic_steps": ["inspect logs"],\n'
            '  "reproduction_notes": "notes"\n'
            "}\n"
        )
        payload = {
            "contents": [
                {"parts": [{"text": f"{instruction}\n\nIncident Details:\n{context}"}]}
            ]
        }
        self._record_gemini_call()
        with httpx.Client(timeout=8.0) as client:
            res = client.post(url, json=payload)
            if res.status_code == 200:
                cands = res.json().get("candidates", [])
                if cands:
                    text = cands[0]["content"]["parts"][0]["text"].strip()
                    # Clean markdown codeblocks if wrapped in ```json
                    if text.startswith("```"):
                        text = re.sub(r"^```[a-z]*\n?", "", text)
                        text = re.sub(r"\n?```$", "", text)
                    validated = DiagnosisSchema.model_validate_json(text)
                    return DiagnosisResult(
                        incident_id=iid,
                        provider="cloud_gemini",
                        model_name=self.gemini_model,
                        root_cause_hypothesis=validated.root_cause_hypothesis,
                        confidence_score=validated.confidence_score,
                        is_code_or_config_issue=validated.is_code_or_config_issue,
                        affected_components=validated.affected_components,
                        recommended_actions=[a.model_dump() for a in validated.recommended_actions],
                        diagnostic_steps=validated.diagnostic_steps,
                        reproduction_notes=validated.reproduction_notes,
                    )
        return None

    def _diagnose_expert_rules(
        self, iid: str, incident: dict[str, Any], context: str
    ) -> DiagnosisResult:
        """Deterministic expert rule-based diagnosis engine."""
        cat = incident.get("category", "")
        summary = incident.get("summary", "")
        evidence = incident.get("evidence") or []

        actions: list[dict[str, Any]] = []
        steps: list[str] = []
        is_code = False
        confidence = 0.85

        if "oom" in cat or "137" in summary:
            hypothesis = "Process or container exceeded allocated memory limits or encountered a rapid memory leak."
            is_code = True
            actions.append({
                "action": "increase_memory_limit_or_investigate_leak",
                "target": "workload_limits",
                "risk_level": "LOW",
                "reason": "Container terminated with Linux OOM kill (exit code 137)",
                "rollback_plan": "Restore previous memory quota",
                "requires_approval": False,
            })
            steps = [
                "Inspect container resident set size (RSS) trajectory prior to exit.",
                "Review recent memory allocations or unbounded query results in application logs.",
                "Verify container cgroup memory limits against application baseline requirements.",
            ]
        elif "crash_loop" in cat:
            hypothesis = "Application process crashed on startup due to unhandled exception, bad configuration, or missing dependency."
            is_code = True
            actions.append({
                "action": "inspect_startup_stacktrace",
                "target": "application_code",
                "risk_level": "LOW",
                "reason": "Process repeatedly exiting with non-zero status",
                "rollback_plan": "N/A",
                "requires_approval": False,
            })
            steps = [
                "Extract last 100 lines of standard error output.",
                "Check for missing environment variables or unresolvable connection strings.",
                "Verify database schema migrations or dependent endpoint health.",
            ]
        elif "thermal" in cat:
            hypothesis = "Sustained high hardware temperature or thermal throttling caused by intensive computation or cooling restriction."
            is_code = False
            confidence = 0.90
            actions.append({
                "action": "set_backpressure",
                "target": "inference_queue",
                "risk_level": "LOW",
                "reason": "Hardware temperature crossed thermal safety threshold",
                "rollback_plan": "Clear backpressure once temperatures return below recovery threshold",
                "requires_approval": False,
            })
            steps = [
                "Identify top CPU/GPU resource-consuming processes.",
                "Verify cooling fans and ventilation airflow.",
                "Engage rate limiting and temporary batch size reduction.",
            ]
        elif "resource" in cat:
            hypothesis = "Host resource saturation: memory, disk, or CPU capacity exceeded configured warning/critical thresholds."
            is_code = False
            confidence = 0.90
            actions.append({
                "action": "recommend_storage_cleanup_or_cache_trim",
                "target": "host_resources",
                "risk_level": "LOW",
                "reason": summary,
                "rollback_plan": "Re-enable cache filling",
                "requires_approval": False,
            })
            steps = [
                "Inspect disk partitions for runaway log files or temporary artifacts.",
                "Review memory breakdown between active processes and buffer/cache.",
            ]
        else:
            hypothesis = f"Operational anomaly in category '{cat}': {summary}"
            steps = ["Review recent service logs and correlated telemetry around the incident timestamp."]

        return DiagnosisResult(
            incident_id=iid,
            provider="deterministic_expert",
            model_name="aegis-rule-engine-v1",
            root_cause_hypothesis=hypothesis,
            confidence_score=confidence,
            is_code_or_config_issue=is_code,
            affected_components=[cat],
            recommended_actions=actions,
            diagnostic_steps=steps,
            reproduction_notes="Reproduce by observing workload under matching resource and input parameters.",
        )
