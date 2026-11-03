"""Intent-based guardrails — fast rule-based tier, LLM fallback for ambiguous cases."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Policy configuration
# ---------------------------------------------------------------------------

_POLICY_TEXT: str = os.getenv(
    "GUARDRAIL_POLICY",
    "ROLE_USER:can only query and access data from your assigned seller ID|cannot request data from other sellers|cannot access data outside your seller partition,ROLE_ADMIN:no restrictions",
)

# LLM guard timeout in seconds (0 = disabled, use rule-based only)
_GUARD_LLM_TIMEOUT: int = int(os.getenv("GUARD_LLM_TIMEOUT", "0"))


@dataclass
class PolicyRule:
    role: str
    constraints: list[str]


def _parse_policy() -> dict[str, PolicyRule]:
    rules: dict[str, PolicyRule] = {}
    for role_spec in _POLICY_TEXT.split(","):
        role_spec = role_spec.strip()
        if not role_spec or ":" not in role_spec:
            continue
        role, constraints_text = role_spec.split(":", 1)
        role = role.strip()
        constraints = [c.strip() for c in constraints_text.split("|") if c.strip()]
        rules[role] = PolicyRule(role=role, constraints=constraints)
    return rules


_POLICY_RULES: dict[str, PolicyRule] = _parse_policy()


def get_policy_text(role: str) -> str:
    rule = _POLICY_RULES.get(role)
    if not rule:
        return "No specific policy defined for this role."
    if rule.constraints == ["no restrictions"]:
        return "No restrictions."
    return " ".join(rule.constraints)


# ---------------------------------------------------------------------------
# Tier 1: Fast rule-based classification (runs in microseconds)
# ---------------------------------------------------------------------------

# Patterns that always indicate a cross-seller / unauthorized access attempt.
# Written to avoid false-positives on innocent queries.
_DENY_PATTERNS: list[re.Pattern] = [
    # explicit seller ID references: "seller 42", "seller_14", "seller14", sellerID 7
    re.compile(r"\bseller[_\s]?\d+\b", re.IGNORECASE),
    re.compile(r"\bseller[\s_]?id[:\s=]+\d+\b", re.IGNORECASE),
    # cross-seller or other-seller phrasing
    re.compile(r"\b(other\s+seller|all\s+sellers?|every\s+seller|each\s+seller)\b", re.IGNORECASE),
    re.compile(r"\bacross\s+(all\s+)?sellers?\b", re.IGNORECASE),
    re.compile(r"\bsellers?\s+data\b", re.IGNORECASE),
    re.compile(r"\bcompare\s+.*\bsellers?\b", re.IGNORECASE),
    re.compile(r"\bother\s+users?\b", re.IGNORECASE),
]


def _rule_based_check(question: str, role: str) -> tuple[bool | None, str]:
    """
    Fast rule-based pre-check.

    Returns:
        (True, reason)   → definitely ALLOWED, skip LLM
        (False, reason)  → definitely DENIED, skip LLM
        (None, "")       → ambiguous, fall through to LLM
    """
    from elasticsearch_agent.auth import ROLE_ADMIN

    if role == ROLE_ADMIN:
        return (True, "Admin role has no restrictions.")

    q = question.lower()

    for pattern in _DENY_PATTERNS:
        match = pattern.search(q)
        if match:
            return (
                False,
                f"You can only access your own seller data. "
                f"Requests referencing other sellers ('{match.group(0)}') are not permitted.",
            )

    # If query only references first-person possessives or "my", it's clearly own-data
    if re.search(r"\b(my|mine|I have|I've)\b", question, re.IGNORECASE):
        return (True, "Request targets own data — complies with policy.")

    # Ambiguous — let LLM decide (if enabled)
    return (None, "")


# ---------------------------------------------------------------------------
# Tier 2: LLM-as-judge (only called for ambiguous queries, with timeout)
# ---------------------------------------------------------------------------

def _llm_check(question: str, role: str, llm_classifier) -> tuple[bool, str]:
    """
    LLM classification for ambiguous queries.
    Runs with a thread-based timeout to avoid blocking the UI.
    """
    import concurrent.futures

    policy = get_policy_text(role)

    prompt = (
        f"You are a strict data access policy enforcer.\n\n"
        f"User Role: {role}\n"
        f"Policy: {policy}\n"
        f"User Request: \"{question}\"\n\n"
        f"Does this request violate the policy?\n"
        f"Respond with exactly one line:\n"
        f"  ALLOWED  — if request is within the user's authorized scope\n"
        f"  DENIED: <reason>  — if request tries to access data outside authorized scope\n\n"
        f"One line only. No preamble."
    )

    timeout = _GUARD_LLM_TIMEOUT if _GUARD_LLM_TIMEOUT > 0 else 30

    def _call():
        response = llm_classifier.invoke(prompt)
        return response.content.strip() if hasattr(response, "content") else str(response).strip()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_call)
            text = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        # Timed out — fail open so the user isn't blocked
        return (True, "Guard LLM timed out; allowing request to proceed.")
    except Exception:
        return (True, "Guard LLM error; allowing request to proceed.")

    line = text.split("\n")[0].strip()
    if line.upper().startswith("DENIED"):
        reason = line[6:].lstrip(":").strip()
        return (False, reason or "Request violates policy.")
    return (True, "Request complies with policy.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_intent(question: str, role: str, llm_classifier=None) -> tuple[bool, str]:
    """
    Classify whether a user's question violates their role's data access policy.

    Strategy (A then C):
      A — fast rule-based check (microseconds, no LLM call)
      C — LLM-as-judge, only for queries that pass the rule filter as ambiguous

    Returns (is_allowed: bool, reasoning: str).
    """
    # Tier 1: rules
    verdict, reason = _rule_based_check(question, role)

    if verdict is not None:
        # Definitive answer from rules — no LLM needed
        return (verdict, reason)

    # Tier 2: LLM fallback for ambiguous queries
    if llm_classifier is not None and _GUARD_LLM_TIMEOUT >= 0:
        return _llm_check(question, role, llm_classifier)

    # No LLM available — default allow (fail-open)
    return (True, "Ambiguous query allowed (LLM guard disabled).")
