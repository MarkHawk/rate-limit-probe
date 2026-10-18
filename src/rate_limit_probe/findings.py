"""Findings and the report they make up, in the same shape as token-inspector."""

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    INFO = "INFO"  # a measurement, neither good nor bad


@dataclass
class Finding:
    check_id: str
    status: Status
    title: str  # short label, e.g. "Retry-After"
    summary: str  # plain English, one or two sentences
    details: dict = field(default_factory=dict)  # facts for causes and --json, never wording


@dataclass
class Report:
    result: object  # probe.ProbeResult
    facts: object  # analyse.Facts
    findings: list = field(default_factory=list)
    likely_cause: object = None  # causes.Cause
    notes: list = field(default_factory=list)  # e.g. a flag lowered to its ceiling

    def count(self, status):
        return sum(1 for f in self.findings if f.status == status)

    def finding(self, check_id):
        return next((f for f in self.findings if f.check_id == check_id), None)

    @property
    def unreachable(self):
        """No HTTP response at all: the target could not be reached."""
        return not any(o.status is not None for o in self.result.observations)

    @property
    def exit_code(self):
        if self.unreachable:
            return 2
        return 1 if self.count(Status.FAIL) else 0
