"""Agent plugin: agent runs, threads, gateway, routing and channels API."""

from __future__ import annotations


def apply(ctx) -> None:
    from ..api.agent import create_agent_router
    from ..api.channels import create_channels_router
    from ..services.agent_gateway_runtime import AgentGatewayWorker, AgentRunService
    from ..services.agent_route_service import AgentRouteService
    from ..services.agent_service import (
        AgentService,
        Fts5RetrieverBackend,
        SqlAgentEvidenceRetriever,
        SqlKnowledgeScopeResolver,
    )
    from ..services.agent_thread_service import AgentThreadService
    from ..services.openclaw_status_service import OpenClawStatusService

    connection_factory = ctx.get("db.connection_factory")
    model_profile_service = ctx.get("models.service")
    gchat_service = ctx.get("gchat.service")
    call_ledger = ctx.get("db.call_ledger")
    database_initialized = ctx.get("db.initialized")

    agent_thread_service = AgentThreadService(connection_factory)
    agent_service = AgentService(
        connection_factory=connection_factory,
        thread_service=agent_thread_service,
        model_profile_service=model_profile_service,
        scope_resolver=SqlKnowledgeScopeResolver(connection_factory),
        retriever=Fts5RetrieverBackend(
            SqlAgentEvidenceRetriever(connection_factory)
        ),
        provider_factory=ctx.override("agent.provider_factory"),
        gchat_service=gchat_service,
        call_ledger=call_ledger,
    )
    agent_runs = AgentRunService(connection_factory)
    agent_route_service = AgentRouteService(connection_factory)
    if database_initialized:
        agent_thread_service.recover_interrupted_runs()
        agent_runs.recover_interrupted()

    def execute_agent_run(run_id: str, request: object):
        payload = request if isinstance(request, dict) else {}
        options = (
            payload.get("options")
            if isinstance(payload.get("options"), dict)
            else {}
        )
        return agent_service.stream(
            thread_id=payload.get("threadId"),
            question=payload.get("question", ""),
            model_profile_id=payload.get("modelProfileId"),
            scope=payload.get("scope", {"mode": "global"}),
            project_id=payload.get("projectId"),
            remote_send_confirmation=(
                payload.get("remoteSendConfirmation", {}).get("fingerprint")
                if isinstance(payload.get("remoteSendConfirmation"), dict)
                else None
            ),
            reasoning_mode=options.get("reasoningMode", "auto"),
            include_thread_history=options.get("includeThreadHistory", True),
            generate_insights=options.get("generateInsights", False),
            execution_run_id=run_id,
        )

    agent_gateway_worker = AgentGatewayWorker(
        agent_runs,
        execute_agent_run,
    )
    ctx.state["gateway"] = agent_gateway_worker
    ctx.provide("agent.gateway", agent_gateway_worker)

    ctx.add_router(
        "/api",
        create_channels_router(OpenClawStatusService()),
    )
    ctx.add_router(
        "/api",
        create_agent_router(
            agent_service,
            agent_thread_service,
            runs=agent_runs,
            call_ledger=call_ledger,
            run_created=agent_gateway_worker.notify,
            routes=agent_route_service,
        ),
    )


def start(ctx) -> None:
    if not ctx.get("db.initialized"):
        return
    gateway = ctx.state.get("gateway")
    if gateway is not None:
        gateway.start()
        gateway.notify()


def stop(ctx) -> None:
    gateway = ctx.state.get("gateway")
    if gateway is not None:
        gateway.stop()
