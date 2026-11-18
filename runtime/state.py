"""Compatibility imports for the original contract-v2 task-state interface.

Implementations live in their owning layers. Existing callers may keep importing
these names; task keys, fingerprints and retained evidence do not change.
"""
from task_primitives import (
    digest, task_key, has_output_content, read, write, git, identity, CONTRACT)
from task_capture import (
    resource, _packed_refs_safe, _GitContextGuard, _git_context, _capture_git, _CaptureContext)
from task_policy import (
    capture, make_spec)
from task_runtime import (
    Store, _pointer, _component_changes, REVIEW_CYCLES)
