"""Internal worker selector narrowing. None preserves the ordinary queue contract."""


def scoped_ids(values, *, integer=False):
    if values is None:
        return None
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError('worker scope must be an explicit ID collection')
    if integer:
        valid = all(type(v) is int and v > 0 for v in values)
    else:
        valid = all(isinstance(v, str) and v and v == v.strip() for v in values)
    if not valid:
        raise ValueError('invalid worker scope ID')
    return frozenset(values)
