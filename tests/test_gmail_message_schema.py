from src.schema import CANONICAL_SCHEMA, GMAIL_MESSAGES_HEADERS


def test_gmail_messages_is_part_of_canonical_schema():
    assert CANONICAL_SCHEMA["Gmail_Messages"].headers == GMAIL_MESSAGES_HEADERS
    assert GMAIL_MESSAGES_HEADERS == [
        "message_id",
        "thread_id",
        "subject",
        "sender",
        "received_at",
        "status",
        "attempt_count",
        "alerts_parsed",
        "jobs_accepted",
        "jobs_rejected",
        "error_message",
        "first_processed_at",
        "last_processed_at",
    ]
