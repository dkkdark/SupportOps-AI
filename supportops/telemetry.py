import logging
import time
from contextlib import contextmanager
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

logger = logging.getLogger("supportops")


def configure(settings):
    resource = Resource.create({"service.name": settings.otel_service_name, "service.version": settings.code_version})
    provider = TracerProvider(resource=resource)
    readers = []
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        base = settings.otel_exporter_otlp_endpoint.rstrip("/")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=base+"/v1/traces")))
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=base+"/v1/metrics")))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))


@contextmanager
def step(state, name):
    start = time.perf_counter()
    with trace.get_tracer("supportops.workflow").start_as_current_span(name) as span:
        span.set_attribute("ticket.id", state["ticket_id"])
        span.set_attribute("workflow.trace_id", state["trace_id"])
        try:
            yield
        finally:
            duration = round((time.perf_counter()-start)*1000, 2)
            state.setdefault("steps", []).append({"name":name, "duration_ms":duration, "status":"error" if state.get("reason") else "ok"})
            metrics.get_meter("supportops.workflow").create_histogram("workflow.step.duration", unit="ms").record(duration, {"step":name})
            logger.info('workflow_step ticket_id=%s trace_id=%s step=%s duration_ms=%s', state["ticket_id"],state["trace_id"],name,duration)
