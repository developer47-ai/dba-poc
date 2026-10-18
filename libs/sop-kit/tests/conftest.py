from pathlib import Path

import pytest

LIBRARY = Path(__file__).resolve().parents[3] / "knowledge"

VALID = """---
doc_id: SOP-TST-001
title: Test procedure
version: 1.0
kind: procedure
agent_scope: [inhome]
carrier_tech_types: [GPON]
symptoms: [no internet]
entry_step: S1
---

## Purpose

Fix things.

## Procedure

Intro text before the first step.

### S1 — Check the session
- actor: agent
- tool: network-diagnostics.get_ppp_session
- next: { up: S3, down: S2 }
Read the PPP status.
Outcome `up` when the status is up.

### S2 — Reboot
- actor: agent
- tool: network-diagnostics.reboot_device
- requires_consent: true
- next: { done: S3 }
Reboot after approval.

### S3 — Done
- actor: expert
- end: resolved
Tell the customer.
"""


@pytest.fixture
def library_dir() -> Path:
    return LIBRARY


@pytest.fixture
def valid_text() -> str:
    return VALID
