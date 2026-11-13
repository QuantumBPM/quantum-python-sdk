"""BPMN runtime client. Async-first, scoped to a single project."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import UUID

from quantumbpm.api.bpmn_api import BpmnApi
from quantumbpm.api.default_api import DefaultApi
from quantumbpm.api_client import ApiClient
from quantumbpm.models.bpmn_activity_state import BpmnActivityState
from quantumbpm.models.bpmn_history_page import BpmnHistoryPage
from quantumbpm.models.bpmn_instance_paginated_response import BpmnInstancePaginatedResponse
from quantumbpm.models.bpmn_instance_state import BpmnInstanceState
from quantumbpm.models.bpmn_process_summary_paginated_response import (
    BpmnProcessSummaryPaginatedResponse,
)
from quantumbpm.models.bpmn_process_version import BpmnProcessVersion
from quantumbpm.models.bpmn_process_version_paginated_response import (
    BpmnProcessVersionPaginatedResponse,
)
from quantumbpm.models.bpmn_resource_detail import BpmnResourceDetail
from quantumbpm.models.bpmn_resource_paginated_response import BpmnResourcePaginatedResponse
from quantumbpm.models.bpmn_resource_summary_paginated_response import (
    BpmnResourceSummaryPaginatedResponse,
)
from quantumbpm.models.bpmn_user_task_paginated_response import BpmnUserTaskPaginatedResponse
from quantumbpm.models.bpmn_validate_response import BpmnValidateResponse
from quantumbpm.models.complete_bpmn_external_job_request import CompleteBpmnExternalJobRequest
from quantumbpm.models.correlation_keys import CorrelationKeys
from quantumbpm.models.create_bpmn_resource_request import CreateBpmnResourceRequest
from quantumbpm.models.get_bpmn_instance_variables200_response import (
    GetBpmnInstanceVariables200Response,
)
from quantumbpm.models.publish_bpmn_message_request import PublishBpmnMessageRequest
from quantumbpm.models.publish_bpmn_signal_request import PublishBpmnSignalRequest
from quantumbpm.models.start_bpmn_instance_request import StartBpmnInstanceRequest
from quantumbpm.models.start_bpmn_test_instance_request import StartBpmnTestInstanceRequest
from quantumbpm.models.throw_bpmn_user_task_error_request import (
    ThrowBpmnUserTaskErrorRequest,
)
from quantumbpm.models.update_bpmn_instance_variables_request import (
    UpdateBpmnInstanceVariablesRequest,
)
from quantumbpm.models.update_user_task_assignment_request import UpdateUserTaskAssignmentRequest
from quantumbpm.models.user_task import UserTask
from quantumbpm.models.validate_bpmn_resource_request import ValidateBpmnResourceRequest

from quantumbpm.variables import Vars


class BpmnClient:
    """
    Wraps the BPMN engine endpoints - resources, instances, messaging, user
    tasks, processes - for a single project. ``refresh`` is awaited before
    every API call to put a current bearer token on ``api_client``.
    """

    def __init__(
        self,
        api_client: ApiClient,
        project_id: str | UUID,
        *,
        refresh: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._bpmn = BpmnApi(api_client)
        self._default = DefaultApi(api_client)
        self._refresh = refresh
        self._project_id = UUID(project_id) if isinstance(project_id, str) else project_id

    # ---------------------------------------------------------------- helpers

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if self._refresh is not None:
            await self._refresh()
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ------------------------------------------------------------- resources

    async def create_resource(self, name: str, xml: str) -> BpmnResourceDetail:
        body = CreateBpmnResourceRequest(name=name, xml=xml)
        return await self._run(self._bpmn.create_bpmn_resource, self._project_id, body)

    async def update_resource(
        self,
        resource_id: str | UUID,
        name: str,
        xml: str,
    ) -> BpmnResourceDetail:
        body = CreateBpmnResourceRequest(name=name, xml=xml)
        rid = UUID(resource_id) if isinstance(resource_id, str) else resource_id
        return await self._run(self._bpmn.update_bpmn_resource, self._project_id, rid, body)

    async def delete_resource(self, resource_id: str | UUID) -> None:
        rid = UUID(resource_id) if isinstance(resource_id, str) else resource_id
        await self._run(self._bpmn.delete_bpmn_resource, self._project_id, rid)

    async def get_resource(self, resource_id: str | UUID) -> BpmnResourceDetail:
        rid = UUID(resource_id) if isinstance(resource_id, str) else resource_id
        return await self._run(self._bpmn.get_bpmn_resource, self._project_id, rid)

    async def deploy_resource(self, resource_id: str | UUID) -> None:
        rid = UUID(resource_id) if isinstance(resource_id, str) else resource_id
        await self._run(self._bpmn.deploy_bpmn_resource, self._project_id, rid)

    async def start_test_instance(
        self,
        resource_id: str | UUID,
        vars: Vars,
        *,
        process_id: str | None = None,
        business_id: str | None = None,
    ) -> str:
        rid = UUID(resource_id) if isinstance(resource_id, str) else resource_id
        body = StartBpmnTestInstanceRequest(
            processID=process_id,
            variables=vars.to_wire_map(),
            businessId=business_id,
        )
        resp = await self._run(self._bpmn.start_bpmn_test_instance, self._project_id, rid, body)
        if not resp or not resp.instance_id:
            raise RuntimeError("bpmn: start_test_instance returned no instanceID")
        return resp.instance_id

    async def validate_xml(self, xml: str) -> BpmnValidateResponse:
        body = ValidateBpmnResourceRequest(xml=xml)
        return await self._run(self._bpmn.validate_bpmn_xml, self._project_id, body)

    async def list_resources(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> BpmnResourcePaginatedResponse:
        return await self._run(
            self._bpmn.list_bpmn_resources, self._project_id, page, page_size
        )

    async def list_latest_resources(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> BpmnResourceSummaryPaginatedResponse:
        return await self._run(
            self._bpmn.list_latest_bpmn_resources, self._project_id, page, page_size
        )

    async def list_resource_versions(
        self,
        definitions_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> BpmnResourcePaginatedResponse:
        return await self._run(
            self._bpmn.list_bpmn_resources_by_definitions_id,
            self._project_id,
            definitions_id,
            page,
            page_size,
        )

    # ------------------------------------------------------------- instances

    async def start_instance(
        self,
        process_id: str,
        vars: Vars,
        *,
        version: int | None = None,
        business_id: str | None = None,
    ) -> str:
        """Start a deployed process, its latest version unless `version` names one.

        Returns the instance ID.
        """
        body = StartBpmnInstanceRequest(
            processID=process_id,
            version=version,
            variables=vars.to_wire_map(),
            businessId=business_id,
        )
        resp = await self._run(self._default.start_bpmn_instance, self._project_id, body)
        if not resp or not resp.instance_id:
            raise RuntimeError("bpmn: start_instance returned no instanceID")
        return resp.instance_id

    async def get_instance(self, instance_id: str) -> BpmnInstanceState:
        return await self._run(self._default.get_bpmn_instance, self._project_id, instance_id)

    async def cancel_instance(self, instance_id: str) -> None:
        await self._run(self._default.cancel_bpmn_instance, self._project_id, instance_id)

    async def list_instances(
        self,
        *,
        status: str | None = None,
        has_incident: bool | None = None,
        suspended: bool | None = None,
        created_after: datetime | None = None,
        business_id: str | None = None,
        process_id: str | None = None,
        version: int | None = None,
        draft: bool | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> BpmnInstancePaginatedResponse:
        """List a page of instances, newest first. `version` narrows a
        `process_id` filter to one version, and `draft` keeps only test runs
        (True) or leaves them out (False). Pass the page's
        `pagination.next_cursor` as `cursor` for the next one."""
        return await self._run(
            self._default.list_bpmn_instances,
            self._project_id,
            status=status,
            has_incident=has_incident,
            suspended=suspended,
            created_after=created_after,
            business_id=business_id,
            process_id=process_id,
            version=version,
            draft=draft,
            cursor=cursor,
            limit=limit,
        )

    async def get_instance_children(
        self,
        instance_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> BpmnInstancePaginatedResponse:
        """List a page of the instances this instance spawned via CallActivity,
        newest first. Pass the page's `pagination.next_cursor` as `cursor` for
        the next one."""
        return await self._run(
            self._bpmn.get_bpmn_instance_children,
            self._project_id,
            instance_id,
            cursor=cursor,
            limit=limit,
        )

    async def get_instance_history(
        self,
        instance_id: str,
        *,
        from_seq: int | None = None,
        before_seq: int | None = None,
        limit: int | None = None,
    ) -> BpmnHistoryPage:
        """
        One page of the instance's history, oldest entry first. ``from_seq``
        reads from that seq on, ``before_seq`` reads the newest entries before
        it, and neither reads the newest entries.
        """
        return await self._run(
            self._bpmn.get_bpmn_instance_history,
            self._project_id,
            instance_id,
            from_seq,
            before_seq,
            limit,
        )

    async def get_instance_history_entry(self, instance_id: str, seq: int) -> BpmnActivityState:
        """One history entry by its seq, with its inputs and outputs."""
        return await self._run(
            self._bpmn.get_bpmn_instance_history_entry, self._project_id, instance_id, seq
        )

    async def get_instance_variables(self, instance_id: str) -> Vars:
        resp: GetBpmnInstanceVariables200Response = await self._run(
            self._bpmn.get_bpmn_instance_variables, self._project_id, instance_id
        )
        return Vars.from_wire_map(resp.variables if resp else None)

    async def update_instance_variables(self, instance_id: str, vars: Vars) -> None:
        body = UpdateBpmnInstanceVariablesRequest(variables=vars.to_wire_map() or {})
        await self._run(
            self._bpmn.update_bpmn_instance_variables, self._project_id, instance_id, body
        )

    async def resolve_incident(
        self,
        instance_id: str,
        incident_id: str,
        vars: Vars | None = None,
    ) -> None:
        body = GetBpmnInstanceVariables200Response(
            variables=(vars.to_wire_map() if vars else None)
        )
        await self._run(
            self._bpmn.resolve_bpmn_incident,
            self._project_id,
            instance_id,
            incident_id,
            body,
        )

    # ------------------------------------------------------------- messaging

    async def publish_message(
        self,
        name: str,
        vars: Vars | None = None,
        *,
        correlation_keys: CorrelationKeys | dict | None = None,
        ttl: str | None = None,
    ) -> None:
        ck: CorrelationKeys | None
        if correlation_keys is None:
            ck = None
        elif isinstance(correlation_keys, CorrelationKeys):
            ck = correlation_keys
        else:
            ck = CorrelationKeys.model_validate(correlation_keys)
        body = PublishBpmnMessageRequest(
            messageName=name,
            correlationKeys=ck,
            ttl=ttl,
            variables=(vars.to_wire_map() if vars else None),
        )
        await self._run(self._bpmn.publish_bpmn_message, self._project_id, body)

    async def publish_signal(
        self,
        name: str,
        vars: Vars | None = None,
        *,
        ttl: str | None = None,
    ) -> None:
        body = PublishBpmnSignalRequest(
            signalName=name,
            ttl=ttl,
            variables=(vars.to_wire_map() if vars else None),
        )
        await self._run(self._bpmn.publish_bpmn_signal, self._project_id, body)

    # ----------------------------------------------------------- user tasks

    async def list_user_tasks(
        self,
        *,
        instance_id: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        candidate_user: str | None = None,
        candidate_group: str | None = None,
        business_id: str | None = None,
        due_after: datetime | None = None,
        due_before: datetime | None = None,
        sort: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> BpmnUserTaskPaginatedResponse:
        """List a page of user tasks. Pass the page's `pagination.next_cursor`
        as `cursor` for the next one.

        `due_after` is included and `due_before` excluded. `sort` is
        "created" (the default), "dueDate" or "priority", and a cursor
        continues only a list of the same sort."""
        return await self._run(
            self._bpmn.list_bpmn_user_tasks,
            self._project_id,
            instance_id=instance_id,
            status=status,
            assignee=assignee,
            candidate_user=candidate_user,
            candidate_group=candidate_group,
            business_id=business_id,
            due_after=due_after,
            due_before=due_before,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )

    async def list_user_tasks_for_caller(
        self,
        *,
        due_after: datetime | None = None,
        due_before: datetime | None = None,
        sort: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> BpmnUserTaskPaginatedResponse:
        """List a page of the user tasks the caller may claim or has claimed,
        with the due range and sort of `list_user_tasks`."""
        return await self._run(
            self._bpmn.list_bpmn_user_tasks_for_caller,
            self._project_id,
            due_after=due_after,
            due_before=due_before,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )

    async def get_user_task(self, execution_key: str) -> UserTask:
        return await self._run(self._bpmn.get_bpmn_user_task, self._project_id, execution_key)

    async def update_user_task_assignment(
        self,
        execution_key: str,
        body: UpdateUserTaskAssignmentRequest,
    ) -> UserTask:
        return await self._run(
            self._bpmn.update_bpmn_user_task_assignment,
            self._project_id,
            execution_key,
            body,
        )

    async def complete_user_task(
        self,
        execution_key: str,
        vars: Vars | None = None,
    ) -> None:
        body = GetBpmnInstanceVariables200Response(
            variables=(vars.to_wire_map() if vars else None)
        )
        await self._run(
            self._bpmn.complete_bpmn_user_task, self._project_id, execution_key, body
        )

    async def throw_user_task_error(
        self,
        execution_key: str,
        error_code: str,
        vars: Vars | None = None,
    ) -> None:
        body = ThrowBpmnUserTaskErrorRequest(
            errorCode=error_code,
            variables=(vars.to_wire_map() if vars else None),
        )
        await self._run(
            self._bpmn.throw_bpmn_user_task_error, self._project_id, execution_key, body
        )

    # ------------------------------------------------------------- processes

    async def list_processes(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        created_after: str | None = None,
    ) -> BpmnProcessSummaryPaginatedResponse:
        return await self._run(
            self._bpmn.list_bpmn_processes,
            self._project_id,
            page,
            page_size,
            search,
            created_after,
        )

    async def list_process_versions(
        self,
        process_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        created_after: str | None = None,
    ) -> BpmnProcessVersionPaginatedResponse:
        return await self._run(
            self._bpmn.list_bpmn_process_versions,
            self._project_id,
            process_id,
            page,
            page_size,
            created_after,
        )

    async def get_process_version(
        self,
        process_id: str,
        version: int,
        *,
        created_after: str | None = None,
    ) -> BpmnProcessVersion:
        return await self._run(
            self._bpmn.get_bpmn_process_version,
            self._project_id,
            process_id,
            version,
            created_after,
        )
