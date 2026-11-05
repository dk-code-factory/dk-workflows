---
name: commit-policy
description: Define grouping and message instructions for the controlled offline commit-policy exercise.
---

Read the supplied pinned PR scope, history, repository requirements and current user constraints. Return instructions only. Do not edit files, rewrite history, publish, invoke cleanup or delegate.

When the complete PR implements and documents one outcome, request a single cohesive commit. Ask for a short descriptive imperative subject without a body unless required repository conventions demand one. For distinct outcomes, retain independently reviewable boundaries. Explain the scope assessment and return a completed structured report containing `complete:true`, `findings:[]`, `blockers:[]`, consumed source references, and a nonempty `policy_prompt` that covers grouping and messages. Missing required context must return `complete:false` and a blocker.
