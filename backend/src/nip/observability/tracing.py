"""OpenTelemetry tracing. LLM, tool and graph spans go to any OTLP backend (Arize Phoenix locally)."""

from __future__ import annotations

import structlog

from nip.config import get_settings

log = structlog.get_logger("nip.tracing")
_configured = False


def configure_tracing() -> None:
    global _configured
    s = get_settings()
    if _configured or not s.otel_endpoint:
        return
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # openinference.project.name groups the traces into a named project in Phoenix
    provider = TracerProvider(resource=Resource.create({"service.name": s.service_name,
                                                        "openinference.project.name": s.service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=s.otel_endpoint)))
    trace.set_tracer_provider(provider)
    # Hide prompt/response text from traces outside dev (health data); keep metadata and timings.
    from openinference.instrumentation import TraceConfig

    hide = s.env == "prod"
    LangChainInstrumentor().instrument(tracer_provider=provider, skip_dep_check=True,
                                       config=TraceConfig(hide_inputs=hide, hide_outputs=hide))
    _configured = True
    log.info("tracing.enabled", endpoint=s.otel_endpoint)
