"""Start the same NeMo CLI with an OTLP/gRPC SDK and HTTP trace-context propagation."""
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.trace import Status
from opentelemetry.sdk.trace.export import BatchSpanProcessor


# NeMo can attach custom action parameters even when prompt capture is disabled.
# Select diagnostic fields before sending a span to either a local or shared collector.
METADATA = frozenset({
    "http.method", "http.route", "http.status_code", "http.request.method", "http.response.status_code",
    "rail.name", "rail.type", "rail.stop", "action.name", "action.has_llm_calls", "action.llm_calls_count",
    "gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.request.model", "gen_ai.response.model",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.total_tokens", "llm.cache.hit",
})


class MetadataExporter(OTLPSpanExporter):
    def export(self, spans):
        metadata = [
            ReadableSpan(s.name, context=s.context, parent=s.parent, resource=s.resource, kind=s.kind,
                         attributes={k: v for k, v in (s.attributes or {}).items() if k in METADATA},
                         status=Status(s.status.status_code), start_time=s.start_time, end_time=s.end_time,
                         instrumentation_scope=s.instrumentation_scope)
            for s in spans
        ]
        return super().export(metadata)


def main():
    provider = TracerProvider()
    exporter = OTLPSpanExporter() if os.getenv("GRLAB_OTEL_CAPTURE_CONTENT") == "true" else MetadataExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    # Instrument before NeMo imports FastAPI and defines GuardrailsApp.
    FastAPIInstrumentor().instrument(tracer_provider=provider, excluded_urls=r"/v1/rails/configs|/health|/metrics",
                                     exclude_spans=["receive", "send"])
    from nemoguardrails.cli import app
    app()


if __name__ == "__main__":
    main()
