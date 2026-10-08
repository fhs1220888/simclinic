"""Reduce failed CLI diagnostics to fixed categories; never persist raw text."""
import json
import re


def failure_diagnostic(stdout, stderr):
    messages, event_types, codes, statuses = [], [], [], []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get('type') not in ('error', 'turn.failed'):
            continue
        event_types.append(event['type'])
        error = event.get('error')
        payload = error if isinstance(error, dict) else event
        message = payload.get('message')
        if isinstance(message, str):
            messages.append(message)
        code = payload.get('code')
        if isinstance(code, str):
            codes.append(code.lower())
        status = payload.get('status', payload.get('status_code'))
        if type(status) is int and status in (400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504):
            statuses.append(status)
    text = '\n'.join(messages + [stderr]).lower()
    if not statuses:
        statuses = [int(s) for s in re.findall(r'http(?: error| status)?\s*[:=]?\s*(400|401|403|404|408|409|422|429|500|502|503|504)\b', text)]
    status = statuses[-1] if statuses else None
    if status == 429 or any(c in ('rate_limit_exceeded', 'usage_limit_reached', 'insufficient_quota') for c in codes) or \
            any(s in text for s in ('rate limit', 'usage limit', 'too many requests', 'quota exceeded')):
        category = 'model_rate_limit'
    elif status in (401, 403) or any(c in ('invalid_api_key', 'authentication_error') for c in codes) or \
            any(s in text for s in ('unauthorized', 'authentication failed', 'not logged in', 'token expired')):
        category = 'model_auth_error'
    elif any(c in ('model_not_found', 'unsupported_model') for c in codes) or \
            any(s in text for s in ('model not found', 'model is not supported', 'unsupported model')):
        category = 'model_unavailable'
    elif status in (400, 422) or any(s in text for s in ('invalid schema', 'invalid request', 'unrecognized argument')):
        category = 'model_request_error'
    elif status in (500, 502, 503, 504):
        category = 'model_service_error'
    elif status == 408 or any(s in text for s in ('stream disconnected', 'connection reset', 'connection refused',
                                               'connection closed', 'timed out', 'timeout', 'dns', 'network error',
                                               'error sending request', 'transport error')):
        category = 'model_transport_error'
    else:
        category = 'model_unknown_error'
    return dict(category=category, http_status=status, event_types=sorted(set(event_types)),
                method='fixed-category reduction of error events/stderr; raw text discarded', raw_diagnostics_saved=False)


def retain_usage(metadata, usage):
    if not isinstance(usage, dict):
        return
    for key in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
        value = usage.get(key)
        if type(value) is int and value >= 0:
            metadata[key] = value
    if metadata['input_tokens'] is not None and metadata['output_tokens'] is not None:
        metadata['total_tokens'] = metadata['input_tokens'] + metadata['output_tokens']
