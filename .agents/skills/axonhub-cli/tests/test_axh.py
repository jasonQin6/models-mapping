#!/usr/bin/env python3
"""Tests for the axh helper: bare-JSON parsing and write error detection."""

import json

import pytest
from axh import parse_bare_json, recon, RECON_QUERIES


def test_parse_bare_json_plain_object() -> None:
    assert parse_bare_json('{"a": 1}') == {"a": 1}


def test_parse_bare_json_skips_npm_notice_noise() -> None:
    out = '\n'.join([
        "npm notice npm is awesome",
        "npm notice more noise",
        '{"updateModel": {"id": "gid://axonhub/Model/1"}}',
    ])
    assert parse_bare_json(out) == {"updateModel": {"id": "gid://axonhub/Model/1"}}


def test_parse_bare_json_ignores_braces_inside_strings() -> None:
    payload = {"remark": "json inside: {\"nested\": true} stays", "n": 2}
    out = json.dumps(payload)
    assert parse_bare_json(out) == payload


def test_parse_bare_json_takes_first_balanced_block() -> None:
    out = '{"first": 1}\n{"second": 2}'
    assert parse_bare_json(out) == {"first": 1}


def test_parse_bare_json_rejects_non_json_and_unbalanced() -> None:
    with pytest.raises(ValueError):
        parse_bare_json("Error: HTTP 422: nothing parseable here")
    with pytest.raises(ValueError):
        parse_bare_json('{"open": 1')


def test_recon_queries_are_selectable_via_graphql_shapes() -> None:
    # Field names and subfield selections this deployment actually accepts:
    # modelID (not modelId), status (no enabled), subfields on modelCard /
    # associations / regex / modelId, queryChannels relay via input:{}.
    models_query = RECON_QUERIES["models"]
    assert "modelID" in models_query
    assert "status" in models_query
    assert "modelCard { cost" in models_query
    assert "channelModel { channelId modelId }" in models_query
    assert "modelId { modelId }" in models_query
    assert "regex { pattern }" in models_query
    assert 'queryChannels(input: {first: 50})' in RECON_QUERIES["channels"]
    assert "autoSyncModelPattern supportedModels" in RECON_QUERIES["channels"]
