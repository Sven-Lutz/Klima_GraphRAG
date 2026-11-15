"""One frozen generic research candidate per factor; production files untouched."""

import ast
import hashlib
import re
import types
from collections import defaultdict

from research.experiments.m4a2_segmentation_admission.prepare_controls import clean, node_path
from research.experiments.m4a_parsing_admission import diagnose as d


def contact_signature(text):
    """Two distinct contact classes; a single policy mention of email is insufficient."""
    return (
        sum(
            bool(re.search(pattern, text, re.I))
            for pattern in [r"\b(?:Telefon|Tel\.)", r"\bFax\b", r"\bE-?Mail"]
        )
        >= 2
    )


def experimental_parser():
    """Instrument a private copy of the exact current parser AST."""
    path = d.ROOT / "crawler/core/parsers/html_parser.py"
    tree = ast.parse(path.read_text())
    replacements = {"predicate": 0, "kind": 0}

    class Patch(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node.name != "flush":
                return self.generic_visit(node)
            original = node.body
            body = []
            for statement in original:
                body.append(statement)
                if isinstance(statement, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "keep" for t in statement.targets
                ):
                    body.extend(
                        ast.parse("""candidate_added = (not keep and _m4a2_fragment(
                        node, text, bool(heading_path or local_context), link_only))
keep = keep or candidate_added""").body
                    )
                    replacements["predicate"] += 1
                if (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Name)
                    and statement.value.func.id == "emit"
                ):
                    statement.value.args[1] = ast.IfExp(
                        test=ast.Name(id="candidate_added", ctx=ast.Load()),
                        body=ast.Constant(value="context_fragment"),
                        orelse=statement.value.args[1],
                    )
                    replacements["kind"] += 1
            node.body = body
            return node

    tree = Patch().visit(tree)
    d.require(replacements == {"predicate": 1, "kind": 1}, "Unexpected parser AST structure")
    ast.fix_missing_locations(tree)
    namespace = {"__name__": "_m4a2_private_html", "__file__": str(path)}
    witnesses = []

    def eligible(node, text, context, link_only):
        if (
            link_only
            or not context
            or node.name not in {"p", "li"}
            or not 8 <= len(text) < 30
            or not re.search(r"[^\W\d_]", text)
        ):
            return False
        for sibling in [node.find_previous_sibling(), node.find_next_sibling()]:
            if sibling is None or sibling.name not in {"p", "li"}:
                continue
            sibling_text = clean(sibling.get_text(" ", strip=True))
            if len(sibling_text) >= 30 or re.search(r"\d", sibling_text):
                witnesses.append(
                    {
                        "dom_path": node_path(node),
                        "sibling_dom_path": node_path(sibling),
                        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "sibling_text_sha256": hashlib.sha256(sibling_text.encode()).hexdigest(),
                    }
                )
                return True
        return False

    namespace["_m4a2_fragment"] = eligible
    exec(compile(tree, str(path) + ":private-M4A2", "exec"), namespace)
    return types.SimpleNamespace(parse_html=namespace["parse_html"], witnesses=witnesses)


def alternative(segments):
    """Return all decisions without learning from controls or altering input segments."""
    scorer, _ = d.filter_components()
    standard, _, _ = d.admission(segments)
    section, sid = {}, -1
    members = defaultdict(list)
    features = [scorer(s["text"]) for s in segments]
    for i, s in enumerate(segments):
        if s["segment_type"] == "heading":
            sid = i
        section[i] = sid
        members[sid].append(i)
    support = {}
    for group, indices in members.items():
        support[group] = (
            []
            if group == -1
            else [
                i
                for i in indices
                if not features[i][2]
                and not contact_signature(segments[i]["text"])
                and (features[i][1] > 0 or scorer(segments[i]["heading_context"] or "")[1] > 0)
            ]
        )
    accepted, decisions, representatives = set(), [], {}
    for i, s in enumerate(segments):
        text, group = s["text"], section[i]
        reason, keep = "EXCLUDE_UNRESOLVED_CONTEXT", False
        if features[i][2] or contact_signature(text):
            reason = "EXCLUDE_NEGATIVE_OR_CONTACT"
        elif i in standard:
            reason, keep = "ACCEPT_STANDARD", True
        elif support[group] and len(text) >= 8:
            if s["segment_type"] == "heading":
                reason, keep = "ACCEPT_SUPPORTED_HEADING", True
            elif (
                re.search(r"\d", text) or len(text) >= 30 or s["segment_type"] == "context_fragment"
            ):
                reason, keep = "ACCEPT_SUPPORTED_CONTENT", True
        key = (clean(text), s["heading_context"], s["page_ref"])
        alias = None
        if keep and key in representatives:
            alias = representatives[key]
            reason, keep = "EXCLUDE_SAME_SOURCE_CONTEXT_DUPLICATE", False
        elif keep:
            representatives[key] = i
        if keep:
            accepted.add(i)
        decisions.append(
            {
                "segment_index": i,
                "admitted": keep,
                "reason": reason,
                "section_heading_index": group,
                "support_segment_indices": support[group],
                "score": features[i][0],
                "hits": features[i][1],
                "negative": bool(features[i][2]),
                "contact_signature": contact_signature(text),
                "duplicate_of_segment_index": alias,
            }
        )
    return accepted, decisions
