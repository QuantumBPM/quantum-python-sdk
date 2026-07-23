"""BPMN runtime client. Async-first, scoped to a single project."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

from quantumbpm.api.bpmn_api import BpmnApi
from quantumbpm.api.default_api import DefaultApi
from quantumbpm.api_client import ApiClient
from quantumbpm.models.bpmn_instance_children_response import BpmnInstanceChildrenResponse
from quantumbpm.models.bpmn_instance_paginated_response import BpmnInstancePaginatedResponse
from quantumbpm.models.bpmn_instance_state import BpmnInstanceState
from quantumbpm.models.bpmn_process_summary_paginated_response import (
    BpmnProcessSummaryPaginatedResponse,
)
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
    Wraps the BPMN engine endpoints — resources, instances, messaging, user
    tasks, processes — for a single project.
    """

    def __init__(self, api_client: ApiClient, project_id: str | UUID) -> None:
        self._bpmn = BpmnApi(api_client)
        self._default = DefaultApi(api_client)
        self._project_id = UUID(project_id) if isinstance(project_id, str) else project_id

    # ---------------------------------------------------------------- helpers

    @property
    def project_id(self) -> UUID:
        return self._project_id

    @staticmethod
    async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
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
        if not resp or not resp.workflow_id:
            raise RuntimeError("bpmn: start_test_instance returned no workflowID")
        return resp.workflow_id

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
        process_definition_id: str | UUID,
        vars: Vars,
        *,
        business_id: str | None = None,
    ) -> str:
        pid = (
            UUID(process_definition_id)
            if isinstance(process_definition_id, str)
            else process_definition_id
        )
        body = StartBpmnInstanceRequest(
            processDefinitionID=pid,
            variables=vars.to_wire_map(),
            businessId=business_id,
        )
        resp = await self._run(self._default.start_bpmn_instance, self._project_id, body)
        if not resp or not resp.workflow_id:
            raise RuntimeError("bpmn: start_instance returned no workflowID")
        return resp.workflow_id

    async def get_instance(self, workflow_id: str) -> BpmnInstanceState:
        return await self._run(self._default.get_bpmn_instance, self._project_id, workflow_id)

    async def cancel_instance(self, workflow_id: str) -> None:
        await self._run(self._default.cancel_bpmn_instance, self._project_id, workflow_id)

    async def list_instances(
        self,
        *,
        definition_id: str | UUID | None = None,
        status: str | None = None,
        has_incident: bool | None = None,
        suspended: bool | None = None,
        created_after: datetime | None = None,
        business_id: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> BpmnInstancePaginatedResponse:
        did = UUID(definition_id) if isinstance(definition_id, str) else definition_id
        return await self._run(
            self._default.list_bpmn_instances,
            self._project_id,
            did,
            status,
            has_incident,
            suspended,
            created_after,
            business_id,
            page,
            page_size,
        )

    async def get_instance_children(self, workflow_id: str) -> BpmnInstanceChildrenResponse:
        return await self._run(
            self._bpmn.get_bpmn_instance_children, self._project_id, workflow_id
        )

    async def get_instance_variables(self, workflow_id: str) -> Vars:
        resp: GetBpmnInstanceVariables200Response = await self._run(
            self._bpmn.get_bpmn_instance_variables, self._project_id, workflow_id
        )
        return Vars.from_wire_map(resp.variables if resp else None)

    async def update_instance_variables(self, workflow_id: str, vars: Vars) -> None:
        body = UpdateBpmnInstanceVariablesRequest(variables=vars.to_wire_map() or {})
        await self._run(
            self._bpmn.update_bpmn_instance_variables, self._project_id, workflow_id, body
        )

    async def resolve_incident(
        self,
        workflow_id: str,
        incident_id: str,
        vars: Vars | None = None,
    ) -> None:
        body = GetBpmnInstanceVariables200Response(
            variables=(vars.to_wire_map() if vars else None)
        )
        await self._run(
            self._bpmn.resolve_bpmn_incident,
            self._project_id,
            workflow_id,
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
        workflow_id: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        candidate_user: str | None = None,
        candidate_group: str | None = None,
        business_id: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> BpmnUserTaskPaginatedResponse:
        return await self._run(
            self._bpmn.list_bpmn_user_tasks,
            self._project_id,
            workflow_id,
            status,
            assignee,
            candidate_user,
            candidate_group,
            business_id,
            page,
            page_size,
        )

    async def list_user_tasks_for_caller(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> BpmnUserTaskPaginatedResponse:
        return await self._run(
            self._bpmn.list_bpmn_user_tasks_for_caller, self._project_id, page, page_size
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
