"""
Data Quality Controls and Governance System
Implements comprehensive data quality checks, audit trails, and governance per Festival Bloomberg spec
"""
import logging
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, date, timedelta
from enum import Enum
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class QualityLevel(Enum):
    """Data quality levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class QualityStatus(Enum):
    """Data quality status"""
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class IssueCategory(Enum):
    """Data quality issue categories"""
    COMPLETENESS = "completeness"
    ACCURACY = "accuracy"
    CONSISTENCY = "consistency"
    VALIDITY = "validity"
    TIMELINESS = "timeliness"
    UNIQUENESS = "uniqueness"
    INTEGRITY = "integrity"
    FORMAT = "format"


@dataclass
class QualityCheck:
    """Data quality check definition"""
    check_id: str
    name: str
    description: str
    category: IssueCategory
    level: QualityLevel
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityIssue:
    """Data quality issue"""
    issue_id: str
    check_id: str
    category: IssueCategory
    level: QualityLevel
    entity_type: str
    entity_id: Optional[str]
    field_name: Optional[str]
    description: str
    severity: str
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityReport:
    """Data quality report"""
    report_id: str
    entity_type: str
    entity_id: Optional[str]
    run_id: str
    started_at: datetime
    finished_at: datetime
    total_checks: int
    passed_checks: int
    failed_checks: int
    warning_checks: int
    skipped_checks: int
    issues: List[QualityIssue]
    overall_score: float  # 0-100
    status: QualityStatus
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseQualityCheck(ABC):
    """Base class for data quality checks"""
    
    def __init__(self, check: QualityCheck):
        self.check = check
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
    
    @abstractmethod
    def execute(self, data: Any, context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """Execute the quality check"""
        pass
    
    def _create_issue(self, 
                     entity_id: Optional[str],
                     field_name: Optional[str],
                     description: str) -> QualityIssue:
        """Create a quality issue"""
        return QualityIssue(
            issue_id=f"{self.check.check_id}_{datetime.utcnow().timestamp()}",
            check_id=self.check.check_id,
            category=self.check.category,
            level=self.check.level,
            entity_type="unknown",
            entity_id=entity_id,
            field_name=field_name,
            description=description,
            severity=self.check.level.value,
            detected_at=datetime.utcnow(),
            metadata=self.check.metadata
        )


class CompletenessCheck(BaseQualityCheck):
    """Check for missing/null values"""
    
    def execute(self, data: pd.DataFrame, context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """Check for null values in specified columns"""
        issues = []
        
        required_fields = context.get('required_fields', []) if context else []
        
        for field in required_fields:
            if field in data.columns:
                null_count = data[field].isnull().sum()
                if null_count > 0:
                    null_percentage = (null_count / len(data)) * 100
                    
                    # Create issues for null values
                    null_rows = data[data[field].isnull()]
                    for idx, row in null_rows.iterrows():
                        entity_id = row.get('id') if 'id' in row else str(idx)
                        issue = self._create_issue(
                            entity_id=entity_id,
                            field_name=field,
                            description=f"Missing required field: {field}"
                        )
                        issue.metadata['null_percentage'] = null_percentage
                        issues.append(issue)
        
        self.logger.info(f"Completeness check found {len(issues)} issues")
        return issues


class AccuracyCheck(BaseQualityCheck):
    """Check for data accuracy using validation rules"""
    
    def execute(self, data: pd.DataFrame, context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """Check data accuracy against validation rules"""
        issues = []
        
        validation_rules = context.get('validation_rules', {}) if context else {}
        
        for field, rule in validation_rules.items():
            if field in data.columns:
                # Check numeric ranges
                if 'min' in rule:
                    invalid = data[data[field] < rule['min']]
                    for idx, row in invalid.iterrows():
                        entity_id = row.get('id') if 'id' in row else str(idx)
                        issue = self._create_issue(
                            entity_id=entity_id,
                            field_name=field,
                            description=f"Value {row[field]} below minimum {rule['min']}"
                        )
                        issues.append(issue)
                
                if 'max' in rule:
                    invalid = data[data[field] > rule['max']]
                    for idx, row in invalid.iterrows():
                        entity_id = row.get('id') if 'id' in row else str(idx)
                        issue = self._create_issue(
                            entity_id=entity_id,
                            field_name=field,
                            description=f"Value {row[field]} above maximum {rule['max']}"
                        )
                        issues.append(issue)
        
        self.logger.info(f"Accuracy check found {len(issues)} issues")
        return issues


class ConsistencyCheck(BaseQualityCheck):
    """Check for data consistency across fields"""
    
    def execute(self, data: pd.DataFrame, context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """Check consistency rules"""
        issues = []
        
        consistency_rules = context.get('consistency_rules', []) if context else []
        
        for rule in consistency_rules:
            if rule['type'] == 'date_range':
                start_field = rule['start_field']
                end_field = rule['end_field']
                
                if start_field in data.columns and end_field in data.columns:
                    invalid = data[data[start_field] > data[end_field]]
                    for idx, row in invalid.iterrows():
                        entity_id = row.get('id') if 'id' in row else str(idx)
                        issue = self._create_issue(
                            entity_id=entity_id,
                            field_name=f"{start_field},{end_field}",
                            description=f"Start date after end date"
                        )
                        issues.append(issue)
            
            elif rule['type'] == 'referential_integrity':
                foreign_key = rule['foreign_key']
                reference_table = rule['reference_table']
                
                # In production, this would check against the reference table
                # For now, just log that we would check
                self.logger.debug(f"Would check referential integrity for {foreign_key} -> {reference_table}")
        
        self.logger.info(f"Consistency check found {len(issues)} issues")
        return issues


class UniquenessCheck(BaseQualityCheck):
    """Check for duplicate records"""
    
    def execute(self, data: pd.DataFrame, context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """Check for duplicates based on key fields"""
        issues = []
        
        key_fields = context.get('key_fields', ['id']) if context else ['id']
        
        # Check if key fields exist
        if all(field in data.columns for field in key_fields):
            # Find duplicates
            duplicates = data[data.duplicated(subset=key_fields, keep=False)]
            
            for idx, row in duplicates.iterrows():
                entity_id = row.get('id') if 'id' in row else str(idx)
                issue = self._create_issue(
                    entity_id=entity_id,
                    field_name=','.join(key_fields),
                    description=f"Duplicate record based on key fields"
                )
                issues.append(issue)
        
        self.logger.info(f"Uniqueness check found {len(issues)} issues")
        return issues


class TimelinessCheck(BaseQualityCheck):
    """Check for data timeliness"""
    
    def execute(self, data: pd.DataFrame, context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """Check if data is within acceptable time window"""
        issues = []
        
        date_field = context.get('date_field', 'created_at') if context else 'created_at'
        max_age_days = context.get('max_age_days', 30) if context else 30
        
        if date_field in data.columns:
            cutoff_date = datetime.utcnow() - timedelta(days=max_age_days)
            stale = data[pd.to_datetime(data[date_field]) < cutoff_date]
            
            for idx, row in stale.iterrows():
                entity_id = row.get('id') if 'id' in row else str(idx)
                issue = self._create_issue(
                    entity_id=entity_id,
                    field_name=date_field,
                    description=f"Data is older than {max_age_days} days"
                )
                issues.append(issue)
        
        self.logger.info(f"Timeliness check found {len(issues)} issues")
        return issues


class FormatCheck(BaseQualityCheck):
    """Check for format compliance"""
    
    def execute(self, data: pd.DataFrame, context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """Check field formats"""
        issues = []
        
        format_rules = context.get('format_rules', {}) if context else {}
        
        for field, rule in format_rules.items():
            if field in data.columns:
                if rule['type'] == 'email':
                    import re
                    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
                    invalid = data[~data[field].str.match(email_pattern, na=False)]
                    
                    for idx, row in invalid.iterrows():
                        entity_id = row.get('id') if 'id' in row else str(idx)
                        issue = self._create_issue(
                            entity_id=entity_id,
                            field_name=field,
                            description=f"Invalid email format"
                        )
                        issues.append(issue)
                
                elif rule['type'] == 'url':
                    import re
                    url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'
                    invalid = data[~data[field].str.match(url_pattern, na=False)]
                    
                    for idx, row in invalid.iterrows():
                        entity_id = row.get('id') if 'id' in row else str(idx)
                        issue = self._create_issue(
                            entity_id=entity_id,
                            field_name=field,
                            description=f"Invalid URL format"
                        )
                        issues.append(issue)
        
        self.logger.info(f"Format check found {len(issues)} issues")
        return issues


class DataQualityEngine:
    """
    Main data quality engine
    Implements Festival Bloomberg data quality requirements
    """
    
    def __init__(self):
        self._checks: Dict[str, BaseQualityCheck] = {}
        self._quality_reports: List[QualityReport] = []
        self._issue_registry: Dict[str, QualityIssue] = {}
        
        # Register default checks
        self._register_default_checks()
        
        logger.info("Data quality engine initialized")
    
    def _register_default_checks(self):
        """Register default quality checks"""
        default_checks = [
            QualityCheck("completeness", "Completeness Check", "Check for missing required fields", IssueCategory.COMPLETENESS, QualityLevel.HIGH),
            QualityCheck("accuracy", "Accuracy Check", "Check data accuracy against validation rules", IssueCategory.ACCURACY, QualityLevel.HIGH),
            QualityCheck("consistency", "Consistency Check", "Check data consistency across fields", IssueCategory.CONSISTENCY, QualityLevel.MEDIUM),
            QualityCheck("uniqueness", "Uniqueness Check", "Check for duplicate records", IssueCategory.UNIQUENESS, QualityLevel.HIGH),
            QualityCheck("timeliness", "Timeliness Check", "Check data freshness", IssueCategory.TIMELINESS, QualityLevel.MEDIUM),
            QualityCheck("format", "Format Check", "Check field format compliance", IssueCategory.FORMAT, QualityLevel.LOW)
        ]
        
        for check in default_checks:
            self.register_check(check)
    
    def register_check(self, check: QualityCheck, check_class: type = None):
        """
        Register a quality check
        
        Args:
            check: Quality check definition
            check_class: Check class (uses default mapping if not provided)
        """
        check_classes = {
            IssueCategory.COMPLETENESS: CompletenessCheck,
            IssueCategory.ACCURACY: AccuracyCheck,
            IssueCategory.CONSISTENCY: ConsistencyCheck,
            IssueCategory.UNIQUENESS: UniquenessCheck,
            IssueCategory.TIMELINESS: TimelinessCheck,
            IssueCategory.FORMAT: FormatCheck
        }
        
        if check_class is None:
            check_class = check_classes.get(check.category, BaseQualityCheck)
        
        self._checks[check.check_id] = check_class(check)
        logger.info(f"Registered quality check: {check.check_id}")
    
    def get_check(self, check_id: str) -> Optional[BaseQualityCheck]:
        """Get a registered check"""
        return self._checks.get(check_id)
    
    def list_checks(self, enabled_only: bool = True) -> List[QualityCheck]:
        """List registered checks"""
        checks = list(self._checks.values())
        if enabled_only:
            checks = [c for c in checks if c.check.enabled]
        return [c.check for c in checks]
    
    def run_quality_check(self,
                        check_id: str,
                        data: pd.DataFrame,
                        context: Optional[Dict[str, Any]] = None) -> List[QualityIssue]:
        """
        Run a single quality check
        
        Args:
            check_id: Check identifier
            data: Data to check
            context: Optional context for the check
            
        Returns:
            List of quality issues
        """
        check = self.get_check(check_id)
        if not check:
            logger.error(f"Check not found: {check_id}")
            return []
        
        if not check.check.enabled:
            logger.info(f"Check disabled: {check_id}")
            return []
        
        issues = check.execute(data, context)
        
        # Register issues
        for issue in issues:
            self._issue_registry[issue.issue_id] = issue
        
        return issues
    
    def run_quality_suite(self,
                        data: pd.DataFrame,
                        entity_type: str,
                        entity_id: Optional[str] = None,
                        context: Optional[Dict[str, Any]] = None) -> QualityReport:
        """
        Run all enabled quality checks
        
        Args:
            data: Data to check
            entity_type: Type of entity being checked
            entity_id: Optional entity identifier
            context: Optional context for checks
            
        Returns:
            QualityReport
        """
        run_id = f"qa_{entity_type}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        started_at = datetime.utcnow()
        
        logger.info(f"Starting quality suite {run_id} for {entity_type}")
        
        all_issues = []
        passed_checks = 0
        failed_checks = 0
        warning_checks = 0
        skipped_checks = 0
        
        for check in self.list_checks(enabled_only=True):
            issues = self.run_quality_check(check.check_id, data, context)
            all_issues.extend(issues)
            
            if issues:
                if check.level == QualityLevel.HIGH or check.level == QualityLevel.CRITICAL:
                    failed_checks += 1
                else:
                    warning_checks += 1
            else:
                passed_checks += 1
        
        finished_at = datetime.utcnow()
        total_checks = passed_checks + failed_checks + warning_checks + skipped_checks
        
        # Calculate overall score
        if total_checks > 0:
            overall_score = (passed_checks / total_checks) * 100
        else:
            overall_score = 0.0
        
        # Determine status
        if failed_checks > 0:
            status = QualityStatus.FAILED
        elif warning_checks > 0:
            status = QualityStatus.WARNING
        else:
            status = QualityStatus.PASSED
        
        report = QualityReport(
            report_id=f"{run_id}_report",
            entity_type=entity_type,
            entity_id=entity_id,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            total_checks=total_checks,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            warning_checks=warning_checks,
            skipped_checks=skipped_checks,
            issues=all_issues,
            overall_score=overall_score,
            status=status,
            metadata={
                'check_count': total_checks,
                'data_rows': len(data)
            }
        )
        
        self._quality_reports.append(report)
        
        logger.info(f"Quality suite {run_id} complete: {status.value}, score={overall_score:.1f}")
        return report
    
    def resolve_issue(self, issue_id: str, resolution_notes: str):
        """
        Mark a quality issue as resolved
        
        Args:
            issue_id: Issue identifier
            resolution_notes: Notes about the resolution
        """
        if issue_id in self._issue_registry:
            issue = self._issue_registry[issue_id]
            issue.resolved_at = datetime.utcnow()
            issue.resolution_notes = resolution_notes
            logger.info(f"Resolved issue: {issue_id}")
    
    def get_issue_registry(self, 
                         category: Optional[IssueCategory] = None,
                         resolved: Optional[bool] = None) -> List[QualityIssue]:
        """
        Get issues from registry with optional filters
        
        Args:
            category: Filter by category
            resolved: Filter by resolution status
            
        Returns:
            List of quality issues
        """
        issues = list(self._issue_registry.values())
        
        if category:
            issues = [i for i in issues if i.category == category]
        
        if resolved is not None:
            if resolved:
                issues = [i for i in issues if i.resolved_at is not None]
            else:
                issues = [i for i in issues if i.resolved_at is None]
        
        return issues
    
    def get_quality_summary(self) -> Dict[str, Any]:
        """Get summary of quality reports"""
        if not self._quality_reports:
            return {}
        
        total_reports = len(self._quality_reports)
        avg_score = np.mean([r.overall_score for r in self._quality_reports])
        passed = sum(1 for r in self._quality_reports if r.status == QualityStatus.PASSED)
        failed = sum(1 for r in self._quality_reports if r.status == QualityStatus.FAILED)
        warnings = sum(1 for r in self._quality_reports if r.status == QualityStatus.WARNING)
        
        return {
            'total_reports': total_reports,
            'average_score': avg_score,
            'passed': passed,
            'failed': failed,
            'warnings': warnings,
            'total_issues': len(self._issue_registry),
            'unresolved_issues': sum(1 for i in self._issue_registry.values() if i.resolved_at is None)
        }
    
    def clear_registry(self):
        """Clear issue registry and reports"""
        self._issue_registry.clear()
        self._quality_reports.clear()
        logger.info("Quality registry cleared")


class AuditTrail:
    """
    Audit trail for data operations
    Implements Festival Bloomberg audit requirements
    """
    
    def __init__(self):
        self._audit_log: List[Dict[str, Any]] = []
        logger.info("Audit trail initialized")
    
    def log_operation(self,
                     operation: str,
                     entity_type: str,
                     entity_id: Optional[str],
                     user: str,
                     details: Optional[Dict[str, Any]] = None):
        """
        Log a data operation
        
        Args:
            operation: Operation type (create, update, delete, etc.)
            entity_type: Type of entity
            entity_id: Entity identifier
            user: User performing the operation
            details: Optional operation details
        """
        log_entry = {
            'operation': operation,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'user': user,
            'timestamp': datetime.utcnow().isoformat(),
            'details': details or {}
        }
        
        self._audit_log.append(log_entry)
        logger.info(f"Logged operation: {operation} on {entity_type}:{entity_id} by {user}")
    
    def get_audit_log(self,
                     entity_type: Optional[str] = None,
                     entity_id: Optional[str] = None,
                     operation: Optional[str] = None,
                     start_date: Optional[datetime] = None,
                     end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Get audit log with optional filters
        
        Args:
            entity_type: Filter by entity type
            entity_id: Filter by entity ID
            operation: Filter by operation type
            start_date: Filter by start date
            end_date: Filter by end date
            
        Returns:
            Filtered audit log
        """
        log = self._audit_log.copy()
        
        if entity_type:
            log = [e for e in log if e['entity_type'] == entity_type]
        
        if entity_id:
            log = [e for e in log if e['entity_id'] == entity_id]
        
        if operation:
            log = [e for e in log if e['operation'] == operation]
        
        if start_date:
            start_ts = start_date.isoformat()
            log = [e for e in log if e['timestamp'] >= start_ts]
        
        if end_date:
            end_ts = end_date.isoformat()
            log = [e for e in log if e['timestamp'] <= end_ts]
        
        return log
    
    def get_operation_count(self, 
                          operation: Optional[str] = None,
                          entity_type: Optional[str] = None) -> int:
        """Get count of operations"""
        log = self.get_audit_log(operation=operation, entity_type=entity_type)
        return len(log)


def create_data_quality_engine() -> DataQualityEngine:
    """Factory function to create data quality engine"""
    return DataQualityEngine()


def create_audit_trail() -> AuditTrail:
    """Factory function to create audit trail"""
    return AuditTrail()
