from uuid import UUID, uuid5

SESSION_NAMESPACE = UUID("f9b9f456-2d14-4ed5-a293-8b4d83f5c777")


def stable_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        return uuid5(SESSION_NAMESPACE, value)
