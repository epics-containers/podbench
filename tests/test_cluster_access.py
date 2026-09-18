"""The pure parts of make-sa, delete-sa and tunnel: rules, names and parsing."""

from __future__ import annotations

import base64
import json

import pytest

from podbench.__main__ import main
from podbench.access import AccessError
from podbench.agent_sa import token_expiry
from podbench.agent_sa_rules import (
    Tiers,
    account_name,
    manifest,
    parse_tiers,
    snapshot_rules,
    tier_rules,
)
from podbench.tunnel_source import parse_source, split_server


@pytest.mark.parametrize(
    ("who", "account"),
    [
        ("hgv27681", "agent-hgv27681"),
        ("Giles.Knap", "agent-giles-knap"),
        ("agent-bob", "agent-bob"),
        ("...", "agent-unknown"),
    ],
)
def test_account_name_is_an_rfc1123_label(who: str, account: str) -> None:
    assert account_name(who) == account


def test_every_tier_implies_observe() -> None:
    assert parse_tiers(["resize"]) == Tiers(observe=True, resize=True)
    assert parse_tiers(["iterate,hotfix"]).observe
    assert parse_tiers([]) == Tiers()
    assert str(parse_tiers(["all"])) == "observe,iterate,resize,hotfix"


def test_unknown_tier_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown podbench tier 'bogus'"):
        parse_tiers(["observe,bogus"])


def _grants(rules: list[dict]) -> set[tuple[str, str]]:
    return {
        (verb, resource)
        for rule in rules
        for resource in rule["resources"]
        for verb in rule["verbs"]
    }


def test_tiers_never_grant_secrets_and_resize_can_read_first() -> None:
    grants = _grants(tier_rules(parse_tiers(["all"])))
    assert not any(resource == "secrets" for _, resource in grants)
    assert {("get", "pods/resize"), ("patch", "pods/resize")} <= grants
    assert ("create", "pods/exec") in grants
    assert ("create", "pods/exec") not in _grants(tier_rules(Tiers()))


def test_snapshot_keeps_reads_and_resource_names() -> None:
    review = {
        "status": {
            "incomplete": False,
            "resourceRules": [
                {"verbs": ["*"], "apiGroups": [""], "resources": ["pods"]},
                {"verbs": ["create"], "apiGroups": [""], "resources": ["pods/exec"]},
                {
                    "verbs": ["get", "delete"],
                    "apiGroups": [""],
                    "resources": ["secrets"],
                    "resourceNames": ["mine"],
                },
            ],
        }
    }
    read = snapshot_rules(review, read_only=True)
    assert [rule["verbs"] for rule in read] == [["get", "list", "watch"], ["get"]]
    assert read[1]["resourceNames"] == ["mine"]
    assert len(snapshot_rules(review, read_only=False)) == 3


@pytest.mark.parametrize(
    "status",
    [{"incomplete": True, "resourceRules": []}, {"resourceRules": []}, {}],
)
def test_incomplete_snapshot_is_refused(status: dict) -> None:
    with pytest.raises(ValueError):
        snapshot_rules({"status": status}, read_only=True)


def test_manifest_is_namespaced_and_labelled() -> None:
    items = manifest("agent-x", "ns", [])["items"]
    assert [item["kind"] for item in items] == [
        "ServiceAccount",
        "Role",
        "RoleBinding",
    ]
    assert all(item["metadata"]["namespace"] == "ns" for item in items)
    assert all("managed-by" in str(item["metadata"]["labels"]) for item in items)


def test_token_expiry_reads_the_claim() -> None:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": 0}).encode()).rstrip(b"=")
    assert token_expiry(f"head.{claims.decode()}.sig") is not None
    assert token_expiry("not-a-jwt") is None


@pytest.mark.parametrize(
    ("spec", "host", "path"),
    [
        ("box:k8s/a.kubeconfig", "box", "k8s/a.kubeconfig"),
        ("me@box:/abs/a.kubeconfig", "me@box", "/abs/a.kubeconfig"),
        ("./k8s/a:b.kubeconfig", "", "./k8s/a:b.kubeconfig"),
        ("a.kubeconfig", "", "a.kubeconfig"),
    ],
)
def test_source_follows_scp_rules(spec: str, host: str, path: str) -> None:
    source = parse_source(spec)
    assert (source.host, source.path) == (host, path)


def test_source_needs_a_path_after_the_host() -> None:
    with pytest.raises(AccessError):
        parse_source("box:")


@pytest.mark.parametrize(
    ("server", "expected"),
    [
        ("https://api.example:6443", ("api.example", 6443)),
        ("https://api.example", ("api.example", 443)),
        ("https://[::1]:6443/prefix", ("::1", 6443)),
        ("https://[fd00::2]", ("fd00::2", 443)),
    ],
)
def test_split_server(server: str, expected: tuple[str, int]) -> None:
    assert split_server(server) == expected


def test_plain_http_is_not_tunnelled() -> None:
    with pytest.raises(AccessError, match="only https"):
        split_server("http://api.example:8080")


@pytest.mark.parametrize("verb", ["make-sa", "delete-sa", "tunnel"])
def test_verbs_have_help(verb: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([verb, "--help"]) == 0
    assert f"podbench {verb}" in capsys.readouterr().out


def test_tier_flags_and_all_are_exclusive(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("podbench.agent_sa.require", lambda *_: None)
    assert main(["make-sa", "ns", "--podbench", "--all"]) == 1
    assert "cannot be combined" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ('Error from server (NotFound): serviceaccounts "agent-bob" not found', 0),
        ("Error from server (Forbidden): cannot get resource", 1),
    ],
)
def test_delete_looks_before_it_deletes(
    stderr: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing account is reported as missing, and a namespace you cannot read
    is reported before anything is deleted."""
    from podbench import agent_sa_delete
    from podbench.kubectl import CommandResult

    monkeypatch.setattr(agent_sa_delete, "require", lambda *_: None)
    monkeypatch.setattr(
        agent_sa_delete,
        "kubectl",
        lambda *args, **_: CommandResult(tuple(args), 1, "", stderr),
    )
    monkeypatch.setattr(
        agent_sa_delete, "kubectl_out", lambda *_, **__: pytest.fail("deleted")
    )
    assert main(["delete-sa", "ns", "--user", "bob", "--yes"]) == expected
    output = capsys.readouterr()
    if expected:
        assert "another namespace" in output.err
    else:
        assert "no agent-bob in ns" in output.out
