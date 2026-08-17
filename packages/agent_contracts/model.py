"""Versioned framework-neutral bounded-agent contract records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import ClassVar, TypeAlias

from .canonical import canonical_sha256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


class ContractViolation(ValueError):
    """Raised when a contract would become ambiguous or unsafe."""


class Service(str, Enum):
    PROCESSOR = "processor-worker"
    REPLICATION = "replication-worker"


class Severity(str, Enum):
    PASS = "pass"
    ATTENTION = "attention"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class Actionability(str, Enum):
    NONE = "none"
    RETRY = "retry"
    PROPOSE = "propose"
    ESCALATE = "escalate"


class ProcessorIntegrityStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class ReplicationIdentityStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_CONFIGURED = "not-configured"
    UNAVAILABLE = "unavailable"


class ProviderVerification(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class SourceIntegrityStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class ApprovalOutcome(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    EXPIRE = "expire"


class ExecutionStatus(str, Enum):
    NOT_RUN = "not-run"
    DENIED = "denied"
    EXECUTED = "executed"
    FAILED = "failed"


class VerificationStatus(str, Enum):
    NOT_RUN = "not-run"
    PASS = "pass"
    FAIL = "fail"
    ESCALATE = "escalate"
    UNAVAILABLE = "unavailable"


class ReceiptStatus(str, Enum):
    READ_ONLY_COMPLETE = "read-only-complete"
    NOT_RUN = "not-run"
    DENIED = "denied"
    EXPIRED = "expired"
    EXECUTION_FAILED = "execution-failed"
    EXECUTED_UNVERIFIED = "executed-unverified"
    ESCALATED = "escalated"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification-failed"


class PolicyDisposition(str, Enum):
    ALLOW = "allow"
    FORBID = "forbid"
    ESCALATE = "escalate"


class ApprovalRequirement(str, Enum):
    NONE = "none"
    EXPLICIT_OWNER = "explicit-owner"
    SEPARATE_DESIGN_AUTHORITY = "separate-design-authority"
    OPERATOR_OWNED = "operator-owned"
    NOT_APPROVABLE = "not-approvable"


class MutationClass(str, Enum):
    READ_ONLY = "read-only"
    PROPOSAL_ONLY = "proposal-only"
    BOUNDED_EXECUTION = "bounded-execution"
    ESCALATION_ONLY = "escalation-only"
    FORBIDDEN = "forbidden"


class PolicyPhase(str, Enum):
    WAVE_C = "wave-c"
    FUTURE_SUCCESSOR = "future-successor"


class ProcessorOperation(str, Enum):
    READ_OBSERVATION = "READ_PROCESSOR_OBSERVATION"
    CLASSIFY = "CLASSIFY_PROCESSOR_OBSERVATION"
    EXPLAIN = "EXPLAIN_PROCESSOR_FINDINGS"
    NO_ACTION = "NO_PROCESSOR_ACTION"
    RETRY_OBSERVATION = "RETRY_PROCESSOR_OBSERVATION_LATER"
    PROPOSE_CATCH_UP = "PROPOSE_PROCESSOR_CATCH_UP"
    ESCALATE_STATE = "ESCALATE_PROCESSOR_STATE"
    INVOKE_WORKER_RUN = "INVOKE_PROCESSOR_WORKER_RUN"
    DIRECT_DATABASE_MUTATION = "DIRECT_PROCESSOR_DATABASE_MUTATION"
    DIRECT_SPOOL_MUTATION = "DIRECT_PROCESSOR_SPOOL_MUTATION"
    CHANGE_PROCESSOR_IDENTITY = "CHANGE_PROCESSOR_IDENTITY"
    RUN_MIGRATIONS = "RUN_PROCESSOR_MIGRATIONS"
    BYPASS_LOCK = "BYPASS_PROCESSOR_LOCK"
    GENERAL_SHELL = "GENERAL_PROCESSOR_SHELL"


class ReplicationOperation(str, Enum):
    READ_OBSERVATION = "READ_REPLICATION_OBSERVATION"
    CLASSIFY = "CLASSIFY_REPLICATION_OBSERVATION"
    EXPLAIN = "EXPLAIN_REPLICATION_FINDINGS"
    NO_ACTION = "NO_REPLICATION_ACTION"
    RETRY_OBSERVATION = "RETRY_REPLICATION_OBSERVATION_LATER"
    PROPOSE_VERIFY = "PROPOSE_TARGET_VERIFY"
    PROPOSE_RECONCILE = "PROPOSE_REPLICATION_RECONCILE"
    PROPOSE_RUN = "PROPOSE_REPLICATION_RUN"
    ESCALATE_STATE = "ESCALATE_REPLICATION_STATE"
    INVOKE_VERIFY = "INVOKE_REPLICATION_VERIFY"
    INVOKE_RECONCILE = "INVOKE_REPLICATION_RECONCILE"
    INVOKE_RUN = "INVOKE_REPLICATION_RUN"
    INITIALIZE_TARGET = "INITIALIZE_REPLICATION_TARGET"
    CHANGE_TARGET_IDENTITY = "CHANGE_REPLICATION_TARGET_IDENTITY"
    CHANGE_TARGET_ROOT = "CHANGE_REPLICATION_TARGET_ROOT"
    CHANGE_TARGET_MARKER_CONTENT = "CHANGE_REPLICATION_TARGET_MARKER_CONTENT"
    CHANGE_TARGET_MARKER_METADATA = "CHANGE_REPLICATION_TARGET_MARKER_METADATA"
    CHANGE_TARGET_NAMESPACE = "CHANGE_REPLICATION_TARGET_NAMESPACE"
    CHANGE_DATABASE_BINDING = "CHANGE_REPLICATION_DATABASE_BINDING"
    PROVISION_PROVIDER = "PROVISION_REPLICATION_PROVIDER"
    CHANGE_IAM_OR_CREDENTIALS = "CHANGE_REPLICATION_IAM_OR_CREDENTIALS"
    BYPASS_COLLISION = "BYPASS_REPLICATION_COLLISION"
    WEAKEN_IMMUTABILITY = "WEAKEN_REPLICATION_IMMUTABILITY"
    DIRECT_FINAL_OBJECT_MUTATION = "DIRECT_FINAL_OBJECT_MUTATION"
    DIRECT_PARTIAL_CLEANUP = "DIRECT_REPLICATION_PARTIAL_CLEANUP"
    DIRECT_DATABASE_MUTATION = "DIRECT_REPLICATION_DATABASE_MUTATION"
    GENERAL_PROVIDER_API = "GENERAL_REPLICATION_PROVIDER_API"
    DESTRUCTIVE_PROVIDER_ACTION = "DESTRUCTIVE_REPLICATION_PROVIDER_ACTION"
    GENERAL_SHELL = "GENERAL_REPLICATION_SHELL"


OperationCode: TypeAlias = ProcessorOperation | ReplicationOperation


class AllowedTool(str, Enum):
    PROCESSOR_RUN = "processor-worker.run"
    REPLICATION_RUN = "replication-worker.run"
    REPLICATION_RECONCILE = "replication-worker.reconcile"
    REPLICATION_VERIFY = "replication-worker.verify"


class ContractState(str, Enum):
    OBSERVED = "observed"
    CLASSIFIED = "classified"
    PROPOSED = "proposed"
    READ_ONLY_COMPLETE = "read-only-complete"
    AWAITING_APPROVAL = "awaiting-approval"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    EXECUTED = "executed"
    VERIFIED = "verified"
    ESCALATED = "escalated"
    RECEIPTED = "receipted"


def _sha(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ContractViolation(f"{field} must be a lowercase SHA-256")


def _optional_sha(value: str | None, field: str) -> None:
    if value is not None:
        _sha(value, field)


def _text(value: str, field: str) -> None:
    if not isinstance(value, str) or _SAFE_TEXT.fullmatch(value) is None:
        raise ContractViolation(f"{field} must be a bounded stable identifier")
    lowered = value.lower()
    if any(term in lowered for term in ("secret", "token", "password", "private_key", "/home/", "/users/")):
        raise ContractViolation(f"{field} contains forbidden private material")


def _count(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractViolation(f"{field} must be a non-negative integer")


def _enum(value: object, expected: type[Enum], field: str) -> None:
    if not isinstance(value, expected):
        raise ContractViolation(f"{field} must be {expected.__name__}")


def _sorted_codes(values: tuple[str, ...], field: str, *, required: bool = False) -> None:
    if not isinstance(values, tuple) or values != tuple(sorted(set(values))):
        raise ContractViolation(f"{field} must be unique and sorted")
    if required and not values:
        raise ContractViolation(f"{field} must not be empty")
    for item in values:
        _text(item, field)


def _operation_matches_service(service: Service, operation: OperationCode) -> bool:
    return (
        service is Service.PROCESSOR and isinstance(operation, ProcessorOperation)
    ) or (
        service is Service.REPLICATION and isinstance(operation, ReplicationOperation)
    )


def _require_operation(service: Service, operation: object, field: str = "operation_code") -> OperationCode:
    if not isinstance(operation, (ProcessorOperation, ReplicationOperation)):
        raise ContractViolation(f"{field} must be a frozen operation enum")
    if not _operation_matches_service(service, operation):
        raise ContractViolation(f"{field} does not belong to {service.value}")
    return operation


class _Digest:
    @property
    def digest_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True)
class AuthorityReference(_Digest):
    schema: ClassVar[str] = "bounded-agent-authority-reference.v1"
    service: Service
    canonical_repository: str
    canonical_commit: str
    authority_instance: str
    projection_schema: str
    contract_version: str
    database_schema_version: int
    observed_snapshot_sha256: str
    proof_class: str

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        _text(self.canonical_repository, "canonical_repository")
        if not isinstance(self.canonical_commit, str) or _GIT_SHA.fullmatch(self.canonical_commit) is None:
            raise ContractViolation("canonical_commit must be a 40-character Git SHA")
        for field in ("authority_instance", "projection_schema", "contract_version", "proof_class"):
            _text(getattr(self, field), field)
        if isinstance(self.database_schema_version, bool) or self.database_schema_version < 1:
            raise ContractViolation("database_schema_version must be positive")
        _sha(self.observed_snapshot_sha256, "observed_snapshot_sha256")


@dataclass(frozen=True)
class EvidenceReference(_Digest):
    schema: ClassVar[str] = "bounded-agent-evidence-reference.v1"
    finding_code: str
    evidence_code: str
    authority_reference_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        _text(self.finding_code, "finding_code")
        _text(self.evidence_code, "evidence_code")
        _sha(self.authority_reference_sha256, "authority_reference_sha256")
        _sha(self.evidence_sha256, "evidence_sha256")


def _validate_observation_evidence(
    authority: AuthorityReference,
    finding_codes: tuple[str, ...],
    evidence_references: tuple[EvidenceReference, ...],
) -> None:
    _sorted_codes(finding_codes, "finding_codes", required=True)
    if not isinstance(evidence_references, tuple) or not evidence_references:
        raise ContractViolation("evidence references must be non-empty")
    keys: list[tuple[str, str, str]] = []
    for reference in evidence_references:
        if not isinstance(reference, EvidenceReference):
            raise ContractViolation("evidence references must contain EvidenceReference values")
        if reference.authority_reference_sha256 != authority.digest_sha256:
            raise ContractViolation("evidence authority differs from observation authority")
        keys.append((reference.finding_code, reference.evidence_code, reference.evidence_sha256))
    if tuple(keys) != tuple(sorted(set(keys))):
        raise ContractViolation("evidence references must be unique and sorted")
    if {reference.finding_code for reference in evidence_references} != set(finding_codes):
        raise ContractViolation("evidence references must exactly cover observation findings")


@dataclass(frozen=True)
class ProcessorObservation(_Digest):
    schema: ClassVar[str] = "processor-observation.v2"
    authority: AuthorityReference
    semantic_input_sha256: str
    finding_codes: tuple[str, ...]
    evidence_references: tuple[EvidenceReference, ...]
    eligible_count: int
    missing_count: int
    prepared_count: int
    complete_count: int
    deferred_lock_count: int
    identity_conflict_count: int
    integrity_status: ProcessorIntegrityStatus

    def __post_init__(self) -> None:
        if not isinstance(self.authority, AuthorityReference):
            raise ContractViolation("authority must be AuthorityReference")
        if self.authority.service is not Service.PROCESSOR:
            raise ContractViolation("processor observation has wrong authority service")
        _sha(self.semantic_input_sha256, "semantic_input_sha256")
        _validate_observation_evidence(
            self.authority,
            self.finding_codes,
            self.evidence_references,
        )
        for field in ("eligible_count", "missing_count", "prepared_count", "complete_count", "deferred_lock_count", "identity_conflict_count"):
            _count(getattr(self, field), field)
        _enum(self.integrity_status, ProcessorIntegrityStatus, "integrity_status")


@dataclass(frozen=True)
class ReplicationObservation(_Digest):
    schema: ClassVar[str] = "replication-observation.v2"
    authority: AuthorityReference
    semantic_input_sha256: str
    finding_codes: tuple[str, ...]
    evidence_references: tuple[EvidenceReference, ...]
    target_id: str
    adapter_kind: str
    identity_status: ReplicationIdentityStatus
    provider_verification: ProviderVerification
    source_integrity_status: SourceIntegrityStatus
    eligible_objects: int
    registered_objects: int
    unregistered_objects: int
    pending_objects: int
    verified_objects: int
    failed_pending_objects: int

    def __post_init__(self) -> None:
        if not isinstance(self.authority, AuthorityReference):
            raise ContractViolation("authority must be AuthorityReference")
        if self.authority.service is not Service.REPLICATION:
            raise ContractViolation("replication observation has wrong authority service")
        _sha(self.semantic_input_sha256, "semantic_input_sha256")
        _validate_observation_evidence(
            self.authority,
            self.finding_codes,
            self.evidence_references,
        )
        _text(self.target_id, "target_id")
        _text(self.adapter_kind, "adapter_kind")
        _enum(self.identity_status, ReplicationIdentityStatus, "identity_status")
        _enum(self.provider_verification, ProviderVerification, "provider_verification")
        _enum(self.source_integrity_status, SourceIntegrityStatus, "source_integrity_status")
        for field in ("eligible_objects", "registered_objects", "unregistered_objects", "pending_objects", "verified_objects", "failed_pending_objects"):
            _count(getattr(self, field), field)


Observation: TypeAlias = ProcessorObservation | ReplicationObservation


@dataclass(frozen=True)
class Classification(_Digest):
    schema: ClassVar[str] = "bounded-agent-classification.v1"
    service: Service
    primary_code: str
    severity: Severity
    actionability: Actionability
    finding_codes: tuple[str, ...]
    observation_sha256: str
    classifier_version: str

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        _text(self.primary_code, "primary_code")
        _enum(self.severity, Severity, "severity")
        _enum(self.actionability, Actionability, "actionability")
        _sorted_codes(self.finding_codes, "finding_codes", required=True)
        _sha(self.observation_sha256, "observation_sha256")
        _text(self.classifier_version, "classifier_version")


@dataclass(frozen=True)
class ApprovalScope(_Digest):
    schema: ClassVar[str] = "bounded-agent-approval-scope.v1"
    service: Service
    authority_reference_sha256: str
    observation_sha256: str
    classification_sha256: str
    operation_code: OperationCode
    parameter_sha256: str
    policy_version: str
    execution_limit: int = 1

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        _require_operation(self.service, self.operation_code)
        for field in ("authority_reference_sha256", "observation_sha256", "classification_sha256", "parameter_sha256"):
            _sha(getattr(self, field), field)
        _text(self.policy_version, "policy_version")
        if self.execution_limit != 1:
            raise ContractViolation("approval scope must be single execution")


@dataclass(frozen=True)
class Proposal(_Digest):
    schema: ClassVar[str] = "bounded-agent-proposal.v2"
    service: Service
    authority_reference_sha256: str
    operation_code: OperationCode
    policy_phase: PolicyPhase
    disposition: PolicyDisposition
    mutation_class: MutationClass
    exact_parameters: tuple[tuple[str, object], ...]
    observation_sha256: str
    classification_sha256: str
    approval_requirement: ApprovalRequirement
    precondition_codes: tuple[str, ...]
    required_evidence_codes: tuple[str, ...]
    verification_requirement_codes: tuple[str, ...]
    policy_version: str
    expires_when_observation_changes: bool = True

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        _require_operation(self.service, self.operation_code)
        _enum(self.policy_phase, PolicyPhase, "policy_phase")
        _enum(self.disposition, PolicyDisposition, "disposition")
        _enum(self.mutation_class, MutationClass, "mutation_class")
        _enum(self.approval_requirement, ApprovalRequirement, "approval_requirement")
        _sha(self.authority_reference_sha256, "authority_reference_sha256")
        _sha(self.observation_sha256, "observation_sha256")
        _sha(self.classification_sha256, "classification_sha256")
        names = tuple(name for name, _ in self.exact_parameters)
        if names != tuple(sorted(set(names))):
            raise ContractViolation("exact parameter names must be unique and sorted")
        for name, value in self.exact_parameters:
            _text(name, "parameter_name")
            if any(term in name.lower() for term in ("secret", "token", "password", "private_key", "credential")):
                raise ContractViolation("secret-bearing parameters are forbidden")
            canonical_sha256(value)
        _sorted_codes(self.precondition_codes, "precondition_codes", required=True)
        _sorted_codes(self.required_evidence_codes, "required_evidence_codes", required=True)
        _sorted_codes(self.verification_requirement_codes, "verification_requirement_codes", required=True)
        _text(self.policy_version, "policy_version")
        if self.expires_when_observation_changes is not True:
            raise ContractViolation("proposals must expire when observations change")
        if self.disposition is PolicyDisposition.FORBID:
            raise ContractViolation("forbidden operations cannot become proposals")
        if self.mutation_class is MutationClass.FORBIDDEN:
            raise ContractViolation("forbidden mutation classes cannot become proposals")

    @property
    def parameter_sha256(self) -> str:
        return canonical_sha256(dict(self.exact_parameters))

    @property
    def approval_scope(self) -> ApprovalScope:
        return ApprovalScope(
            service=self.service,
            authority_reference_sha256=self.authority_reference_sha256,
            observation_sha256=self.observation_sha256,
            classification_sha256=self.classification_sha256,
            operation_code=self.operation_code,
            parameter_sha256=self.parameter_sha256,
            policy_version=self.policy_version,
        )


@dataclass(frozen=True)
class ApprovalDecision(_Digest):
    schema: ClassVar[str] = "bounded-agent-approval-decision.v2"
    service: Service
    outcome: ApprovalOutcome
    proposal_sha256: str
    authority_reference_sha256: str
    observation_sha256: str
    classification_sha256: str
    operation_code: OperationCode
    parameter_sha256: str
    approval_scope_sha256: str
    approver_reference: str
    policy_version: str

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        _enum(self.outcome, ApprovalOutcome, "outcome")
        _require_operation(self.service, self.operation_code)
        for field in (
            "proposal_sha256",
            "authority_reference_sha256",
            "observation_sha256",
            "classification_sha256",
            "parameter_sha256",
            "approval_scope_sha256",
        ):
            _sha(getattr(self, field), field)
        _text(self.approver_reference, "approver_reference")
        _text(self.policy_version, "policy_version")


@dataclass(frozen=True)
class ExecutionResult(_Digest):
    schema: ClassVar[str] = "bounded-agent-execution-result.v2"
    service: Service
    tool: AllowedTool
    status: ExecutionStatus
    authority_reference_sha256: str
    observation_sha256: str
    classification_sha256: str
    operation_code: OperationCode
    proposal_sha256: str
    exact_input_sha256: str
    approval_sha256: str | None
    exit_code: int
    result_code: str
    output_receipt_sha256: str

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        _enum(self.tool, AllowedTool, "tool")
        _enum(self.status, ExecutionStatus, "status")
        _require_operation(self.service, self.operation_code)
        expected_tools = {
            ProcessorOperation.INVOKE_WORKER_RUN: AllowedTool.PROCESSOR_RUN,
            ReplicationOperation.INVOKE_RUN: AllowedTool.REPLICATION_RUN,
            ReplicationOperation.INVOKE_RECONCILE: AllowedTool.REPLICATION_RECONCILE,
            ReplicationOperation.INVOKE_VERIFY: AllowedTool.REPLICATION_VERIFY,
        }
        expected_tool = expected_tools.get(self.operation_code)
        if expected_tool is None or self.tool is not expected_tool:
            raise ContractViolation("execution tool does not match bounded operation")
        for field in (
            "authority_reference_sha256",
            "observation_sha256",
            "classification_sha256",
            "proposal_sha256",
            "exact_input_sha256",
            "output_receipt_sha256",
        ):
            _sha(getattr(self, field), field)
        _optional_sha(self.approval_sha256, "approval_sha256")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ContractViolation("exit_code must be an integer")
        if self.status is ExecutionStatus.EXECUTED and self.exit_code != 0:
            raise ContractViolation("executed results require exit_code 0")
        if self.status is ExecutionStatus.FAILED and self.exit_code == 0:
            raise ContractViolation("failed results require non-zero exit_code")
        _text(self.result_code, "result_code")


@dataclass(frozen=True)
class VerificationResult(_Digest):
    schema: ClassVar[str] = "bounded-agent-verification-result.v2"
    service: Service
    authority_reference_sha256: str
    observation_sha256: str
    classification_sha256: str
    proposal_sha256: str
    execution_sha256: str
    observed_snapshot_sha256: str
    invariant_codes: tuple[str, ...]
    mismatch_codes: tuple[str, ...]
    status: VerificationStatus
    verifier_version: str

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        for field in (
            "authority_reference_sha256",
            "observation_sha256",
            "classification_sha256",
            "proposal_sha256",
            "execution_sha256",
            "observed_snapshot_sha256",
        ):
            _sha(getattr(self, field), field)
        _sorted_codes(self.invariant_codes, "invariant_codes")
        _sorted_codes(self.mismatch_codes, "mismatch_codes")
        _enum(self.status, VerificationStatus, "status")
        _text(self.verifier_version, "verifier_version")
        if self.mismatch_codes and self.status not in {
            VerificationStatus.FAIL,
            VerificationStatus.ESCALATE,
        }:
            raise ContractViolation("verification mismatches require FAIL or ESCALATE")
        if self.status is VerificationStatus.PASS:
            if self.mismatch_codes:
                raise ContractViolation("PASS verification cannot contain mismatches")
            if not self.invariant_codes:
                raise ContractViolation("PASS verification requires proven invariants")
        if self.status is VerificationStatus.FAIL and not self.mismatch_codes:
            raise ContractViolation("FAIL verification requires mismatch codes")
        if self.status is VerificationStatus.ESCALATE and not self.mismatch_codes:
            raise ContractViolation("ESCALATE verification requires mismatch codes")


@dataclass(frozen=True)
class StructuredReceipt(_Digest):
    schema: ClassVar[str] = "bounded-agent-receipt.v2"
    service: Service
    authority_reference_sha256: str
    observation_sha256: str
    classification_sha256: str
    proposal_sha256: str
    approval_sha256: str | None
    execution_sha256: str | None
    verification_sha256: str | None
    final_status: ReceiptStatus

    def __post_init__(self) -> None:
        _enum(self.service, Service, "service")
        for field in ("authority_reference_sha256", "observation_sha256", "classification_sha256", "proposal_sha256"):
            _sha(getattr(self, field), field)
        _optional_sha(self.approval_sha256, "approval_sha256")
        _optional_sha(self.execution_sha256, "execution_sha256")
        _optional_sha(self.verification_sha256, "verification_sha256")
        _enum(self.final_status, ReceiptStatus, "final_status")
        if self.final_status is ReceiptStatus.READ_ONLY_COMPLETE:
            if any(value is not None for value in (self.approval_sha256, self.execution_sha256, self.verification_sha256)):
                raise ContractViolation("read-only receipts cannot bind approval, execution, or verification")
        if self.final_status in {
            ReceiptStatus.EXECUTION_FAILED,
            ReceiptStatus.EXECUTED_UNVERIFIED,
            ReceiptStatus.ESCALATED,
            ReceiptStatus.VERIFIED,
            ReceiptStatus.VERIFICATION_FAILED,
        } and self.execution_sha256 is None:
            raise ContractViolation("execution receipt status requires execution binding")
        if self.final_status in {
            ReceiptStatus.ESCALATED,
            ReceiptStatus.VERIFIED,
            ReceiptStatus.VERIFICATION_FAILED,
        } and self.verification_sha256 is None:
            raise ContractViolation("verification receipt status requires verification binding")
        if self.final_status in {ReceiptStatus.DENIED, ReceiptStatus.EXPIRED}:
            if self.approval_sha256 is None:
                raise ContractViolation("approval outcome receipt requires approval binding")
            if self.execution_sha256 is not None or self.verification_sha256 is not None:
                raise ContractViolation("approval outcome receipt cannot bind execution or verification")
        if self.verification_sha256 is not None and self.execution_sha256 is None:
            raise ContractViolation("verification binding requires execution binding")


_TRANSITIONS = frozenset({
    (ContractState.OBSERVED, ContractState.CLASSIFIED),
    (ContractState.CLASSIFIED, ContractState.PROPOSED),
    (ContractState.PROPOSED, ContractState.READ_ONLY_COMPLETE),
    (ContractState.PROPOSED, ContractState.AWAITING_APPROVAL),
    (ContractState.READ_ONLY_COMPLETE, ContractState.RECEIPTED),
    (ContractState.AWAITING_APPROVAL, ContractState.APPROVED),
    (ContractState.AWAITING_APPROVAL, ContractState.DENIED),
    (ContractState.AWAITING_APPROVAL, ContractState.EXPIRED),
    (ContractState.APPROVED, ContractState.EXECUTED),
    (ContractState.DENIED, ContractState.RECEIPTED),
    (ContractState.EXPIRED, ContractState.RECEIPTED),
    (ContractState.EXECUTED, ContractState.VERIFIED),
    (ContractState.EXECUTED, ContractState.ESCALATED),
    (ContractState.VERIFIED, ContractState.RECEIPTED),
    (ContractState.ESCALATED, ContractState.RECEIPTED),
})


def allowed_transitions() -> frozenset[tuple[ContractState, ContractState]]:
    return _TRANSITIONS


def transition_state(current: ContractState, target: ContractState) -> ContractState:
    if not isinstance(current, ContractState) or not isinstance(target, ContractState):
        raise ContractViolation("contract transitions require ContractState values")
    if (current, target) not in _TRANSITIONS:
        raise ContractViolation(f"forbidden contract transition: {current.value} -> {target.value}")
    return target


def _approval_matches_proposal(
    approval: ApprovalDecision,
    proposal: Proposal,
    current_observation_sha256: str,
) -> bool:
    return (
        approval.service is proposal.service
        and approval.proposal_sha256 == proposal.digest_sha256
        and approval.authority_reference_sha256 == proposal.authority_reference_sha256
        and approval.observation_sha256 == proposal.observation_sha256 == current_observation_sha256
        and approval.classification_sha256 == proposal.classification_sha256
        and approval.operation_code is proposal.operation_code
        and approval.parameter_sha256 == proposal.parameter_sha256
        and approval.approval_scope_sha256 == proposal.approval_scope.digest_sha256
        and approval.policy_version == proposal.policy_version
    )


def approval_binds_proposal(
    approval: ApprovalDecision,
    proposal: Proposal,
    current_observation_sha256: str,
) -> bool:
    return (
        approval.outcome is ApprovalOutcome.APPROVE
        and _approval_matches_proposal(approval, proposal, current_observation_sha256)
    )


def derive_receipt_status(
    execution: ExecutionResult | None,
    verification: VerificationResult | None,
) -> ReceiptStatus:
    if execution is None:
        if verification is not None:
            raise ContractViolation("verification cannot exist without execution")
        return ReceiptStatus.READ_ONLY_COMPLETE
    if execution.status is ExecutionStatus.DENIED:
        return ReceiptStatus.DENIED
    if execution.status is ExecutionStatus.FAILED:
        return ReceiptStatus.EXECUTION_FAILED
    if execution.status is not ExecutionStatus.EXECUTED:
        return ReceiptStatus.NOT_RUN
    if verification is None or verification.status in {VerificationStatus.NOT_RUN, VerificationStatus.UNAVAILABLE}:
        return ReceiptStatus.EXECUTED_UNVERIFIED
    if verification.status is VerificationStatus.ESCALATE:
        return ReceiptStatus.ESCALATED
    if verification.status is VerificationStatus.FAIL or verification.mismatch_codes:
        return ReceiptStatus.VERIFICATION_FAILED
    if verification.status is not VerificationStatus.PASS:
        return ReceiptStatus.EXECUTED_UNVERIFIED
    return ReceiptStatus.VERIFIED


def _validate_classification_and_proposal(
    observation: Observation,
    classification: Classification,
    proposal: Proposal,
) -> None:
    if observation.authority.service is Service.PROCESSOR:
        from .processor import validate_processor_classification, validate_processor_proposal

        validate_processor_classification(observation, classification)
        validate_processor_proposal(observation, classification, proposal)
    else:
        from .replication import validate_replication_classification, validate_replication_proposal

        validate_replication_classification(observation, classification)
        validate_replication_proposal(observation, classification, proposal)


def build_structured_receipt(
    *,
    authority: AuthorityReference,
    observation: Observation,
    classification: Classification,
    proposal: Proposal,
    approval: ApprovalDecision | None = None,
    execution: ExecutionResult | None = None,
    verification: VerificationResult | None = None,
) -> StructuredReceipt:
    service = authority.service
    if observation.authority.digest_sha256 != authority.digest_sha256:
        raise ContractViolation("observation authority differs from supplied authority")
    if any(stage_service is not service for stage_service in (observation.authority.service, classification.service, proposal.service)):
        raise ContractViolation("authority, observation, classification, and proposal services differ")
    if proposal.authority_reference_sha256 != authority.digest_sha256:
        raise ContractViolation("proposal authority differs")
    if classification.observation_sha256 != observation.digest_sha256:
        raise ContractViolation("classification is not bound to observation")
    if proposal.observation_sha256 != observation.digest_sha256:
        raise ContractViolation("proposal is not bound to observation")
    if proposal.classification_sha256 != classification.digest_sha256:
        raise ContractViolation("proposal is not bound to classification")
    _validate_classification_and_proposal(observation, classification, proposal)

    if execution is None:
        if verification is not None:
            raise ContractViolation("verification cannot exist without execution")
        if approval is not None:
            if proposal.approval_requirement is ApprovalRequirement.NONE:
                raise ContractViolation("approval-free operation cannot carry approval")
            if proposal.mutation_class is not MutationClass.BOUNDED_EXECUTION:
                raise ContractViolation("read-only and proposal-only paths cannot include approval")
            if not _approval_matches_proposal(approval, proposal, observation.digest_sha256):
                raise ContractViolation("approval scope does not exactly match proposal")
            if approval.outcome is ApprovalOutcome.APPROVE:
                raise ContractViolation("approved proposal requires execution")
            final_status = (
                ReceiptStatus.DENIED
                if approval.outcome is ApprovalOutcome.DENY
                else ReceiptStatus.EXPIRED
            )
            return StructuredReceipt(
                service=service,
                authority_reference_sha256=authority.digest_sha256,
                observation_sha256=observation.digest_sha256,
                classification_sha256=classification.digest_sha256,
                proposal_sha256=proposal.digest_sha256,
                approval_sha256=approval.digest_sha256,
                execution_sha256=None,
                verification_sha256=None,
                final_status=final_status,
            )
        if proposal.mutation_class not in {
            MutationClass.READ_ONLY,
            MutationClass.PROPOSAL_ONLY,
            MutationClass.ESCALATION_ONLY,
        }:
            raise ContractViolation("bounded execution proposal requires execution or approval result")
        return StructuredReceipt(
            service=service,
            authority_reference_sha256=authority.digest_sha256,
            observation_sha256=observation.digest_sha256,
            classification_sha256=classification.digest_sha256,
            proposal_sha256=proposal.digest_sha256,
            approval_sha256=None,
            execution_sha256=None,
            verification_sha256=None,
            final_status=ReceiptStatus.READ_ONLY_COMPLETE,
        )

    if proposal.mutation_class is not MutationClass.BOUNDED_EXECUTION:
        raise ContractViolation("only bounded execution proposals may bind execution")
    if execution.service is not service:
        raise ContractViolation("execution service differs")
    if execution.authority_reference_sha256 != authority.digest_sha256:
        raise ContractViolation("execution authority differs")
    if execution.observation_sha256 != observation.digest_sha256:
        raise ContractViolation("execution observation differs")
    if execution.classification_sha256 != classification.digest_sha256:
        raise ContractViolation("execution classification differs")
    if execution.operation_code is not proposal.operation_code:
        raise ContractViolation("execution operation differs from proposal")
    if execution.proposal_sha256 != proposal.digest_sha256:
        raise ContractViolation("execution proposal binding differs")
    if execution.exact_input_sha256 != proposal.parameter_sha256:
        raise ContractViolation("execution input differs from exact proposal")

    if proposal.approval_requirement is ApprovalRequirement.NONE:
        if approval is not None or execution.approval_sha256 is not None:
            raise ContractViolation("approval-free operation cannot carry approval")
    elif proposal.approval_requirement in {
        ApprovalRequirement.EXPLICIT_OWNER,
        ApprovalRequirement.SEPARATE_DESIGN_AUTHORITY,
        ApprovalRequirement.OPERATOR_OWNED,
    }:
        if approval is None or not approval_binds_proposal(approval, proposal, observation.digest_sha256):
            raise ContractViolation("execution requires exact approval")
        if execution.approval_sha256 != approval.digest_sha256:
            raise ContractViolation("execution is not bound to approval")
    else:
        raise ContractViolation("operation is not approvable")

    if verification is not None:
        if verification.service is not service:
            raise ContractViolation("verification service differs")
        if verification.authority_reference_sha256 != authority.digest_sha256:
            raise ContractViolation("verification authority differs")
        if verification.observation_sha256 != observation.digest_sha256:
            raise ContractViolation("verification observation differs")
        if verification.classification_sha256 != classification.digest_sha256:
            raise ContractViolation("verification classification differs")
        if verification.proposal_sha256 != proposal.digest_sha256:
            raise ContractViolation("verification proposal differs")
        if verification.execution_sha256 != execution.digest_sha256:
            raise ContractViolation("verification is not bound to execution")
        if verification.status is VerificationStatus.PASS:
            required = set(proposal.verification_requirement_codes)
            proven = set(verification.invariant_codes)
            if not required.issubset(proven):
                raise ContractViolation("PASS verification lacks required verification invariants")

    return StructuredReceipt(
        service=service,
        authority_reference_sha256=authority.digest_sha256,
        observation_sha256=observation.digest_sha256,
        classification_sha256=classification.digest_sha256,
        proposal_sha256=proposal.digest_sha256,
        approval_sha256=None if approval is None else approval.digest_sha256,
        execution_sha256=execution.digest_sha256,
        verification_sha256=None if verification is None else verification.digest_sha256,
        final_status=derive_receipt_status(execution, verification),
    )
