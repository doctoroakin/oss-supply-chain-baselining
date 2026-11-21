class ShardNotFoundError(FileNotFoundError):
    """Raised when a repo's shard directory or expected CSV file is missing."""
    pass


class SchemaMismatchError(ValueError):
    """Raised when a loaded CSV is missing expected columns."""
    pass
