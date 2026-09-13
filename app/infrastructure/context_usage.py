"""上下文评测的调用级计量，不采集提示词正文。"""
from contextvars import ContextVar
from opentelemetry import trace

context_call_kind = ContextVar('context_call_kind', default='business')
context_usage_sink = ContextVar('context_usage_sink', default=None)


def record_context_usage(input_tokens, output_tokens, elapsed_ms):
    sample = {'kind':context_call_kind.get(), 'input_tokens':input_tokens, 'output_tokens':output_tokens, 'elapsed_ms':elapsed_ms}
    sink=context_usage_sink.get()
    if sink is not None:sink(sample)
    # Langfuse/OTel 只接收数值；未知值明确记录为未知。
    span=trace.get_current_span()
    span.set_attribute('globex.context.call_kind',sample['kind'])
    span.set_attribute('globex.context.usage_known',input_tokens is not None)
    if input_tokens is not None:span.set_attribute('globex.context.input_tokens',input_tokens)
    if output_tokens is not None:span.set_attribute('globex.context.output_tokens',output_tokens)
