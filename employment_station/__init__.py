"""驻点就业服务站：面谈解析、撮合、签约把关与接运交接。

本包只使用 Python 3.11 标准库。所有结构化字段都保留来源记录编号与原话
片段，结构化信息仅用于核对，原话不被覆盖。
"""

from .models import (
    Ambiguity,
    CaseFacts,
    Confirmation,
    ConsentLevel,
    Contract,
    ContractStatus,
    EffectCondition,
    HandoverPacket,
    Interview,
    Job,
    LegChange,
    LegStatus,
    PickupSheet,
    Reconfirmation,
    ReconfirmStatus,
    Seat,
    SheetStatus,
    Statement,
    StatementType,
    TransportLeg,
)
from .parser import parse_corpus
from .engine import CaseBook, build_casebook

__all__ = [
    "Ambiguity",
    "CaseBook",
    "CaseFacts",
    "Confirmation",
    "ConsentLevel",
    "Contract",
    "ContractStatus",
    "EffectCondition",
    "HandoverPacket",
    "Interview",
    "Job",
    "LegChange",
    "LegStatus",
    "PickupSheet",
    "Reconfirmation",
    "ReconfirmStatus",
    "Seat",
    "SheetStatus",
    "Statement",
    "StatementType",
    "TransportLeg",
    "parse_corpus",
    "build_casebook",
]
