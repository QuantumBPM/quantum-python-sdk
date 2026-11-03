"""End-to-end check that businessId is forwarded through every public method
that supports it (set on start/evaluate; filter on list)."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

from quantumbpm import BpmnClient, DmnClient, Vars

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


def make_bpmn_client():
    api_client = MagicMock()
    c = BpmnClient(api_client, PROJECT_ID)
    # Replace inner SDK handles with mocks we can inspect.
    c._default = MagicMock()
    c._bpmn = MagicMock()
    return c


def make_dmn_client():
    api_client = MagicMock()
    c = DmnClient(api_client, PROJECT_ID)
    c._api = MagicMock()
    return c


def test_start_instance_sends_business_id():
    c = make_bpmn_client()
    resp = MagicMock()
    resp.instance_id = "wf-1"
    c._default.start_bpmn_instance.return_value = resp

    asyncio.run(
        c.start_instance(
            "order",
            Vars(),
            business_id="ORDER-42",
        )
    )

    body = c._default.start_bpmn_instance.call_args.args[1]
    assert body.business_id == "ORDER-42"


def test_start_instance_omits_business_id_when_not_passed():
    c = make_bpmn_client()
    resp = MagicMock()
    resp.instance_id = "wf-1"
    c._default.start_bpmn_instance.return_value = resp

    asyncio.run(c.start_instance("order", Vars()))

    body = c._default.start_bpmn_instance.call_args.args[1]
    assert body.business_id is None


def test_start_test_instance_sends_business_id():
    c = make_bpmn_client()
    resp = MagicMock()
    resp.instance_id = "wf-test-1"
    c._bpmn.start_bpmn_test_instance.return_value = resp

    asyncio.run(
        c.start_test_instance(
            "22222222-2222-2222-2222-222222222222",
            Vars(),
            business_id="TEST-1",
        )
    )

    body = c._bpmn.start_bpmn_test_instance.call_args.args[2]
    assert body.business_id == "TEST-1"


def test_list_instances_passes_business_id_filter():
    c = make_bpmn_client()
    c._default.list_bpmn_instances.return_value = MagicMock()

    asyncio.run(c.list_instances(business_id="ORDER-42", process_id="order_flow", draft=False, cursor="next-page", limit=3))

    kwargs = c._default.list_bpmn_instances.call_args.kwargs
    assert kwargs["business_id"] == "ORDER-42"
    assert kwargs["process_id"] == "order_flow"
    assert kwargs["draft"] is False
    assert kwargs["cursor"] == "next-page"
    assert kwargs["limit"] == 3


def test_list_user_tasks_passes_business_id_filter():
    c = make_bpmn_client()
    c._bpmn.list_bpmn_user_tasks.return_value = MagicMock()

    asyncio.run(c.list_user_tasks(business_id="ORDER-42", cursor="next-page", limit=3))

    kwargs = c._bpmn.list_bpmn_user_tasks.call_args.kwargs
    assert kwargs["business_id"] == "ORDER-42"
    assert kwargs["cursor"] == "next-page"
    assert kwargs["limit"] == 3


def test_list_user_tasks_for_caller_passes_cursor():
    c = make_bpmn_client()
    c._bpmn.list_bpmn_user_tasks_for_caller.return_value = MagicMock()

    asyncio.run(c.list_user_tasks_for_caller(cursor="next-page", limit=3))

    kwargs = c._bpmn.list_bpmn_user_tasks_for_caller.call_args.kwargs
    assert kwargs["cursor"] == "next-page"
    assert kwargs["limit"] == 3


def test_list_user_tasks_pass_due_range_and_sort():
    c = make_bpmn_client()
    c._bpmn.list_bpmn_user_tasks.return_value = MagicMock()
    c._bpmn.list_bpmn_user_tasks_for_caller.return_value = MagicMock()
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)

    asyncio.run(c.list_user_tasks(due_before=now, sort="dueDate"))
    asyncio.run(c.list_user_tasks_for_caller(due_after=now, sort="priority"))

    kwargs = c._bpmn.list_bpmn_user_tasks.call_args.kwargs
    assert (kwargs["due_after"], kwargs["due_before"], kwargs["sort"]) == (None, now, "dueDate")
    kwargs = c._bpmn.list_bpmn_user_tasks_for_caller.call_args.kwargs
    assert (kwargs["due_after"], kwargs["due_before"], kwargs["sort"]) == (now, None, "priority")


def test_evaluate_sends_business_id():
    c = make_dmn_client()
    c._api.evaluate_by_definitions_id.return_value = {}

    asyncio.run(c.evaluate("def-1", Vars(), business_id="ORDER-42"))

    body = c._api.evaluate_by_definitions_id.call_args.args[2]
    assert body.business_id == "ORDER-42"
