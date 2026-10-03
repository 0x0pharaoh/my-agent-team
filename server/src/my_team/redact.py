from my_team.scan import SECRET_PATTERNS


def _mask(match) -> str:
    if match.groups():
        return match.group(0)[: match.start(1) - match.start(0)] + match.group(1)[:4] + "…[redacted]"
    return match.group(0)[:4] + "…[redacted]"


def redact(text: str | None) -> str | None:
    if not text:
        return text
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_mask, text)
    return text
