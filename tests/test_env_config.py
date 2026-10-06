# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import pytest
from env_config import env_ids, env_id


def test_absent_ids_have_no_personal_defaults(monkeypatch):
    monkeypatch.delenv("TEST_DEPLOYMENT_IDS", raising=False)
    assert env_ids("TEST_DEPLOYMENT_IDS") == ()
    assert env_id("TEST_DEPLOYMENT_IDS") == 0


def test_ordered_ids_ignore_whitespace_and_duplicates(monkeypatch):
    monkeypatch.setenv("TEST_DEPLOYMENT_IDS", " 3, 1,3,2 ")
    assert env_ids("TEST_DEPLOYMENT_IDS") == (3, 1, 2)


@pytest.mark.parametrize("value", ["0", "-1", "1,x", "1,", "1,,2", "1.5", "１２３"])
def test_invalid_config_does_not_silently_enable_access(monkeypatch, value):
    monkeypatch.setenv("TEST_DEPLOYMENT_IDS", value)
    with pytest.raises(ValueError):
        env_ids("TEST_DEPLOYMENT_IDS")


def test_single_id_rejects_an_accidental_list(monkeypatch):
    monkeypatch.setenv("TEST_DEPLOYMENT_ID", "1,2")
    with pytest.raises(ValueError):
        env_id("TEST_DEPLOYMENT_ID")
