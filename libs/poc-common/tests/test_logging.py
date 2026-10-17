import json
import logging

from poc_common.logging import (
    CORRELATION_HEADER,
    get_correlation_id,
    set_correlation_id,
    set_session_id,
    setup_logging,
)


def test_json_log_carries_service_and_correlation_ids(capsys):
    setup_logging("unit-test", "INFO", json_output=True)
    cid = set_correlation_id("abc123")
    set_session_id("sess-1")

    logging.getLogger("demo").info("hello %s", "world")

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["service"] == "unit-test"
    assert line["message"] == "hello world"
    assert line["correlation_id"] == cid == "abc123"
    assert line["session_id"] == "sess-1"
    assert line["level"] == "INFO"


def test_set_correlation_id_generates_one_when_missing():
    generated = set_correlation_id()
    assert generated and get_correlation_id() == generated


def test_header_name_is_stable():
    assert CORRELATION_HEADER == "X-Correlation-ID"
