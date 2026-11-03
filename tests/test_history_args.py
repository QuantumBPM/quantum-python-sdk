"""Checks that the history and process version methods forward each argument
to its matching generated parameter."""

import asyncio
from unittest.mock import MagicMock
from uuid import UUID

from quantumbpm import BpmnClient

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


def make_bpmn_client():
    c = BpmnClient(MagicMock(), PROJECT_ID)
    c._bpmn = MagicMock()
    return c


def test_get_instance_history_forwards_options():
    c = make_bpmn_client()

    asyncio.run(c.get_instance_history("inst-1", from_seq=4, before_seq=9, limit=50))
    asyncio.run(c.get_instance_history("inst-1", before_seq=9))

    calls = c._bpmn.get_bpmn_instance_history.call_args_list
    assert calls[0].args == (PROJECT_ID, "inst-1", 4, 9, 50)
    assert calls[1].args == (PROJECT_ID, "inst-1", None, 9, None)


def test_get_instance_history_entry_forwards_seq():
    c = make_bpmn_client()

    asyncio.run(c.get_instance_history_entry("inst-1", 7))

    assert c._bpmn.get_bpmn_instance_history_entry.call_args.args == (PROJECT_ID, "inst-1", 7)


def test_get_process_version_forwards_version_and_created_after():
    c = make_bpmn_client()

    asyncio.run(c.get_process_version("order_flow", 3, created_after="2026-01-01T00:00:00Z"))

    assert c._bpmn.get_bpmn_process_version.call_args.args == (
        PROJECT_ID,
        "order_flow",
        3,
        "2026-01-01T00:00:00Z",
    )
