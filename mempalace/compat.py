"""
compat.py — Runtime compatibility shims for older palace data.

Applies monkey-patches at import time so they are in effect for every
mempalace operation regardless of which chromadb 0.6.x patch is installed.
"""

import pickle


def _patch_persistent_data_load():
    """
    chromadb < 0.5 serialised PersistentData via pickle as a plain dict.
    chromadb 0.6.x expects attribute access (obj.dimensionality) on the
    deserialised value, which raises AttributeError when the file on disk
    is a dict.

    This patch wraps load_from_file so that a dict payload is transparently
    promoted back to a PersistentData instance, making existing palaces
    readable without migration or repair.
    """
    try:
        from chromadb.segment.impl.vector.local_persistent_hnsw import PersistentData
    except ImportError:
        return  # chromadb not installed or path changed — skip silently

    @staticmethod  # type: ignore[misc]
    def load_from_file(filename: str) -> "PersistentData":
        with open(filename, "rb") as f:
            ret = pickle.load(f)
        if isinstance(ret, dict):
            ret = PersistentData(
                dimensionality=ret.get("dimensionality"),
                total_elements_added=ret.get("total_elements_added", 0),
                id_to_label=ret.get("id_to_label", {}),
                label_to_id=ret.get("label_to_id", {}),
                id_to_seq_id=ret.get("id_to_seq_id", {}),
            )
        return ret  # type: ignore[return-value]

    PersistentData.load_from_file = load_from_file


def apply_all():
    _patch_persistent_data_load()
