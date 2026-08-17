"""Exhaustive deny-by-default mutation policy tables."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import (
    ApprovalRequirement,
    ContractViolation,
    MutationClass,
    PolicyDisposition,
    PolicyPhase,
    ProcessorOperation,
    ReplicationOperation,
    Service,
)


def _codes(*values: str) -> tuple[str, ...]:
    return tuple(sorted(values))


@dataclass(frozen=True)
class MutationPolicyRow:
    service: Service
    operation_code: str
    wave_c: PolicyDisposition
    future_successor: PolicyDisposition
    approval_requirement: ApprovalRequirement
    mutation_class: MutationClass
    precondition_codes: tuple[str, ...]
    required_evidence_codes: tuple[str, ...]
    verification_requirement_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.service, Service):
            raise ContractViolation("policy service is invalid")
        if not isinstance(self.operation_code, str) or not self.operation_code:
            raise ContractViolation("operation_code is required")
        if not isinstance(self.wave_c, PolicyDisposition):
            raise ContractViolation("wave_c disposition is invalid")
        if not isinstance(self.future_successor, PolicyDisposition):
            raise ContractViolation("future_successor disposition is invalid")
        if not isinstance(self.approval_requirement, ApprovalRequirement):
            raise ContractViolation("approval requirement is invalid")
        if not isinstance(self.mutation_class, MutationClass):
            raise ContractViolation("mutation class is invalid")
        for field in (
            "precondition_codes",
            "required_evidence_codes",
            "verification_requirement_codes",
        ):
            values = getattr(self, field)
            if not isinstance(values, tuple) or not values or values != tuple(sorted(set(values))):
                raise ContractViolation(f"{field} must be non-empty, unique, and sorted")
        if self.mutation_class is MutationClass.BOUNDED_EXECUTION and self.future_successor is not PolicyDisposition.ALLOW:
            raise ContractViolation("bounded execution rows must be allowed in the future successor")
        if self.mutation_class is MutationClass.FORBIDDEN:
            if self.wave_c is not PolicyDisposition.FORBID or self.future_successor is not PolicyDisposition.FORBID:
                raise ContractViolation("forbidden mutation rows must be forbidden in every phase")
            if self.approval_requirement is not ApprovalRequirement.NOT_APPROVABLE:
                raise ContractViolation("forbidden mutation rows cannot be approvable")

    def disposition_for(self, phase: PolicyPhase) -> PolicyDisposition:
        if not isinstance(phase, PolicyPhase):
            raise ContractViolation("policy phase is invalid")
        return self.wave_c if phase is PolicyPhase.WAVE_C else self.future_successor


def _row(
    service: Service,
    operation: Enum,
    wave_c: PolicyDisposition,
    future: PolicyDisposition,
    approval: ApprovalRequirement,
    mutation_class: MutationClass,
    preconditions: tuple[str, ...],
    evidence: tuple[str, ...],
    verification: tuple[str, ...],
) -> MutationPolicyRow:
    return MutationPolicyRow(
        service=service,
        operation_code=str(operation.value),
        wave_c=wave_c,
        future_successor=future,
        approval_requirement=approval,
        mutation_class=mutation_class,
        precondition_codes=preconditions,
        required_evidence_codes=evidence,
        verification_requirement_codes=verification,
    )


_READ_PRE = _codes("authority-bound", "fresh-observation")
_READ_EVIDENCE = _codes("authority-reference", "observation-hash")
_READ_VERIFY = _codes("canonical-encoding")
_CLASSIFY_PRE = _codes("deterministic-classifier", "observation-bound")
_CLASSIFY_EVIDENCE = _codes("classification-hash", "observation-hash")
_CLASSIFY_VERIFY = _codes("classifier-replay")
_PROPOSE_PRE = _codes("classification-bound", "fresh-observation", "policy-row-resolved")
_PROPOSE_EVIDENCE = _codes("classification-hash", "observation-hash", "proposal-hash")
_PROPOSE_VERIFY = _codes("policy-replay", "proposal-hash")
_ESCALATE_PRE = _codes("authority-bound", "classification-bound")
_ESCALATE_EVIDENCE = _codes("classification-hash", "escalation-reason", "observation-hash")
_ESCALATE_VERIFY = _codes("human-authority-record")
_FORBID_PRE = _codes("none")
_FORBID_EVIDENCE = _codes("policy-denial")
_FORBID_VERIFY = _codes("not-applicable")


PROCESSOR_MUTATION_MATRIX = (
    _row(Service.PROCESSOR, ProcessorOperation.READ_OBSERVATION, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _READ_PRE, _READ_EVIDENCE, _READ_VERIFY),
    _row(Service.PROCESSOR, ProcessorOperation.CLASSIFY, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _CLASSIFY_PRE, _CLASSIFY_EVIDENCE, _CLASSIFY_VERIFY),
    _row(Service.PROCESSOR, ProcessorOperation.EXPLAIN, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _CLASSIFY_PRE, _CLASSIFY_EVIDENCE, _codes("structured-findings")),
    _row(Service.PROCESSOR, ProcessorOperation.NO_ACTION, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _PROPOSE_PRE, _PROPOSE_EVIDENCE, _codes("converged-state-replay")),
    _row(Service.PROCESSOR, ProcessorOperation.RETRY_OBSERVATION, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _codes("fresh-observation", "retryable-classification"), _PROPOSE_EVIDENCE, _codes("observation-refresh")),
    _row(Service.PROCESSOR, ProcessorOperation.PROPOSE_CATCH_UP, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.PROPOSAL_ONLY, _PROPOSE_PRE, _PROPOSE_EVIDENCE, _PROPOSE_VERIFY),
    _row(Service.PROCESSOR, ProcessorOperation.ESCALATE_STATE, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _ESCALATE_EVIDENCE, _ESCALATE_VERIFY),
    _row(Service.PROCESSOR, ProcessorOperation.INVOKE_WORKER_RUN, PolicyDisposition.FORBID, PolicyDisposition.ALLOW, ApprovalRequirement.EXPLICIT_OWNER, MutationClass.BOUNDED_EXECUTION, _codes("approval-scope-bound", "fresh-observation", "worker-input-exact"), _codes("approval-hash", "proposal-hash", "worker-input-hash"), _codes("lineage-readback", "state-readback", "worker-receipt")),
    _row(Service.PROCESSOR, ProcessorOperation.CHANGE_PROCESSOR_IDENTITY, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _ESCALATE_EVIDENCE, _codes("separate-acceptance")),
    _row(Service.PROCESSOR, ProcessorOperation.RUN_MIGRATIONS, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.OPERATOR_OWNED, MutationClass.ESCALATION_ONLY, _codes("operator-owned"), _codes("migration-plan"), _codes("independent-migration-acceptance")),
    _row(Service.PROCESSOR, ProcessorOperation.DIRECT_DATABASE_MUTATION, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.PROCESSOR, ProcessorOperation.DIRECT_SPOOL_MUTATION, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.PROCESSOR, ProcessorOperation.BYPASS_LOCK, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.PROCESSOR, ProcessorOperation.GENERAL_SHELL, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
)


_REPLICATION_TARGET_PRE = _codes("authority-bound", "fresh-observation", "target-identity-bound")
_REPLICATION_TARGET_EVIDENCE = _codes("observation-hash", "target-identity-evidence", "target-id")
_REPLICATION_EXEC_PRE = _codes("approval-scope-bound", "fresh-observation", "target-identity-bound", "worker-input-exact")
_REPLICATION_EXEC_EVIDENCE = _codes("proposal-hash", "target-identity-evidence", "worker-input-hash")

REPLICATION_MUTATION_MATRIX = (
    _row(Service.REPLICATION, ReplicationOperation.READ_OBSERVATION, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _READ_PRE, _READ_EVIDENCE, _READ_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.CLASSIFY, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _CLASSIFY_PRE, _CLASSIFY_EVIDENCE, _CLASSIFY_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.EXPLAIN, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _CLASSIFY_PRE, _CLASSIFY_EVIDENCE, _codes("structured-findings")),
    _row(Service.REPLICATION, ReplicationOperation.NO_ACTION, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _PROPOSE_PRE, _PROPOSE_EVIDENCE, _codes("converged-state-replay")),
    _row(Service.REPLICATION, ReplicationOperation.RETRY_OBSERVATION, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.READ_ONLY, _codes("fresh-observation", "retryable-classification", "target-identity-bound"), _REPLICATION_TARGET_EVIDENCE, _codes("observation-refresh")),
    _row(Service.REPLICATION, ReplicationOperation.PROPOSE_VERIFY, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.PROPOSAL_ONLY, _REPLICATION_TARGET_PRE, _codes("proposal-hash", "target-id", "target-identity-evidence"), _codes("provider-readback-plan")),
    _row(Service.REPLICATION, ReplicationOperation.PROPOSE_RECONCILE, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.PROPOSAL_ONLY, _REPLICATION_TARGET_PRE, _codes("failed-pending-evidence", "proposal-hash", "target-id"), _codes("database-and-provider-readback-plan")),
    _row(Service.REPLICATION, ReplicationOperation.PROPOSE_RUN, PolicyDisposition.ALLOW, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.PROPOSAL_ONLY, _REPLICATION_TARGET_PRE, _codes("backlog-evidence", "proposal-hash", "target-id"), _codes("independent-target-verification-plan")),
    _row(Service.REPLICATION, ReplicationOperation.ESCALATE_STATE, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _ESCALATE_EVIDENCE, _ESCALATE_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.INVOKE_VERIFY, PolicyDisposition.FORBID, PolicyDisposition.ALLOW, ApprovalRequirement.NONE, MutationClass.BOUNDED_EXECUTION, _REPLICATION_EXEC_PRE, _REPLICATION_EXEC_EVIDENCE, _codes("provider-readback", "target-identity-readback", "worker-receipt")),
    _row(Service.REPLICATION, ReplicationOperation.INVOKE_RECONCILE, PolicyDisposition.FORBID, PolicyDisposition.ALLOW, ApprovalRequirement.EXPLICIT_OWNER, MutationClass.BOUNDED_EXECUTION, _REPLICATION_EXEC_PRE, _codes("approval-hash", "failed-pending-evidence", "proposal-hash", "worker-input-hash"), _codes("database-readback", "provider-readback", "worker-receipt")),
    _row(Service.REPLICATION, ReplicationOperation.INVOKE_RUN, PolicyDisposition.FORBID, PolicyDisposition.ALLOW, ApprovalRequirement.EXPLICIT_OWNER, MutationClass.BOUNDED_EXECUTION, _REPLICATION_EXEC_PRE, _codes("approval-hash", "backlog-evidence", "proposal-hash", "worker-input-hash"), _codes("independent-target-verification", "provider-readback", "worker-receipt")),
    _row(Service.REPLICATION, ReplicationOperation.INITIALIZE_TARGET, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _codes("target-design-authority", "target-identity-plan"), _codes("identity-marker-readback", "separate-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.CHANGE_TARGET_IDENTITY, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _codes("current-target-identity", "replacement-target-identity"), _codes("full-target-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.CHANGE_TARGET_ROOT, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _codes("current-target-root", "replacement-target-root"), _codes("full-target-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.CHANGE_TARGET_MARKER_CONTENT, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _codes("current-marker-hash", "replacement-marker-hash"), _codes("identity-marker-readback", "separate-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.CHANGE_TARGET_MARKER_METADATA, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _codes("current-marker-metadata", "replacement-marker-metadata"), _codes("identity-marker-readback", "separate-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.CHANGE_TARGET_NAMESPACE, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _codes("current-namespace", "replacement-namespace"), _codes("full-target-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.CHANGE_DATABASE_BINDING, PolicyDisposition.ESCALATE, PolicyDisposition.ESCALATE, ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY, MutationClass.ESCALATION_ONLY, _ESCALATE_PRE, _codes("current-database-binding", "replacement-database-binding"), _codes("full-target-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.PROVISION_PROVIDER, PolicyDisposition.FORBID, PolicyDisposition.ESCALATE, ApprovalRequirement.OPERATOR_OWNED, MutationClass.ESCALATION_ONLY, _codes("operator-owned", "provider-design-authority"), _codes("provider-plan"), _codes("provider-specific-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.CHANGE_IAM_OR_CREDENTIALS, PolicyDisposition.FORBID, PolicyDisposition.ESCALATE, ApprovalRequirement.OPERATOR_OWNED, MutationClass.ESCALATION_ONLY, _codes("operator-owned", "security-authority"), _codes("credential-free-change-record", "iam-plan"), _codes("security-acceptance")),
    _row(Service.REPLICATION, ReplicationOperation.BYPASS_COLLISION, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.WEAKEN_IMMUTABILITY, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.DIRECT_FINAL_OBJECT_MUTATION, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.DIRECT_PARTIAL_CLEANUP, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _codes("worker-owned-cleanup-receipt")),
    _row(Service.REPLICATION, ReplicationOperation.DIRECT_DATABASE_MUTATION, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.GENERAL_PROVIDER_API, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.DESTRUCTIVE_PROVIDER_ACTION, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
    _row(Service.REPLICATION, ReplicationOperation.GENERAL_SHELL, PolicyDisposition.FORBID, PolicyDisposition.FORBID, ApprovalRequirement.NOT_APPROVABLE, MutationClass.FORBIDDEN, _FORBID_PRE, _FORBID_EVIDENCE, _FORBID_VERIFY),
)


def _deny(service: Service) -> MutationPolicyRow:
    return MutationPolicyRow(
        service=service,
        operation_code="UNKNOWN_OPERATION",
        wave_c=PolicyDisposition.FORBID,
        future_successor=PolicyDisposition.FORBID,
        approval_requirement=ApprovalRequirement.NOT_APPROVABLE,
        mutation_class=MutationClass.FORBIDDEN,
        precondition_codes=_FORBID_PRE,
        required_evidence_codes=_FORBID_EVIDENCE,
        verification_requirement_codes=_FORBID_VERIFY,
    )


def _code(value: str | Enum) -> str:
    return str(value.value) if isinstance(value, Enum) else str(value)


def resolve_processor_policy(operation: str | ProcessorOperation) -> MutationPolicyRow:
    code = _code(operation)
    return next((row for row in PROCESSOR_MUTATION_MATRIX if row.operation_code == code), _deny(Service.PROCESSOR))


def resolve_replication_policy(operation: str | ReplicationOperation) -> MutationPolicyRow:
    code = _code(operation)
    return next((row for row in REPLICATION_MUTATION_MATRIX if row.operation_code == code), _deny(Service.REPLICATION))
