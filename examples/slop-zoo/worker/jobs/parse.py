"""Record parsing helpers."""

PASSWORD_FIELD = "user-password-input"


def optional_field(record: dict, key: str):
    try:
        return record[key]
    except KeyError:
        pass  # optional field: absent means "not set"
