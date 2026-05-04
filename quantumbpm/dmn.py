"""DMN evaluation client. Async-first, scoped to a single project."""

from __future__ import annotations

import asyncio
from typing import Sequence
from uuid import UUID

from quantumbpm.api.default_api import DefaultApi
from quantumbpm.api_client import ApiClient
from quantumbpm.models.batch_evaluate_design_request import BatchEvaluateDesignRequest
from quantumbpm.models.batch_evaluation_response import BatchEvaluationResponse
from quantumbpm.models.evaluate_design_request import EvaluateDesignRequest
from quantumbpm.models.evaluate_stored_request import EvaluateStoredRequest
from quantumbpm.models.evaluation_result import EvaluationResult

from quantumbpm.variables import Vars

DmnResult = dict[str, EvaluationResult]


class DmnClient:
    """Evaluates DMN definitions in a single project."""

    def __init__(self, api_client: ApiClient, project_id: str | UUID) -> None:
        self._api = DefaultApi(api_client)
        self._project_id = UUID(project_id) if isinstance(project_id, str) else project_id

    async def evaluate(
        self,
        definitions_id: str,
        vars: Vars,
        *,
        version: int | None = None,
        decisions: Sequence[str] | None = None,
        decision_services: Sequence[str] | None = None,
    ) -> DmnResult:
        """
        Run a stored DMN definition identified by its DMN XML
        ``<definitions id="…">`` value. Stable across versions, addressable
        from the BPMN model.
        """
        body = EvaluateStoredRequest(
            context=vars.to_feel_context(),
            version=version,
            decisions=list(decisions) if decisions is not None else None,
            decisionServices=list(decision_services) if decision_services is not None else None,
        )
        return await asyncio.to_thread(
            self._api.evaluate_by_definitions_id,
            self._project_id,
            definitions_id,
            body,
            version,
        )

    async def evaluate_by_id(
        self,
        definition_id: str | UUID,
        vars: Vars,
        *,
        version: int | None = None,
        decisions: Sequence[str] | None = None,
        decision_services: Sequence[str] | None = None,
    ) -> DmnResult:
        """
        Run a stored DMN definition addressed by its platform UUID. Prefer
        :meth:`evaluate` (by definitions id) for normal use.
        """
        body = EvaluateStoredRequest(
            context=vars.to_feel_context(),
            version=version,
            decisions=list(decisions) if decisions is not None else None,
            decisionServices=list(decision_services) if decision_services is not None else None,
        )
        defid = UUID(definition_id) if isinstance(definition_id, str) else definition_id
        return await asyncio.to_thread(
            self._api.evaluate_stored,
            self._project_id,
            defid,
            body,
        )

    async def evaluate_design(
        self,
        xml: str,
        vars: Vars,
        *,
        decisions: Sequence[str] | None = None,
        decision_services: Sequence[str] | None = None,
        additional_xmls: Sequence[str] | None = None,
    ) -> DmnResult:
        """
        Run ad-hoc DMN XML against an input context. The XML is not stored;
        useful for "evaluate while editing" flows.
        """
        body = EvaluateDesignRequest(
            xml=xml,
            context=vars.to_feel_context(),
            decisions=list(decisions) if decisions is not None else None,
            decisionServices=list(decision_services) if decision_services is not None else None,
            additionalXMLs=list(additional_xmls) if additional_xmls is not None else None,
        )
        return await asyncio.to_thread(self._api.evaluate_design, body)

    async def evaluate_design_batch(
        self,
        xml: str,
        rows: Sequence[Vars],
    ) -> BatchEvaluationResponse:
        """Evaluate the same XML against many input rows in one request."""
        body = BatchEvaluateDesignRequest(
            xml=xml,
            inputs=[r.to_dict() for r in rows],
        )
        return await asyncio.to_thread(self._api.evaluate_design_batch, body)
