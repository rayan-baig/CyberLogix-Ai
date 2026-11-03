"""The API reference at /developers: the OpenAPI schema, as plain HTML.

/docs and /redoc draw the same schema with Swagger UI and ReDoc, which
are fetched from a CDN at view time and need a looser content security
policy than the rest of the product. Where the CDN is blocked (a strict
office network, an air-gapped site, a sandbox) those pages are blank.
This one is rendered here, from `app.openapi()`, with no script at all,
under the policy every other page has: the routes are read from the
router, so a new endpoint appears without anybody writing it up.

What it leaves out on purpose: routes only the platform operator can call
(the ones behind X-CyberLogix-Admin or X-CyberLogix-Provisioning and no
customer credential). They are counted at the bottom, not listed. Nothing
here prints a credential's value; examples name the header and show a
placeholder. tests/test_api_reference.py holds both.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# How each credential is presented, by the header that carries it.
# Lower-cased, as HTTP header names are compared.
CREDENTIALS: Dict[str, Tuple[str, str]] = {
    "authorization": ("Signed-in session", "Authorization: Bearer $TOKEN"),
    "x-cyberlogix-key": ("Business API key", "X-CyberLogix-Key: $KEY"),
    "x-cyberlogix-sensor-key": ("Sensor key", "X-CyberLogix-Sensor-Key: $SENSOR_KEY"),
    "x-cyberlogix-partner": ("Partner key", "X-CyberLogix-Partner: $PARTNER_KEY"),
    "x-signature": ("Signed webhook", "X-Signature: $HMAC_OF_THE_BODY"),
}

# Which credential the example command uses, when several will do.
_CURL_PREFERENCE = ("x-cyberlogix-key", "authorization", "x-cyberlogix-sensor-key",
                    "x-cyberlogix-partner")

# The platform operator's own credentials. A route that takes only these
# is internal and is not listed.
OPERATOR_HEADERS = ("x-cyberlogix-admin", "x-cyberlogix-provisioning")

# Example values that must never look like a real secret.
_SECRET_NAME = re.compile(r"key|token|secret|password|signature", re.I)

_METHOD_ORDER = {"get": 0, "post": 1, "put": 2, "patch": 3, "delete": 4}


def _headers(op: Dict[str, Any]) -> List[str]:
    return sorted({p["name"].lower() for p in op.get("parameters", [])
                   if p.get("in") == "header"})


def operator_only(op: Dict[str, Any]) -> bool:
    """Callable only with the platform operator's credential."""
    headers = _headers(op)
    return (any(h in OPERATOR_HEADERS for h in headers)
            and not any(h in CREDENTIALS for h in headers))


def operations(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every operation in the schema, with what this page needs of it."""
    out = []
    for path, ops in schema.get("paths", {}).items():
        for method, op in ops.items():
            if method not in _METHOD_ORDER:
                continue
            tags = op.get("tags") or ["Other"]
            out.append({"method": method, "path": path, "op": op,
                        "group": tags[0], "internal": operator_only(op)})
    return out


def public_operations(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [o for o in operations(schema) if not o["internal"]]


# --- the schema, read --------------------------------------------------------


def _resolve(schema: Optional[Dict[str, Any]], components: Dict[str, Any]) -> Dict[str, Any]:
    seen = 0
    while schema and "$ref" in schema and seen < 20:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = components.get(name, {})
        seen += 1
    return schema or {}


def _nullable_inner(schema: Dict[str, Any], components: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Optional[X] is anyOf [X, null]: X, and whether null is allowed."""
    options = schema.get("anyOf") or schema.get("oneOf")
    if not options:
        return _resolve(schema, components), False
    real = [o for o in options if o.get("type") != "null"]
    nullable = len(real) != len(options)
    if len(real) == 1:
        return _resolve(real[0], components), nullable
    return schema, nullable


def type_name(schema: Dict[str, Any], components: Dict[str, Any]) -> str:
    """A short, readable type: string, integer, array of string, Contact."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        real = [o for o in options if o.get("type") != "null"]
        parts = [type_name(o, components) for o in real]
        return (" or ".join(dict.fromkeys(parts)) or "null") + (
            " or null" if len(real) != len(options) and real else "")
    if "enum" in schema:
        return "one of " + ", ".join(json.dumps(v) for v in schema["enum"])
    kind = schema.get("type", "any")
    if kind == "array":
        return "array of " + type_name(schema.get("items", {}), components)
    if schema.get("format") in ("email", "date-time", "date", "uri"):
        return f"{kind} ({schema['format']})"
    return kind


def _constraints(schema: Dict[str, Any]) -> str:
    bits = []
    for key, word in (("minimum", "at least"), ("exclusiveMinimum", "more than"),
                      ("maximum", "at most"), ("exclusiveMaximum", "less than"),
                      ("minLength", "length at least"), ("maxLength", "length at most")):
        if key in schema:
            bits.append(f"{word} {schema[key]:g}" if isinstance(schema[key], float)
                        else f"{word} {schema[key]}")
    if "pattern" in schema:
        bits.append(f"matches {schema['pattern']}")
    if "default" in schema and schema["default"] is not None:
        bits.append(f"default {json.dumps(schema['default'])}")
    return "; ".join(bits)


def example(schema: Dict[str, Any], components: Dict[str, Any],
            name: str = "", depth: int = 0) -> Any:
    """A made-up value of the right shape, never one that looks like a secret."""
    if depth > 6:
        return None
    schema, _ = _nullable_inner(_resolve(schema, components), components)
    if name and _SECRET_NAME.search(name) and schema.get("type") in (None, "string"):
        return f"<your {name.replace('_', ' ')}>"
    for key in ("example", "default"):
        if schema.get(key) is not None:
            return schema[key]
    if schema.get("examples"):
        return schema["examples"][0]
    if "enum" in schema:
        return schema["enum"][0]
    if schema.get("anyOf") or schema.get("oneOf"):
        return example((schema.get("anyOf") or schema.get("oneOf"))[0],
                       components, name, depth + 1)
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {key: example(sub, components, key, depth + 1) for key, sub in props.items()}
    if kind == "array":
        return [example(schema.get("items", {}), components, name, depth + 1)]
    if kind == "integer":
        return int(schema.get("minimum", 1) or 1)
    if kind == "number":
        return float(schema.get("minimum", 1.0) or 1.0)
    if kind == "boolean":
        return True
    fmt = schema.get("format", "")
    if fmt == "email" or name.endswith("email"):
        return "name@example.com"
    if fmt == "date-time":
        return "2026-01-01T09:00:00Z"
    if fmt == "date":
        return "2026-01-01"
    if "phone" in name:
        return "+15555550100"
    return "string"


def _first_paragraph(text: str) -> str:
    return (text or "").strip().split("\n\n", 1)[0].replace("\n", " ").strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --- the page ----------------------------------------------------------------


def _auth(op: Dict[str, Any]) -> str:
    e = html.escape
    headers = _headers(op)
    known = [h for h in headers if h in CREDENTIALS]
    if not known:
        return "<span class=\"ref-none\">None. Public.</span>"
    parts = [f"{e(CREDENTIALS[h][0])} (<code>{e(CREDENTIALS[h][1])}</code>)" for h in known]
    line = " or ".join(parts)
    if any(h in OPERATOR_HEADERS for h in headers):
        line += ". The platform operator can call it too."
    return line


def _param_rows(op: Dict[str, Any], components: Dict[str, Any]) -> str:
    e = html.escape
    rows = []
    for param in op.get("parameters", []):
        if param.get("in") == "header":
            continue  # a credential, shown under Auth
        schema = param.get("schema", {})
        rows.append(
            f"<tr><td><code>{e(param['name'])}</code></td><td>{e(param['in'])}</td>"
            f"<td>{'yes' if param.get('required') else 'no'}</td>"
            f"<td>{e(type_name(schema, components))}</td>"
            f"<td>{e(_constraints(_nullable_inner(schema, components)[0]))}</td></tr>")
    if not rows:
        return ""
    return ("<table class=\"ref-table\"><thead><tr><th>Parameter</th><th>In</th>"
            "<th>Required</th><th>Type</th><th>Limits</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def _body(op: Dict[str, Any], components: Dict[str, Any]) -> str:
    e = html.escape
    content = (op.get("requestBody") or {}).get("content", {})
    if not content:
        return ""
    media, spec = next(iter(content.items()))
    raw = spec.get("schema", {})
    schema = _resolve(raw, components)
    name = raw.get("$ref", "").rsplit("/", 1)[-1]
    required = set(schema.get("required", []))
    rows = "".join(
        f"<tr><td><code>{e(field)}</code></td>"
        f"<td>{'yes' if field in required else 'no'}</td>"
        f"<td>{e(type_name(sub, components))}</td>"
        f"<td>{e(_constraints(_nullable_inner(sub, components)[0]))}</td></tr>"
        for field, sub in schema.get("properties", {}).items())
    table = ("<table class=\"ref-table\"><thead><tr><th>Field</th><th>Required</th>"
             f"<th>Type</th><th>Limits</th></tr></thead><tbody>{rows}</tbody></table>"
             if rows else "")
    sample = json.dumps(example(raw, components), indent=2)
    title = f"Body: <code>{e(media)}</code>" + (f", {e(name)}" if name else "")
    return (f"<p class=\"ref-label\">{title}</p>{table}"
            f"<p class=\"ref-label\">Example body</p><pre class=\"ref-pre\">{e(sample)}</pre>")


def _responses(op: Dict[str, Any]) -> str:
    e = html.escape
    codes = [f"<code>{e(code)}</code> {e(r.get('description', ''))}"
             for code, r in op.get("responses", {}).items()]
    return "<p class=\"ref-small\">Answers: " + "; ".join(codes) + "</p>" if codes else ""


def _curl(item: Dict[str, Any], base: str) -> str:
    op = item["op"]
    headers = _headers(op)
    parts = [f"curl -X {item['method'].upper()} '{base}{item['path']}'"]
    for header in _CURL_PREFERENCE:
        if header in headers:
            parts.append(f"  -H '{CREDENTIALS[header][1]}'")
            break
    if op.get("requestBody"):
        parts.append("  -H 'Content-Type: application/json' -d @body.json")
    return " \\\n".join(parts)


def render(schema: Dict[str, Any], base: str) -> Dict[str, str]:
    """The page's markup: a contents list and every group of endpoints."""
    e = html.escape
    components = schema.get("components", {}).get("schemas", {})
    items = operations(schema)
    shown = [i for i in items if not i["internal"]]
    hidden = len(items) - len(shown)

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in sorted(shown, key=lambda i: (i["group"].lower(), i["path"],
                                             _METHOD_ORDER[i["method"]])):
        groups.setdefault(item["group"], []).append(item)

    toc = "".join(
        f"<li><a href=\"#{_slug(group)}\">{e(group)}</a> "
        f"<span class=\"ref-count\">{len(ops)}</span></li>"
        for group, ops in groups.items())

    sections = []
    for group, ops in groups.items():
        blocks = []
        for item in ops:
            op = item["op"]
            anchor = _slug(f"{item['method']} {item['path']}")
            description = _first_paragraph(op.get("description", ""))
            blocks.append(
                f"<section class=\"ref-op\" id=\"{anchor}\" "
                f"data-route=\"{e(item['method'].upper())} {e(item['path'])}\">"
                f"<h3><span class=\"ref-method ref-{item['method']}\">"
                f"{e(item['method'].upper())}</span> <code>{e(item['path'])}</code></h3>"
                f"<p class=\"ref-summary\">{e(op.get('summary', ''))}"
                f"{(' &mdash; ' + e(description)) if description else ''}</p>"
                f"<p class=\"ref-auth\"><b>Auth:</b> {_auth(op)}</p>"
                f"{_param_rows(op, components)}{_body(op, components)}"
                f"<pre class=\"ref-pre ref-curl\">{e(_curl(item, base))}</pre>"
                f"{_responses(op)}</section>")
        sections.append(
            f"<h2 id=\"{_slug(group)}\">{e(group)}</h2>{''.join(blocks)}")

    note = (f"<p class=\"ref-small\">{hidden} more route"
            f"{'' if hidden == 1 else 's'} can be called only by the platform "
            "operator, with its own key, and are not listed here.</p>"
            if hidden else "")
    return {
        "%%TOC%%": toc,
        "%%REFERENCE%%": "".join(sections),
        "%%HIDDEN_NOTE%%": note,
        "%%ROUTE_COUNT%%": str(len(shown)),
    }
